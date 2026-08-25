#!/usr/bin/env python3
"""Deterministic PII/secret hygiene scanner for the eval corpora (APPSEC-3971).

evals/README.md mandates that everything committed here is sanitized —
customer names, account IDs, emails, tickets, credentials all removed —
because this repo ships to customers. This script turns that mandate into
a blocking CI check.

Design constraints:
  - stdlib only, no network, no LLM. Same input -> same output, always.
  - Opt-out coverage: scans EVERY file under the target directories
    (default: evals/ plus each skills/<name>/evals/) except this
    script, the eval runner, the two hygiene sidecars, and a fixed list
    of never-committed cache/local-state directories. New file types —
    and hidden files, which are exactly as public as any other
    committed file — are covered by default rather than silently
    exempt.
  - Names are scanned too: the corpus-relative path of every file is
    run through the same rules and denylist, so a fixture named after a
    customer, a ticket, or an account UUID cannot pass a gate that only
    reads contents.
  - Fail closed on encoding: files are decoded strictly (UTF-8, or
    UTF-16/32 via BOM sniff). A file that does not decode cleanly or
    contains NUL bytes after decoding cannot be verified and is a
    configuration error (exit 2) — never treated as clean.
  - Fail closed on rule config: a sidecar that is missing, unreadable,
    or not valid UTF-8 is a configuration error even when it is the
    built-in default. Loading a default as "empty" would let deleting
    hygiene-denylist.sha256 turn customer-name enforcement off with a
    green build.
  - JSON-aware: for *.json / *.jsonl the decoded string values are ALSO
    scanned, so \\uXXXX escaping cannot hide a match from the raw-text
    pass (and non-ASCII names survive json.dumps(ensure_ascii=True)).
  - Regex denylist for pattern-shaped leaks (emails, API keys, tokens,
    IPs, ticket IDs, UUIDs, tenant-bearing console URLs).
  - Hashed customer-name denylist (``hygiene-denylist.sha256``):
    forbidden tokens are stored as SHA-256 hashes. Each line is NFKC-
    normalized and lowercased BEFORE tokenizing (so a canonically
    decomposed spelling cannot split into runs that never rejoin), then
    word tokens, adjacent 2-/3-token joins, and digit-stripped variants
    are hashed and compared: hyphen/dot/space-separated and
    year-suffixed spellings of a denylisted name still trip. NOTE: this
    is grep-resistance, not secrecy — see the denylist file header for
    the threat-model limit.
  - Sidecar allowlist (``hygiene-allowlist.txt``): one regex per line;
    a finding is suppressed when an allowlist match on the same line
    fully covers the finding's span. Sidecar because JSONL has no
    comments, so inline suppression markers are impossible.

Exit codes:
  0  clean
  1  one or more findings
  2  configuration error (bad allowlist regex, malformed denylist hash,
     missing/unreadable sidecar, missing given path, undecodable file).
     Config errors take precedence over findings: exit 2 means the
     corpus could not be fully verified.

Output: one line per finding. Under GitHub Actions (``GITHUB_ACTIONS``
env var set) findings are emitted as ``::error file=...,line=N::...``
workflow annotations so they attach to the diff in the PR view.

See evals/HYGIENE.md for the operator guide (fixing hits, extending the
denylist/allowlist, known residual limits).
"""

from __future__ import annotations

import argparse
import codecs
import hashlib
import json
import os
import re
import sys
import unicodedata
from pathlib import Path
from typing import Iterator, NamedTuple

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent

# Files that are never scanned: the rule config and the scripts that
# implement the gate. Deliberately NOT here: run_trigger_evals.py --out
# artifacts (trigger-results.json and friends). Those are gitignored
# instead — a name-based exemption would leave a file that someone can
# still `git add -f` permanently unscanned. Delete a local sweep
# artifact before running the gate rather than allowlisting its content.
SKIP_FILENAMES = {
    "check_eval_hygiene.py",
    "run_trigger_evals.py",
    "hygiene-allowlist.txt",
    "hygiene-denylist.sha256",
    ".DS_Store",  # Finder metadata: binary, gitignored, never committed
}

