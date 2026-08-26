#!/usr/bin/env python3
"""Deterministic PII/secret hygiene scanner for the eval corpora (APPSEC-3971).

evals/README.md mandates that everything committed here is sanitized —
customer names, account IDs, emails, tickets, credentials all removed —
because this repo ships to customers. This script turns that mandate into
a blocking CI check.

Design constraints:
  - stdlib only, no network, no LLM. Same input -> same output, always.
  - Opt-out coverage: scans EVERY file under the target directories
    (by default: every tree that becomes publicly readable — the eval
    corpora, the skill sources, and the rendered dist/ and plugins/
    trees) except this script, the eval runner, the allowlist sidecar,
    and a fixed list
    of never-committed cache/local-state directories. New file types —
    and hidden files, which are exactly as public as any other
    committed file — are covered by default rather than silently
    exempt.
  - Names are scanned too: the target-relative path of every file is
    run through the same rules, so a fixture named after a ticket or an
    account UUID cannot pass a gate that only reads contents.
  - Fail closed on encoding: files are decoded strictly (UTF-8, or
    UTF-16/32 via BOM sniff). A file that does not decode cleanly or
    contains NUL bytes after decoding cannot be verified and is a
    configuration error (exit 2) — never treated as clean.
  - Fail closed on rule config: the allowlist sidecar being missing,
    unreadable, or not valid UTF-8 is a configuration error even though
    it is the built-in default. Loading a missing default as "empty"
    is the shape that lets a deleted rule file pass with a green
    build, so it is refused here too.
  - JSON-aware: for *.json / *.jsonl the decoded string values are ALSO
    scanned, so \\uXXXX escaping cannot hide a match from the raw-text
    pass (and non-ASCII names survive json.dumps(ensure_ascii=True)).
  - PATTERN-ONLY, deliberately. Every rule matches a SHAPE — emails,
    API keys, tokens, IPs, ticket IDs, UUIDs, tenant-bearing console
    URLs — and the gate commits nothing about any specific customer.
    An earlier revision carried a hashed customer-name denylist; it was
    removed because making it work meant committing a reversible hash
    of every protected name to a public repo, where the entry count and
    `git log` dates leak on their own. Catching a customer NAME in
    otherwise-clean prose is the job of the second-reviewer control
    (the threat model's defense chain, layer 4), not of this scanner.
    Do not reintroduce a name list here.
  - Sidecar allowlist (``hygiene-allowlist.txt``): one regex per line;
    a finding is suppressed when an allowlist match on the same line
    fully covers the finding's span. Sidecar because JSONL has no
    comments, so inline suppression markers are impossible.

Exit codes:
  0  clean
  1  one or more findings
  2  configuration error (bad allowlist regex, missing/unreadable
     allowlist, missing given path, undecodable file).
     Config errors take precedence over findings: exit 2 means the
     corpus could not be fully verified.

Output: one line per finding. Under GitHub Actions (``GITHUB_ACTIONS``
env var set) findings are emitted as ``::error file=...,line=N::...``
workflow annotations so they attach to the diff in the PR view.

See evals/HYGIENE.md for the operator guide (fixing hits, extending the
allowlist, known residual limits).
"""

from __future__ import annotations

import argparse
import codecs
import json
import os
import re
import sys
from pathlib import Path
from typing import Iterator, NamedTuple

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent

# Files that are never scanned, by name. Name-based exemption is a
# standing hole in the gate, so the bar for an entry is narrow: scanning
# the file must be either self-defeating or impossible.
#   - self-defeating: the three gate files below carry the ruleset
#     itself. Their literals ARE the patterns, so scanning them reports
#     the rules rather than a leak.
#   - impossible: .DS_Store is binary Finder metadata, so the strict
#     decode turns it into a configuration error (exit 2) — it would
#     break every local run on a Mac while verifying nothing. The file
#     names it caches belong to files the gate scans in their own right.
#
# "Not normally committed" is NOT on that list, which is why the
# run_trigger_evals.py --out artifacts (trigger-results.json and
# friends) are absent even though they are gitignored: they hold raw
# model and tool output, the least-reviewed text in the tree, so an
# exemption would leave a force-added copy permanently unscanned. Delete
# a local sweep artifact before running the gate rather than
# allowlisting its content.
SKIP_FILENAMES = {
    "check_eval_hygiene.py",
    "check_eval_hygiene_selftest.py",  # a planted-violation file BY DESIGN
    "run_trigger_evals.py",
    "hygiene-allowlist.txt",
    ".DS_Store",  # binary Finder metadata: undecodable, and gitignored
    ".git",       # a FILE named .git: git's worktree pointer, not a dir
}

