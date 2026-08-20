#!/usr/bin/env python3
"""Deterministic PII/secret hygiene scanner for the eval corpus (APPSEC-3971).

evals/README.md mandates that everything committed here is sanitized —
customer names, account IDs, emails, tickets, credentials all removed —
because this repo ships to customers. This script turns that mandate into
a blocking CI check.

Design constraints:
  - stdlib only, no network, no LLM. Same input -> same output, always.
  - Scans ``*.jsonl``, ``*.json`` and ``*.md`` files under the target
    directory (default: the directory this script lives in).
  - Regex denylist for pattern-shaped leaks (emails, API keys, tokens,
    IPs, ticket IDs, UUIDs, tenant-bearing console URLs).
  - Hashed customer-name denylist (``hygiene-denylist.sha256``): forbidden
    tokens are stored as SHA-256 hashes so the denylist itself never leaks
    a name. Every word token in the corpus is hashed and compared.
  - Sidecar allowlist (``hygiene-allowlist.txt``): one regex per line;
    a finding is suppressed when an allowlist match on the same line fully
    covers the finding's span. Sidecar because JSONL has no comments, so
    inline suppression markers are impossible.

Exit codes:
  0  clean
  1  one or more findings
  2  configuration error (bad allowlist regex, malformed denylist hash,
     missing explicitly-given path)

Output: one line per finding. Under GitHub Actions (``GITHUB_ACTIONS``
env var set) findings are emitted as ``::error file=...,line=N::...``
workflow annotations so they attach to the diff in the PR view.

See evals/HYGIENE.md for the operator guide (fixing hits, extending the
denylist/allowlist).
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
from pathlib import Path
from typing import Iterator, NamedTuple

SCRIPT_DIR = Path(__file__).resolve().parent

SCAN_EXTENSIONS = {".jsonl", ".json", ".md"}

# Sidecar / self files that are never scanned even if their extension is
# ever added to SCAN_EXTENSIONS: they hold rule config, not corpus content.
SKIP_FILENAMES = {
    "check_eval_hygiene.py",
    "hygiene-allowlist.txt",
    "hygiene-denylist.sha256",
}

DEFAULT_ALLOWLIST = SCRIPT_DIR / "hygiene-allowlist.txt"
DEFAULT_DENYLIST = SCRIPT_DIR / "hygiene-denylist.sha256"

# --------------------------------------------------------------------------
# Regex rules. Each entry: (rule_name, compiled_regex).
#
# False-positive constraint (see HYGIENE.md): the corpus legitimately
# contains CoreWeave availability-zone names (US-EAST-04A) and instance
# types (gd-8xh100ib-i128). The ticket-ID rule therefore requires the match
# to be a standalone token — not preceded or followed by another
# hyphen-joined segment — so the EAST-04 substring of US-EAST-04A can
# never match. hygiene-allowlist.txt carries explicit zone-name entries as
# defense in depth.
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
        "slack-token",
        re.compile(r"\bxox[bpars]-[A-Za-z0-9-]{10,}"),
    ),
    (
        # Base64url-encoded '{"' is 'eyJ'. Require at least two
        # dot-separated segments so prose mentioning the bare prefix
        # doesn't trip.
        "jwt",
        re.compile(r"\beyJ[A-Za-z0-9_=-]{8,}(?:\.[A-Za-z0-9_=-]{4,}){1,2}\b"),
    ),
    (
        "pem-header",
        re.compile(r"-----BEGIN [A-Z][A-Z0-9 ]*-----"),
    ),
    (
        # Valid dotted quads only; the lookarounds keep it from matching
        # inside longer dotted runs ("1.2.3.4.5") or version-ish strings
        # ("v1.2.3.4").
        "ipv4-address",
        re.compile(
            r"(?<![\w.])"
            r"(?:(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)\.){3}"
            r"(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)"
            r"(?![\w.])"
        ),
    ),
    (
        # Jira-style ticket IDs (project key + number). Must be a
        # standalone token: not preceded or followed by a further
        # hyphen-joined segment, so zone names like US-EAST-04A never
        # match on their EAST-04 substring.
        "ticket-id",
        re.compile(r"(?<![\w-])[A-Z][A-Z0-9]{1,9}-\d{1,6}(?![\w-])"),
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
        # identifier in a path segment or query parameter.
        "console-url-with-org-id",
        re.compile(
            r"https?://[A-Za-z0-9.-]*coreweave\.com[^\s\"'<>]*"
            r"(?:/(?:orgs?|organizations?|accounts?|tenants?)/[A-Za-z0-9_-]{2,}"
            r"|[?&](?:org|org_?id|account|account_?id|tenant)=[A-Za-z0-9_-]{2,})",
            re.IGNORECASE,
        ),
    ),
]

# Word tokens hashed against the customer-name denylist. Two passes:
# simple alphanumeric runs, and separator-joined compounds (which are
# additionally hashed with separators stripped, so the canonical
# "all lowercase, no separators" denylist entry catches hyphenated and
# dotted spellings too).
_SIMPLE_TOKEN = re.compile(r"[a-z0-9]+")
_COMPOUND_TOKEN = re.compile(r"[a-z0-9]+(?:[._-][a-z0-9]+)+")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


class ConfigError(Exception):
    pass


def _iter_config_lines(path: Path, explicit: bool, kind: str) -> Iterator[tuple[int, str]]:
    """Yield (lineno, stripped line) skipping blanks/#-comments.

    A missing default sidecar is treated as empty; a missing explicitly
    given path is a configuration error.
    """
    if not path.is_file():
        if explicit:
            raise ConfigError(f"{kind} file not found: {path}")
        return
    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if line and not line.startswith("#"):
            yield lineno, line


def load_allowlist(path: Path, explicit: bool) -> list[re.Pattern[str]]:
    patterns = []
    for lineno, line in _iter_config_lines(path, explicit, "allowlist"):
        try:
            patterns.append(re.compile(line))
        except re.error as exc:
            raise ConfigError(f"{path}:{lineno}: invalid allowlist regex {line!r}: {exc}")
    return patterns


def load_denylist(path: Path, explicit: bool) -> set[str]:
    hashes = set()
    for lineno, line in _iter_config_lines(path, explicit, "denylist"):
        if not _HEX64.fullmatch(line):
            raise ConfigError(
                f"{path}:{lineno}: denylist entries must be one lowercase "
                f"64-char hex SHA-256 per line, got {line!r}"
            )
        hashes.add(line)
    return hashes


def _sha256(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def redact(text: str) -> str:
    """Show just enough of a match to locate it without re-leaking it."""
    keep = 2 if len(text) <= 8 else 6
    keep = min(keep, max(1, len(text) // 2))  # never reveal more than half
    masked = max(1, min(len(text) - keep, 12))  # always mask something
    return text[:keep] + "*" * masked


class Finding(NamedTuple):
    path: Path
    line: int
    col: int
    rule: str
    message: str
    span: tuple[int, int]


def _token_candidates(lower_line: str) -> Iterator[tuple[tuple[int, int], tuple[str, ...]]]:
    """Yield (span, candidate tokens to hash) for the denylist check.

    Simple alphanumeric runs are hashed as-is. Separator-joined compounds
    are hashed both verbatim and separator-stripped, so the canonical
    "all lowercase, no separators" denylist entry catches hyphenated,
    dotted, and underscored spellings too.
    """
    for m in _SIMPLE_TOKEN.finditer(lower_line):
        yield m.span(), (m.group(0),)
    for m in _COMPOUND_TOKEN.finditer(lower_line):
        compound = m.group(0)
        yield m.span(), (compound, re.sub(r"[._-]", "", compound))


def scan_line(path: Path, lineno: int, line: str, denylist: set[str]) -> list[Finding]:
    findings: list[Finding] = []
    for rule, pattern in RULES:
        for m in pattern.finditer(line):
            findings.append(
                Finding(path, lineno, m.start() + 1, rule,
                        f"matched: {redact(m.group(0))}", (m.start(), m.end()))
            )
    if denylist:
        for span, tokens in _token_candidates(line.lower()):
            for token in tokens:
                digest = _sha256(token)
                if digest in denylist:
                    findings.append(
                        Finding(path, lineno, span[0] + 1, "customer-denylist",
                                f"denylisted token (sha256 {digest[:12]}…, value withheld)",
                                span)
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


def _is_scannable(path: Path) -> bool:
    return path.suffix.lower() in SCAN_EXTENSIONS and path.name not in SKIP_FILENAMES


def iter_target_files(paths: list[Path]) -> list[Path]:
    files: list[Path] = []
    for path in paths:
        if path.is_file():
            # Same filter as the directory walk: sidecar config files and
            # unscanned extensions are exempt even when named explicitly.
            if _is_scannable(path):
                files.append(path)
            else:
                print(f"note: skipping {path} (not a scanned file type)", file=sys.stderr)
        elif path.is_dir():
            files.extend(
                candidate
                for candidate in sorted(path.rglob("*"))
                if candidate.is_file() and _is_scannable(candidate)
            )
        else:
            raise ConfigError(f"path does not exist: {path}")
    return files


def _gha_escape(value: str) -> str:
    return value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def emit(finding: Finding, github: bool) -> None:
    try:
        rel = finding.path.resolve().relative_to(Path.cwd().resolve())
    except ValueError:
        rel = finding.path
    if github:
        print(
            f"::error file={rel},line={finding.line},col={finding.col}"
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
        help="files or directories to scan (default: this script's directory)",
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
        allowlist = load_allowlist(
            args.allowlist or DEFAULT_ALLOWLIST, explicit=args.allowlist is not None
        )
        denylist = load_denylist(
            args.denylist or DEFAULT_DENYLIST, explicit=args.denylist is not None
        )
        targets = iter_target_files(args.paths or [SCRIPT_DIR])
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2

    total = 0
    for path in targets:
        text = path.read_text(encoding="utf-8", errors="replace")
        for lineno, line in enumerate(text.splitlines(), 1):
            for finding in scan_line(path, lineno, line, denylist):
                if is_allowed(line, finding, allowlist):
                    continue
                emit(finding, github)
                total += 1

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