# Never-committed cache / local-state directories, all covered by
# .gitignore. Matched by NAME, not by a blanket "starts with a dot"
# rule: a committed .fixture.jsonl is exactly as public as any other
# file in the tree, so hidden files must not be able to opt themselves
# out of the gate. Keep this list in sync with .gitignore and with the
# exemption list in HYGIENE.md.
SKIP_DIRNAMES = {
    "__pycache__",
    ".git",
    ".venv",
    "venv",
    "env",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".idea",
    ".vscode",
    ".claude",
    ".skillconfig",
    ".build-cache",
    "node_modules",
}

# Files whose decoded JSON string values get a second scan pass.
JSON_EXTENSIONS = {".json", ".jsonl"}

DEFAULT_ALLOWLIST = SCRIPT_DIR / "hygiene-allowlist.txt"
DEFAULT_DENYLIST = SCRIPT_DIR / "hygiene-denylist.sha256"

# Ticket-shaped tokens that are standards/products, not internal tickets.
# Baked into the rule (rather than the allowlist) because they are an
# open-ended class the corpus will keep hitting: GPU-adjacent prose is
# full of them. Case-insensitive, must be the whole project-key prefix.
_BENIGN_TICKET_PREFIXES = (
    "IEEE|FIPS|SOC|PCI|NIST|TLS|SSL|ISO|RFC|SHA|UTF|MD|AES|RSA|CVE|GPT"
    "|COVID|HTTPS|HTTP|TCP|UDP|DNS|ANSI|POSIX|OAUTH|DIN|EN"
)

# --------------------------------------------------------------------------
# Regex rules. Each entry: (rule_name, compiled_regex).
#
# False-positive constraints (see HYGIENE.md): the corpus legitimately
# contains CoreWeave availability-zone names (US-EAST-04A), instance
# types (gd-8xh100ib-i128), and GPU/standard names (A100-80, GPT-4,
# IEEE-754). The ticket-ID rule therefore (a) requires the match to be a
# standalone token — not preceded or followed by another hyphen-joined
# segment, (b) requires a letters-only project key, so A100-80 can never
# match, and (c) excludes the benign prefix class above.
# hygiene-allowlist.txt carries zone-name entries as defense in depth.
# --------------------------------------------------------------------------
RULES: list[tuple[str, re.Pattern[str]]] = [
    (
        "email-address",
        re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\.[A-Za-z]{2,}\b"),
    ),
    (
        # Bare internal handles ("ping @coreweave.com folks"). Anchored so a
        # full email is reported once, by the email-address rule only.
        "internal-domain-handle",
        re.compile(r"(?<![A-Za-z0-9._%+-])@(?:coreweave|wandb)\.com\b", re.IGNORECASE),
    ),
    (
        "anthropic-api-key",
        re.compile(r"\bsk-ant-[A-Za-z0-9_-]{2,}"),
    ),
    (
        "aws-access-key-id",
        re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    ),
    (
        # ghp_ (personal), gho_ (oauth), ghu_/ghs_/ghr_ (app) tokens.
        "github-token",
        re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    ),
    (
        # All xox?- token families (xoxb, xoxp, xoxa, xoxr, xoxs, xoxc,
        # xoxd, xoxe, ...): one letter class, so new families are covered.
        "slack-token",
        re.compile(r"\bxox[a-z]-[A-Za-z0-9-]{10,}"),
    ),
    (
        # Base64url-encoded '{"' is 'eyJ'. Require at least two
        # dot-separated segments (one separator) so prose mentioning the
        # bare prefix doesn't trip, and reach at most three. Two
        # segments is deliberate, not a typo: an alg=none token is
        # `header.payload.` with an EMPTY signature, and a log excerpt
        # often keeps `header.payload` and drops the rest. Requiring two
        # separators would miss both while only buying immunity to
        # base64-JSON-looking-filename false positives, which a
        # sanitized prose corpus does not produce.
        "jwt",
        re.compile(r"\beyJ[A-Za-z0-9_=-]{8,}(?:\.[A-Za-z0-9_=-]{4,}){1,2}\b"),
    ),
    (
        "pem-header",
        re.compile(r"-----BEGIN [A-Z][A-Z0-9 ]*-----"),
    ),
    (
        # Dotted quads, leading zeros allowed (0*), since 192.168.001.007
        # is still an IP. Lookarounds: don't start mid-word/mid-number,
        # don't continue into a longer dotted run — but DO allow a
        # sentence-final period ("... at 10.0.0.1.").
        "ipv4-address",
        re.compile(
            r"(?<![\w.])"
            r"(?:0*(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)\.){3}"
            r"0*(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)"
            r"(?!\w|\.\d)"
        ),
    ),
    (
        # Jira-style ticket IDs (project key + number), case-insensitive
        # so a lowercased paste ("appsec-1234") is still caught. The key
        # is letters-only: real Jira keys are, and it structurally
        # excludes GPU-ish tokens like A100-80. Standalone-token
        # lookarounds exclude zone names (US-EAST-04A) and instance types.
        "ticket-id",
        re.compile(
            r"(?<![\w-])"
            r"(?!(?:" + _BENIGN_TICKET_PREFIXES + r")-)"
            r"[A-Za-z]{2,10}-\d{1,6}(?![\w-])",
            re.IGNORECASE,
        ),
    ),
    (
        "uuid",
        re.compile(
            r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}"
            r"-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
        ),
    ),
    (
        # CoreWeave console/cloud URLs that carry an org/account/tenant
        # identifier in a path segment or query parameter (snake_case and
        # camelCase param spellings both covered via _? + IGNORECASE).
        #
        # The host is dot-delimited and terminated: subdomain labels are
        # matched as whole labels ((?:label\.)*), so `fakecoreweave.com`
        # is not a CoreWeave host, and the lookahead requires a port,
        # path, query, or fragment right after the TLD, so a lookalike
        # such as `coreweave.com.evil.example/orgs/x` cannot masquerade
        # as one either. Both used to produce a blocking PII finding for
        # a third-party URL.
        "console-url-with-org-id",
        re.compile(
            r"https?://(?:[A-Za-z0-9-]+\.)*coreweave\.com(?=[:/?#])"
            r"[^\s\"'<>]*"
            r"(?:/(?:orgs?|organizations?|accounts?|tenants?)/[A-Za-z0-9_-]{2,}"
            r"|[?&](?:orgs?|org_?id|organizations?(?:_?id)?"
            r"|accounts?(?:_?id)?|tenants?(?:_?id)?)=[A-Za-z0-9_-]{2,})",
            re.IGNORECASE,
        ),
    ),
]