# Extensions whose contents are not text and cannot be scanned. Listed
# by extension rather than sniffed, so the strict decode below keeps its
# teeth: a file that CLAIMS to be text (.md, .jsonl) and isn't is still a
# configuration error, because that is how binary content sneaks into a
# corpus. This list only covers files that were never text to begin with.
BINARY_EXTENSIONS = {
    ".gif", ".png", ".jpg", ".jpeg", ".webp", ".ico", ".svgz", ".pdf",
    ".zip", ".gz", ".tar", ".woff", ".woff2", ".ttf", ".otf", ".eot",
    ".mp4", ".mov", ".webm", ".pyc", ".so", ".dylib", ".wasm",
}

# Never-committed cache / local-state DIRECTORIES. Every name here is
# ignored by the repo root .gitignore except ".git", which git itself
# never tracks and which no ignore rule can therefore cover; the
# gitignore half of that claim is asserted by
# scripts/check_eval_hygiene_selftest.py rather than left to this
# comment to keep true. Matched by NAME, not by
# a blanket "starts with a dot" rule: a committed .fixture.jsonl is
# exactly as public as any other file in the tree, so hidden files must
# not be able to opt themselves out of the gate.
#
# Only directory components of a path are tested against this set (see
# iter_target_files): a FILE named "env" or "node_modules" is ordinary
# corpus content and is scanned. Keep this list in sync with .gitignore
# and with the exemption list in HYGIENE.md.
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

# Ticket-shaped tokens that are standards/products, not internal tickets.
# Baked into the rule (rather than the allowlist) because they are an
# open-ended class the corpus will keep hitting: GPU-adjacent prose is
# full of them. Case-insensitive, must be the whole project-key prefix.
_BENIGN_TICKET_PREFIXES = (
    "IEEE|FIPS|SOC|PCI|NIST|TLS|SSL|ISO|RFC|SHA|UTF|MD|AES|RSA|CVE|GPT"
    "|COVID|HTTPS|HTTP|TCP|UDP|DNS|ANSI|POSIX|OAUTH|DIN|EN"
)


def _ticket_pattern(key: str) -> re.Pattern[str]:
    """Jira-style ``KEY-<number>`` as a standalone token."""
    return re.compile(
        r"(?<![\w-])"
        r"(?!(?:" + _BENIGN_TICKET_PREFIXES + r")-)"
        + key + r"-\d{1,6}(?![\w-])",
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
        # Jira-style ticket IDs: an UPPERCASE letters-only project key, a
        # hyphen, and an issue number, as a standalone token.
        #
        # Uppercase-only is a deliberate, measured trade. This is an AI
        # infrastructure product, so its shipped content is dense with
        # `Word-<number>` tokens that are not tickets and never will be:
        # a scan of dist/ found `cpu-4` (an instance type) 16 times,
        # `Llama-3` 8 times, and `TinyLlama-1` twice, against ZERO real
        # ticket IDs. A case-insensitive rule red-gates every one of
        # those, and a gate that cries wolf on ordinary vocabulary gets
        # routed around rather than fixed.
        #
        # The cost, stated plainly: an all-lowercase paste
        # ("see appsec-1234") is not flagged. Real Jira keys are written
        # uppercase, so this keeps the case that matters while making
        # the product's own vocabulary structurally benign instead of
        # benign-by-allowlist-maintenance.
        "ticket-id",
        _ticket_pattern(r"[A-Z]{2,10}"),
    ),
    (
        # PASTE RESIDUE. Text typed into an editor does not contain these;
        # text pasted out of Slack, Gmail, or a browser very often does.
        # Verified zero occurrences across evals/, dist/, plugins/,
        # skills/ and _snippets/ before this rule was made blocking, so
        # a hit means something arrived by copy-paste rather than by
        # authoring — which is the moment sanitization gets skipped.
        #
        # Non-breaking and zero-width spaces, joiners, soft hyphen, and a
        # BOM appearing anywhere but the first byte. NOT em-dash or
        # ellipsis: this repo's prose uses both constantly (44 and 12
        # files), so flagging them would be pure noise.
        "invisible-character",
        re.compile("[\u00a0\u00ad\u200b\u200c\u200d\u202f\u2060\ufeff]"),
    ),
    (
        # Curly quotes are what a chat client's autoformat produces. Also
        # verified zero repo-wide. The fix is always the same: retype the
        # ASCII quote.
        "smart-quote",
        re.compile("[\u2018\u2019\u201c\u201d]"),
    ),
    (
        # Slack user/channel reference markup, which survives a paste and
        # names an internal person or channel outright.
        "chat-mention",
        re.compile(r"<[@#][A-Z0-9]{6,}(?:\|[^>]*)?>|(?<![\w/])@(?:here|channel|everyone)\b"),
    ),
    (
        # Quote scaffolding from a mail or chat client: the attribution
        # line carries a real name and a real timestamp.
        "quoted-reply-header",
        re.compile(
            r"^\s*On .{0,80}?\bwrote:\s*$"
            r"|Sent from my \w+"
            r"|^\s*\[?\d{1,2}:\d{2}(?::\d{2})?\s*(?:[AaPp]\.?[Mm]\.?)\]?\s",
            re.MULTILINE,
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

# Path separators, for splitting a corpus-relative name into segments
# before the name-level scan (see _name_candidates).
_PATH_SEPARATOR = re.compile(r"[/\\]")


# Rules whose matched text cannot be shown literally: redacting an
# invisible character prints nothing at all, which is a useless finding.
# These report the code point instead, which is also the actionable
# detail ("there is a U+00A0 at column 34").
CODEPOINT_RULES = {"invisible-character"}

# --------------------------------------------------------------------------
# Two axes decide what a rule does: WHERE it applies, and whether a hit
# BLOCKS or merely warns. Both exist to keep the gate believable — a
# scanner that red-gates a LICENSE over a typographic quote gets switched
# off, and then it protects nothing.
# --------------------------------------------------------------------------

# Rules that only make sense over the eval CORPORA. Tier 1 asks "did this
# text arrive by paste?", which is a sharp question about a corpus of
# customer queries and a meaningless one about a hand-written LICENSE,
# README, or source file. Applied repo-wide, `smart-quote` fires on the
# typographic apostrophe in LICENSE and on ordinary prose — noise that
# buys nothing, since nobody pastes a support transcript into LICENSE.
CORPUS_ONLY_RULES = frozenset({
    "invisible-character",
    "smart-quote",
    "chat-mention",
    "quoted-reply-header",
})
PASTE_RESIDUE_RULES = CORPUS_ONLY_RULES

# Rules that BLOCK a merge. Everything else warns.
#
# The split is by false-positive rate, not by severity of the thing
# described. These five match shapes that essentially cannot occur by
# accident — an `AKIA` followed by exactly 16 uppercase alphanumerics is
# a credential, not a coincidence — so a hit is real and merging it
# publishes a live secret. Every other rule matches something that has
# legitimate look-alikes (a documentation IP, an example address, a
# version-numbered model name), so it reports and lets a human look
# rather than stopping the queue. See --strict to block on everything.
BLOCKING_RULES = frozenset({
    "anthropic-api-key",
    "aws-access-key-id",
    "github-token",
    "slack-token",
    "pem-header",
    "jwt",
})

# Tier 1 paste-residue rules. These do NOT look for an identifier; they
# look for evidence that text arrived by copy-paste rather than by
# authoring, which is a meaningful claim about a COMMITTED FILE and a
# meaningless one about PR text.
#
# That distinction is load-bearing, and getting it wrong is how this
# gate would have died: applied to PR prose, `smart-quote` fired on
# ordinary review comments, because a curly apostrophe in a sentence
# someone typed into a web box is just an apostrophe. In a corpus file
# it is a signal. So scan_stdin drops these and keeps the rules about
# what the text CONTAINS, which leak wherever they appear.
PASTE_RESIDUE_RULES = {
    "invisible-character",
    "smart-quote",
    "chat-mention",
    "quoted-reply-header",
}


class ConfigError(Exception):
    pass


def _iter_config_lines(path: Path, kind: str) -> Iterator[tuple[int, str]]:
    """Yield (lineno, stripped line) skipping blanks/#-comments.

    Fails closed, and identically for a default and an explicitly given
    path: a sidecar that is missing, unreadable, or not valid UTF-8 is a
    ConfigError. Treating a missing DEFAULT as "empty config" is the
    dangerous case — deleting hygiene-allowlist.txt would turn the
    sidecar into an empty ruleset and still exit 0 — and an
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
    key: str  # dedup key: the matched text


def scan_line(path: Path, lineno: int, line: str,
              skip_rules: frozenset[str] = frozenset()) -> list[Finding]:
    """Apply every rule except ``skip_rules`` to one line."""
    findings: list[Finding] = []
    for rule, pattern in RULES:
        if rule in skip_rules:
            continue
        for m in pattern.finditer(line):
            if rule in CODEPOINT_RULES:
                shown = " ".join(f"U+{ord(c):04X}" for c in m.group(0))
            else:
                shown = redact(m.group(0))
            findings.append(
                Finding(path, lineno, m.start() + 1, rule,
                        f"matched: {shown}", (m.start(), m.end()),
                        m.group(0))
            )
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


def _scan_decoded(path: Path, lineno: int, doc: object,
                  allowlist: list[re.Pattern[str]], seen_keys: set[tuple[str, str]],
                  note: str, skip: frozenset[str] = frozenset()) -> list[Finding]:
    out: list[Finding] = []
    seen = set(seen_keys)
    for value in _iter_json_strings(doc):
        for finding in scan_line(path, lineno, value, skip):
            key = (finding.rule, finding.key)
            if key in seen:
                continue  # already reported by the raw-text pass
            if is_allowed(value, finding, allowlist):
                continue  # suppressed here, but do NOT claim it as seen
            seen.add(key)
            out.append(finding._replace(message=finding.message + note))
    return out


def scan_file(path: Path, allowlist: list[re.Pattern[str]],
              is_corpus: bool = True) -> list[Finding]:
    try:
        text = _read_text_strict(path)
    except OSError as exc:
        raise ConfigError(f"{path}: cannot read ({exc})")

    skip = frozenset() if is_corpus else CORPUS_ONLY_RULES
    findings: list[Finding] = []
    keys_by_line: dict[int, set[tuple[str, str]]] = {}
    lines = text.splitlines()

    for lineno, line in enumerate(lines, 1):
        for finding in scan_line(path, lineno, line, skip):
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
                _scan_decoded(path, lineno, doc, allowlist,
                              keys_by_line.get(lineno, set()),
                              " (in decoded JSON value)", skip)
            )
    elif suffix == ".json":
        try:
            doc = json.loads(text)
        except ValueError:
            doc = None
        if doc is not None:
            seen = set().union(*keys_by_line.values()) if keys_by_line else set()
            findings.extend(
                _scan_decoded(path, 1, doc, allowlist, seen,
                              " (in decoded JSON document; reported at line 1)",
                              skip)
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
    that without weakening the rule.

    Splitting on hyphens necessarily manufactures ``word-number`` pairs
    out of perfectly ordinary names (gpu-8-node.jsonl yields "gpu-8"),
    so the pairs alone cannot be what keeps benign names benign — the
    uppercase-key requirement in the ticket-id rule is. The pairs only
    decide WHERE a candidate ticket key can start; the rule decides
    whether it looks like a ticket at all.
    """
    yield rel
    for segment in _PATH_SEPARATOR.split(rel):
        chunks = segment.split("-")
        for first, second in zip(chunks, chunks[1:]):
            pair = f"{first}-{second}"
            if pair != segment:
                yield pair


def scan_path_name(path: Path, rel: str, allowlist: list[re.Pattern[str]],
                   is_corpus: bool = True) -> list[Finding]:
    """Scan a file's own name/path text with the same rules.

    A corpus file named after a ticket, an email address, or an
    account UUID publishes that identifier in the
    repo's tree listing exactly as effectively as its contents would —
    and the content pass never sees a file name, so this gate used to
    report such a tree clean.

    ``rel`` is the path relative to the scanned target, so the result
    never depends on the caller's working directory (a home directory or
    checkout path is not corpus content). Findings are reported at line
    1, column 1: the leak is in the name, not at some offset inside the
    file. Duplicate values across candidates collapse to one finding.

    Uses the same RULES as the content pass. They used to diverge on
    ticket-id (uppercase-only on names, case-insensitive in contents);
    that split is gone because the content rule is uppercase-only too
    now, for the same reason the name rule always was — ordinary
    vocabulary is full of ``word-<number>``.
    """
    out: list[Finding] = []
    seen: set[tuple[str, str]] = set()
    for candidate in _name_candidates(rel):
        for finding in scan_line(path, 1, candidate,
                                 frozenset() if is_corpus else CORPUS_ONLY_RULES):
            key = (finding.rule, finding.key)
            if key in seen or is_allowed(candidate, finding, allowlist):
                continue
            seen.add(key)
            out.append(finding._replace(
                col=1,
                message=finding.message + f" (in the file NAME {rel!r}, not its contents)",
            ))
    return out


def is_corpus_path(rel_parts: tuple[str, ...]) -> bool:
    """True for the eval corpora, where the paste-residue rules apply.

    That is ``evals/**`` and ``skills/<name>/evals/**`` — the trees that
    hold text sourced from real customer conversations. Everywhere else
    is authored prose or code, where "did this arrive by paste?" is not
    a meaningful question.
    """
    if not rel_parts:
        return False
    if rel_parts[0] == "evals":
        return True
    # skills/<name>/evals/<file> — the "evals" segment is index 2, and a
    # file under it means at least four components.
    return (len(rel_parts) >= 4
            and rel_parts[0] == "skills"
            and rel_parts[2] == "evals")


def default_targets() -> list[Path]:
    """The whole repository.

    The repo is going fully public, so every committed file is a
    disclosure surface — not just the trees a customer installs. An
    earlier revision scanned only the corpora and the rendered output,
    on the theory that build.yml's dist/-drift check made the sources
    redundant. It does not: only TAGGED REGIONS of a snippet are inlined,
    so most of a snippet file never renders into dist/ and yet is
    world-readable in the repo all the same.

    Scanning everything is therefore the only scope that matches the
    threat. What keeps that from drowning the build in noise is not a
    narrower scope but narrower RULES — see CORPUS_ONLY_RULES and
    BLOCKING_RULES.
    """
    return [REPO_ROOT]


def iter_target_files(paths: list[Path]) -> list[tuple[Path, str, bool]]:
    """Return (path, target-relative name) for every file to scan.

    The second element is the text the name-level rules run on. It is
    relative to the target that produced it, so it is identical on every
    machine. Only SKIP_FILENAMES and SKIP_DIRNAMES are excluded —
    hidden files are scanned, because a committed one is as public as
    any other file in the tree.

    SKIP_DIRNAMES is tested against the DIRECTORY components of the
    relative path only (``rel_parts[:-1]``). Including the final
    component let the directory exemptions leak onto files: a corpus
    file named ``env``, ``node_modules`` or ``.claude`` — extensionless
    names are perfectly ordinary fixture names — was silently exempt
    from the gate, contents and all.
    """
    files: list[tuple[Path, str, bool]] = []
    for path in paths:
        if path.is_file():
            if path.name in SKIP_FILENAMES:
                print(f"note: skipping {path} (scanner config/script file)", file=sys.stderr)
            elif path.suffix.lower() in BINARY_EXTENSIONS:
                print(f"note: skipping {path} (binary)", file=sys.stderr)
            else:
                files.append((path, path.name, True))
        elif path.is_dir():
            for candidate in sorted(path.rglob("*")):
                if not candidate.is_file() or candidate.name in SKIP_FILENAMES:
                    continue
                if candidate.suffix.lower() in BINARY_EXTENSIONS:
                    continue
                rel_parts = candidate.relative_to(path).parts
                if any(p in SKIP_DIRNAMES for p in rel_parts[:-1]):
                    continue
                files.append((candidate, "/".join(rel_parts),
                              is_corpus_path(rel_parts)))
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


def emit(finding: Finding, github: bool, blocking: bool) -> None:
    """Print one finding, as an error if it blocks and a warning if not."""
    rel = _display_path(finding.path)
    level = "error" if blocking else "warning"
    if github:
        print(
            f"::{level} file={_gha_escape_property(str(rel))}"
            f",line={finding.line},col={finding.col}"
            f"::{_gha_escape(f'{finding.rule} {finding.message}')}"
        )
    else:
        print(f"{rel}:{finding.line}:{finding.col}: "
              f"[{level}] [{finding.rule}] {finding.message}")


def scan_stdin(text: str, label: str, allowlist: list[re.Pattern[str]],
               github: bool, warn_only: bool = False) -> int:
    """Scan free text (a PR body, a review comment) with the same rules.

    Same ruleset as the file pass, on purpose: a customer identifier is
    exactly as public in a PR description as in a committed file, and PR
    text gets far less review than a diff does — nobody re-reads a
    comment before merging.

    What this can and cannot do differs by surface, and the difference
    is worth stating. For a PR BODY this is a real gate: the author can
    edit the description and the check goes green before merge. For a
    COMMENT it is an alarm, not a gate — the comment was public the
    moment it was posted, so a hit means "handle a disclosure" (rotate
    the credential, and remember GitHub keeps edit history), never "edit
    it and move on".

    ``warn_only`` follows that split. A comment finding annotates and
    returns 0: a permanently-red check on text nobody can un-publish
    teaches people to ignore the check, which costs more than it buys.
    A body finding returns 1 and blocks.

    Paste-residue rules are skipped here entirely — see
    PASTE_RESIDUE_RULES for why applying them to prose was wrong.

    Findings are emitted without file=/line= because there is no file to
    anchor to; they surface in the job log and summary. Line numbers are
    relative to the text supplied.
    """
    findings = [
        finding
        for lineno, line in enumerate(text.splitlines(), 1)
        for finding in scan_line(Path(label), lineno, line,
                                 skip_rules=frozenset(PASTE_RESIDUE_RULES))
        if not is_allowed(line, finding, allowlist)
    ]
    level = "warning" if warn_only else "error"
    for finding in findings:
        message = f"{label} line {finding.line}: {finding.rule} {finding.message}"
        print(f"::{level}::{_gha_escape(message)}" if github else f"[{level}] {message}")
    if findings:
        print(
            f"\n{label}: {len(findings)} finding(s)."
            + (" ALREADY PUBLIC — this is a disclosure to handle, not a typo "
               "to edit. See evals/HYGIENE.md."
               if warn_only else
               " Edit the description to clear this. See evals/HYGIENE.md."),
            file=sys.stderr,
        )
        return 0 if warn_only else 1
    print(f"{label}: clean.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Scan eval corpus files for PII / secret shapes (APPSEC-3971)."
    )
    parser.add_argument(
        "paths", nargs="*", type=Path,
        help="files or directories to scan (default: evals/ and skills/*/evals/)",
    )
    parser.add_argument(
        "--format", choices=("text", "json"), default="text",
        help="json emits a machine-readable report on stdout (and no "
             "annotations) so scripts/post_hygiene_comments.py can turn "
             "each finding into a resolvable PR review thread",
    )
    parser.add_argument(
        "--strict", action="store_true",
        help="treat every finding as blocking, not just the credential "
             "rules (default: only BLOCKING_RULES fail the run)",
    )
    parser.add_argument(
        "--stdin", action="store_true",
        help="scan text on stdin instead of files (for PR bodies and comments)",
    )
    parser.add_argument(
        "--warn-only", action="store_true",
        help="report --stdin findings as warnings and exit 0 (for already-"
             "published text such as comments, which cannot be un-posted)",
    )
    parser.add_argument(
        "--label", default="stdin",
        help="what the --stdin text is, e.g. 'PR body' (used in the report)",
    )
    parser.add_argument(
        "--allowlist", type=Path, default=None,
        help=f"regex allowlist file (default: {DEFAULT_ALLOWLIST})",
    )
    args = parser.parse_args(argv)

    github = bool(os.environ.get("GITHUB_ACTIONS"))

    try:
        allowlist = load_allowlist(args.allowlist or DEFAULT_ALLOWLIST)
        targets = [] if args.stdin else iter_target_files(
            args.paths or default_targets())
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2

    if args.stdin:
        return scan_stdin(sys.stdin.read(), args.label, allowlist, github,
                          warn_only=args.warn_only)

    total = 0
    blocking = 0
    collected: list[dict[str, object]] = []
    as_json = args.format == "json"
    config_errors: list[str] = []
    for path, rel, is_corpus in targets:
        def report(finding: Finding, scope: str = "content") -> None:
            nonlocal total, blocking
            blocks = args.strict or finding.rule in BLOCKING_RULES
            total += 1
            blocking += 1 if blocks else 0
            if as_json:
                collected.append({
                    "path": str(_display_path(finding.path)),
                    "line": finding.line,
                    "col": finding.col,
                    "rule": finding.rule,
                    # Already redacted by scan_line; never the raw value.
                    "message": finding.message,
                    "blocking": blocks,
                    # "name" findings are about the file's PATH, so their
                    # line 1 is not where the leak is. The comment poster
                    # uses this to route them to the summary instead of
                    # anchoring a thread to an unrelated first line.
                    "scope": scope,
                })
            else:
                emit(finding, github, blocks)

        # Name first, and outside the try: a file whose CONTENTS cannot
        # be decoded still gets its name checked.
        for finding in scan_path_name(path, rel, allowlist, is_corpus):
            report(finding, scope="name")
        try:
            file_findings = scan_file(path, allowlist, is_corpus)
        except ConfigError as exc:
            # Keep scanning the rest so one bad file reports everything,
            # but the run can no longer be trusted as clean: exit 2.
            config_errors.append(str(exc))
            if github and not as_json:
                print(
                    f"::error file={_gha_escape_property(str(_display_path(path)))}"
                    f"::{_gha_escape(str(exc))}"
                )
            continue
        for finding in file_findings:
            report(finding)

    if as_json:
        json.dump({
            "scanned": len(targets),
            "total": total,
            "blocking": blocking,
            "config_errors": config_errors,
            "findings": collected,
        }, sys.stdout, indent=2, sort_keys=True)
        print()
        return 2 if config_errors else (1 if blocking else 0)

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
        warned = total - blocking
        summary = [f"\neval hygiene: {total} finding(s) across "
                   f"{len(targets)} scanned file(s)."]
        if blocking:
            summary.append(
                f"  {blocking} BLOCKING (credential-shaped — these shapes do "
                f"not occur by accident; rotate the value, do not just edit it)."
            )
        if warned:
            summary.append(
                f"  {warned} warning(s) — reported for a human to look at, "
                f"not blocking. Confirm each is a false positive before "
                f"ignoring it."
            )
        summary.append("See evals/HYGIENE.md to fix or allowlist.")
        print("\n".join(summary), file=sys.stderr)
        return 1 if blocking else 0
    print(f"eval hygiene: clean ({len(targets)} file(s) scanned).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