# Word tokens hashed against the customer-name denylist. Unicode-aware
# ([^\W_] = letters+digits) so accented/non-Latin names decoded from JSON
# can match. Candidates per line:
#   - each word run (and its digit-stripped variant, so acmecorp2024 and
#     acme2corp still hash to acmecorp),
#   - each adjacent 2- and 3-run join (so "acme corp", "acme-corp",
#     "api.acme-corp.coreweave.net" all yield acmecorp),
#   - each separator-joined compound verbatim (for entries that were
#     hashed with separators kept, against guidance).
_WORD_RUN = re.compile(r"[^\W_]+", re.UNICODE)
_COMPOUND_TOKEN = re.compile(r"[^\W_]+(?:[._\-'][^\W_]+)+", re.UNICODE)
_DIGIT_RUN = re.compile(r"\d+")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")

# Path separators, for splitting a corpus-relative name into segments
# before the name-level scan (see _name_candidates).
_PATH_SEPARATOR = re.compile(r"[/\\]")


class ConfigError(Exception):
    pass


def _iter_config_lines(path: Path, kind: str) -> Iterator[tuple[int, str]]:
    """Yield (lineno, stripped line) skipping blanks/#-comments.

    Fails closed, and identically for a default and an explicitly given
    path: a sidecar that is missing, unreadable, or not valid UTF-8 is a
    ConfigError. Treating a missing DEFAULT as "empty config" is the
    dangerous case — deleting hygiene-denylist.sha256 would silently
    turn customer-name enforcement off and still exit 0 — and an
    unreadable or non-UTF-8 sidecar used to escape as an unhandled
    UnicodeDecodeError/PermissionError traceback instead of the
    documented exit 2.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise ConfigError(f"{kind} file not found: {path}")
    except UnicodeDecodeError as exc:
        raise ConfigError(f"{kind} file is not valid UTF-8: {path} ({exc})")
    except OSError as exc:
        raise ConfigError(f"{kind} file cannot be read: {path} ({exc})")
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if line and not line.startswith("#"):
            yield lineno, line


def load_allowlist(path: Path) -> list[re.Pattern[str]]:
    patterns = []
    for lineno, line in _iter_config_lines(path, "allowlist"):
        try:
            patterns.append(re.compile(line))
        except re.error as exc:
            raise ConfigError(f"{path}:{lineno}: invalid allowlist regex {line!r}: {exc}")
    return patterns


def load_denylist(path: Path) -> set[str]:
    hashes = set()
    for lineno, line in _iter_config_lines(path, "denylist"):
        if not _HEX64.fullmatch(line):
            raise ConfigError(
                f"{path}:{lineno}: denylist entries must be one lowercase "
                f"64-char hex SHA-256 per line, got {line!r}"
            )
        hashes.add(line)
    return hashes


def _sha256(token: str) -> str:
    # NFKC-normalize so composed/decomposed Unicode spellings of the same
    # name hash identically. ASCII is unaffected, so hashes produced by
    # the documented `printf | shasum` recipe still match.
    return hashlib.sha256(unicodedata.normalize("NFKC", token).encode("utf-8")).hexdigest()


def redact(text: str) -> str:
    """Show just enough of a match to locate it without re-leaking it.

    Never reveal more than a third of the match (floor 1 char), and for
    email-shaped matches never reach the '@' — at most half of the local
    part is shown, and none of the domain.
    """
    keep = min(6, max(1, len(text) // 3))
    at = text.find("@")
    if at > 0:  # at == 0 is a bare domain handle; the domain is the rule
        keep = max(1, min(keep, at // 2))
    masked = max(1, min(len(text) - keep, 12))
    return text[:keep] + "*" * masked


class Finding(NamedTuple):
    path: Path
    line: int
    col: int
    rule: str
    message: str
    span: tuple[int, int]
    key: str  # dedup key: matched text (regex rules) or digest (denylist)


def _denylist_candidates(lower_line: str) -> Iterator[tuple[tuple[int, int], tuple[str, ...]]]:
    """Yield (span, candidate tokens to hash) for the denylist check."""

    def variants(token: str) -> tuple[str, ...]:
        stripped = _DIGIT_RUN.sub("", token)
        return (token,) if stripped in ("", token) else (token, stripped)

    runs = list(_WORD_RUN.finditer(lower_line))
    for m in runs:
        yield m.span(), variants(m.group(0))
    for n in (2, 3):
        for i in range(len(runs) - n + 1):
            window = runs[i : i + n]
            join = "".join(w.group(0) for w in window)
            yield (window[0].start(), window[-1].end()), variants(join)
    for m in _COMPOUND_TOKEN.finditer(lower_line):
        yield m.span(), (m.group(0),)


def scan_line(path: Path, lineno: int, line: str, denylist: set[str]) -> list[Finding]:
    findings: list[Finding] = []
    for rule, pattern in RULES:
        for m in pattern.finditer(line):
            findings.append(
                Finding(path, lineno, m.start() + 1, rule,
                        f"matched: {redact(m.group(0))}", (m.start(), m.end()),
                        m.group(0))
            )
    if denylist:
        # NFKC-normalize the WHOLE LINE before tokenizing, then lowercase.
        # Normalizing per token (inside _sha256) is too late: a canonically
        # decomposed name ("cafe" + U+0301) tokenizes as the single run
        # "cafe", because a combining mark is not a word character, so the
        # accented letter is gone before any hash is taken and the
        # precomposed hash can never match. Normalizing first also folds
        # compatibility spellings (fullwidth, ligatures) onto ASCII.
        #
        # Spans are computed on that normalized+lowercased copy. Both steps
        # can change string length for a handful of code points (U+0130,
        # ligatures), which would shift columns; spans are clamped to the
        # original line so a report can never point past the end. Columns
        # may be off by the length delta in that rare case, and an
        # allowlist entry may fail to cover a shifted span — which errs
        # toward reporting, not toward silence.
        lower = unicodedata.normalize("NFKC", line).lower()
        seen_spans: set[tuple[int, int]] = set()
        for span, tokens in _denylist_candidates(lower):
            if span in seen_spans:
                continue
            for token in tokens:
                digest = _sha256(token)
                if digest in denylist:
                    seen_spans.add(span)
                    span = (min(span[0], len(line)), min(span[1], len(line)))
                    findings.append(
                        Finding(path, lineno, span[0] + 1, "customer-denylist",
                                f"denylisted token (sha256 {digest[:12]}…, value withheld)",
                                span, digest)
                    )
                    break
    return findings


def is_allowed(line: str, finding: Finding, allowlist: list[re.Pattern[str]]) -> bool:
    """True when an allowlist match on this line fully covers the finding."""
    start, end = finding.span
    for pattern in allowlist:
        for m in pattern.finditer(line):
            if m.start() <= start and m.end() >= end:
                return True
    return False


def _iter_json_strings(obj: object) -> Iterator[str]:
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for key, value in obj.items():
            yield from _iter_json_strings(key)
            yield from _iter_json_strings(value)
    elif isinstance(obj, list):
        for value in obj:
            yield from _iter_json_strings(value)


def _read_text_strict(path: Path) -> str:
    """Decode a file, failing closed on anything that can't be verified.

    BOM-sniffs UTF-32/UTF-16/UTF-8; otherwise strict UTF-8. A decode
    error, or NUL bytes surviving the decode (binary content, or BOM-less
    UTF-16 masquerading as UTF-8), is a ConfigError — a file the scanner
    cannot read is never reported clean.
    """
    raw = path.read_bytes()
    try:
        if raw.startswith((codecs.BOM_UTF32_LE, codecs.BOM_UTF32_BE)):
            text = raw.decode("utf-32")
        elif raw.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
            text = raw.decode("utf-16")
        elif raw.startswith(codecs.BOM_UTF8):
            text = raw.decode("utf-8-sig")
        else:
            text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ConfigError(
            f"{path}: cannot decode ({exc}). The corpus must be UTF-8 "
            "(or BOM-marked UTF-16/32); fix the encoding or remove the file."
        )
    if "\x00" in text:
        raise ConfigError(
            f"{path}: NUL bytes after decode (binary file, or BOM-less "
            "UTF-16). The scanner cannot verify it; fix the encoding or "
            "remove the file."
        )
    return text


def _scan_decoded(path: Path, lineno: int, doc: object, denylist: set[str],
                  allowlist: list[re.Pattern[str]], seen_keys: set[tuple[str, str]],
                  note: str) -> list[Finding]:
    out: list[Finding] = []
    seen = set(seen_keys)
    for value in _iter_json_strings(doc):
        for finding in scan_line(path, lineno, value, denylist):
            key = (finding.rule, finding.key)
            if key in seen:
                continue  # already reported by the raw-text pass
            if is_allowed(value, finding, allowlist):
                continue  # suppressed here, but do NOT claim it as seen
            seen.add(key)
            out.append(finding._replace(message=finding.message + note))
    return out


def scan_file(path: Path, denylist: set[str], allowlist: list[re.Pattern[str]]) -> list[Finding]:
    try:
        text = _read_text_strict(path)
    except OSError as exc:
        raise ConfigError(f"{path}: cannot read ({exc})")

    findings: list[Finding] = []
    keys_by_line: dict[int, set[tuple[str, str]]] = {}
    lines = text.splitlines()

    for lineno, line in enumerate(lines, 1):
        for finding in scan_line(path, lineno, line, denylist):
            if is_allowed(line, finding, allowlist):
                # Suppressed, and therefore NOT recorded as already
                # reported. An allowlist match is span- and line-specific
                # while a dedup key is just the value, so recording a
                # suppressed hit would let one allowlisted occurrence
                # silently cover a \uXXXX-escaped occurrence somewhere
                # else in the same document that nothing allows.
                continue
            keys_by_line.setdefault(lineno, set()).add((finding.rule, finding.key))
            findings.append(finding)

    # Second pass for JSON-structured files: scan the DECODED string
    # values, so \uXXXX escapes can't hide a match from the raw pass.
    # Findings already seen raw on the same line/document are deduped by
    # (rule, matched text). Parse failures fall back to raw-only.
    suffix = path.suffix.lower()
    if suffix == ".jsonl":
        for lineno, line in enumerate(lines, 1):
            if not line.strip():
                continue
            try:
                doc = json.loads(line)
            except ValueError:
                continue
            findings.extend(
                _scan_decoded(path, lineno, doc, denylist, allowlist,
                              keys_by_line.get(lineno, set()),
                              " (in decoded JSON value)")
            )
    elif suffix == ".json":
        try:
            doc = json.loads(text)
        except ValueError:
            doc = None
        if doc is not None:
            seen = set().union(*keys_by_line.values()) if keys_by_line else set()
            findings.extend(
                _scan_decoded(path, 1, doc, denylist, allowlist, seen,
                              " (in decoded JSON document; reported at line 1)")
            )
    return findings


def _name_candidates(rel: str) -> Iterator[str]:
    """The path itself, then each adjacent hyphen-delimited chunk pair.

    The ticket-id rule deliberately matches only a STANDALONE
    ``KEY-<number>`` token, so that zone names (US-EAST-04A) and
    instance types (gd-8xh100ib-i128) structurally cannot trip it. The
    same lookarounds mean a ticket ID fused into a longer file name —
    ``<KEY>-<number>-repro.jsonl`` — never matches the path as a whole.
    Re-offering each adjacent chunk pair as its own candidate closes
    that without weakening the rule: the benign compounds are still
    excluded pair by pair, by the benign-prefix class or by the
    letters-only project key.
    """
    yield rel
    for segment in _PATH_SEPARATOR.split(rel):
        chunks = segment.split("-")
        for first, second in zip(chunks, chunks[1:]):
            pair = f"{first}-{second}"
            if pair != segment:
                yield pair


def scan_path_name(path: Path, rel: str, denylist: set[str],
                   allowlist: list[re.Pattern[str]]) -> list[Finding]:
    """Scan a file's own name/path text with the same rules.

    A corpus file named after a ticket, an email address, an account
    UUID, or a denylisted customer publishes that identifier in the
    repo's tree listing exactly as effectively as its contents would —
    and the content pass never sees a file name, so this gate used to
    report such a tree clean.

    ``rel`` is the path relative to the scanned target, so the result
    never depends on the caller's working directory (a home directory or
    checkout path is not corpus content). Findings are reported at line
    1, column 1: the leak is in the name, not at some offset inside the
    file. Duplicate values across candidates collapse to one finding.
    """
    out: list[Finding] = []
    seen: set[tuple[str, str]] = set()
    for candidate in _name_candidates(rel):
        for finding in scan_line(path, 1, candidate, denylist):
            key = (finding.rule, finding.key)
            if key in seen or is_allowed(candidate, finding, allowlist):
                continue
            seen.add(key)
            out.append(finding._replace(
                col=1,
                message=finding.message + f" (in the file NAME {rel!r}, not its contents)",
            ))
    return out


def default_targets() -> list[Path]:
    """evals/ itself plus every skills/<name>/evals/ corpus directory."""
    targets = [SCRIPT_DIR]
    skills_dir = REPO_ROOT / "skills"
    if skills_dir.is_dir():
        targets.extend(sorted(p for p in skills_dir.glob("*/evals") if p.is_dir()))
    return targets


def iter_target_files(paths: list[Path]) -> list[tuple[Path, str]]:
    """Return (path, target-relative name) for every file to scan.

    The second element is the text the name-level rules run on. It is
    relative to the target that produced it, so it is identical on every
    machine. Only SKIP_FILENAMES and SKIP_DIRNAMES are excluded —
    hidden files are scanned, because a committed one is as public as
    any other file in the tree.
    """
    files: list[tuple[Path, str]] = []
    for path in paths:
        if path.is_file():
            if path.name in SKIP_FILENAMES:
                print(f"note: skipping {path} (scanner config/script file)", file=sys.stderr)
            else:
                files.append((path, path.name))
        elif path.is_dir():
            for candidate in sorted(path.rglob("*")):
                if not candidate.is_file() or candidate.name in SKIP_FILENAMES:
                    continue
                rel_parts = candidate.relative_to(path).parts
                if any(p in SKIP_DIRNAMES for p in rel_parts):
                    continue
                files.append((candidate, "/".join(rel_parts)))
        else:
            raise ConfigError(f"path does not exist: {path}")
    return files


def _gha_escape(value: str) -> str:
    """Escape workflow-command DATA (everything after the '::')."""
    return value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def _gha_escape_property(value: str) -> str:
    """Escape a workflow-command PROPERTY value (file=..., line=...).

    Property values need the data escapes PLUS ':' and ',', which
    otherwise terminate the key or the property list: a corpus file
    named ``a,b.jsonl`` would silently truncate the annotation's
    metadata, and a name containing CR/LF could close the command and
    inject a second one.
    """
    return _gha_escape(value).replace(":", "%3A").replace(",", "%2C")


def _display_path(path: Path) -> Path:
    try:
        return path.resolve().relative_to(Path.cwd().resolve())
    except ValueError:
        return path


def emit(finding: Finding, github: bool) -> None:
    rel = _display_path(finding.path)
    if github:
        print(
            f"::error file={_gha_escape_property(str(rel))}"
            f",line={finding.line},col={finding.col}"
            f"::{_gha_escape(f'{finding.rule} {finding.message}')}"
        )
    else:
        print(f"{rel}:{finding.line}:{finding.col}: [{finding.rule}] {finding.message}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Scan eval corpus files for PII / secret shapes (APPSEC-3971)."
    )
    parser.add_argument(
        "paths", nargs="*", type=Path,
        help="files or directories to scan (default: evals/ and skills/*/evals/)",
    )
    parser.add_argument(
        "--allowlist", type=Path, default=None,
        help=f"regex allowlist file (default: {DEFAULT_ALLOWLIST})",
    )
    parser.add_argument(
        "--denylist", type=Path, default=None,
        help=f"hashed token denylist file (default: {DEFAULT_DENYLIST})",
    )
    args = parser.parse_args(argv)

    github = bool(os.environ.get("GITHUB_ACTIONS"))

    try:
        allowlist = load_allowlist(args.allowlist or DEFAULT_ALLOWLIST)
        denylist = load_denylist(args.denylist or DEFAULT_DENYLIST)
        targets = iter_target_files(args.paths or default_targets())
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2

    total = 0
    config_errors: list[str] = []
    for path, rel in targets:
        # Name first, and outside the try: a file whose CONTENTS cannot
        # be decoded still gets its name checked.
        for finding in scan_path_name(path, rel, denylist, allowlist):
            emit(finding, github)
            total += 1
        try:
            file_findings = scan_file(path, denylist, allowlist)
        except ConfigError as exc:
            # Keep scanning the rest so one bad file reports everything,
            # but the run can no longer be trusted as clean: exit 2.
            config_errors.append(str(exc))
            if github:
                print(
                    f"::error file={_gha_escape_property(str(_display_path(path)))}"
                    f"::{_gha_escape(str(exc))}"
                )
            continue
        for finding in file_findings:
            emit(finding, github)
            total += 1

    if config_errors:
        for message in config_errors:
            print(f"config error: {message}", file=sys.stderr)
        print(
            f"\neval hygiene: {len(config_errors)} file(s) could not be verified"
            + (f"; {total} finding(s) in the rest." if total else "."),
            file=sys.stderr,
        )
        return 2
    if total:
        print(
            f"\neval hygiene: {total} finding(s) across {len(targets)} scanned file(s). "
            "See evals/HYGIENE.md to fix or allowlist.",
            file=sys.stderr,
        )
        return 1
    print(f"eval hygiene: clean ({len(targets)} file(s) scanned).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
