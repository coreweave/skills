#!/usr/bin/env python3
"""Reject skill content that runs code before anyone can refuse it.

WHY THIS EXISTS
---------------
A SKILL.md is not inert prose: skill loaders preprocess it, and the model
executes what it says. Both paths can act BEFORE the usual defenses — before
the tool-permission prompt, and before any Checkpoint written into the skill
itself. This lint scans every `.md` under skills/, _snippets/, dist/, and
plugins/ and fails the build on content that exploits either path:

    bang-directive        `!` in column 0 (not `![`, a markdown image): a
                          Claude Code / Cursor load-time preprocessing
                          directive, executed the moment the skill loads —
                          before the model reads the body, before any prompt.
    git-clone             `git clone` fetches whatever the remote's HEAD is
                          today, not the commit we reviewed. Repo convention
                          is the pinned init / `fetch --depth 1 <url> <sha>` /
                          `checkout <sha>` block, which lives once in
                          _snippets/coreweave-cks.md as the
                          fetch-pinned-ref-arch snippet.
    git-pull              `git pull` drifts a pinned checkout back to HEAD.
    branch-head-artifact  /archive/refs/heads/ tarball URLs re-resolve to the
                          branch tip on every download — pin to a SHA tarball.
    curl-pipe-shell       curl/wget piped into sh executes an unreviewed
                          remote script in one step, with nothing pinned.
    helm-unpinned         helm install/upgrade of a remote repo chart without
                          `--version` floats to the newest published chart.
                          Local chart paths (./, /, ~) are exempt.

Prose that explicitly NEGATES a command is guidance, not an instruction to run
it: "do **not** `git pull`" passes. The negation may end the previous line.

ESCAPE HATCH
------------
A reviewed exception is granted per line, per rule, by the immediately
preceding line:

    <!-- content-lint-allow: git-clone -->

which keeps every exception visible in the diff that introduces it.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCAN_DIRS = ("skills", "_snippets", "dist", "plugins")

ALLOW_RE = re.compile(r"<!--\s*content-lint-allow:\s*([a-z,\s-]+?)\s*-->")
NEGATED_RE = re.compile(r"(?:\bnot\b|\bnever\b|n't)[*_`'\"()\s]*$", re.IGNORECASE)
CURL_PIPE_RE = re.compile(r"\b(?:curl|wget)\b[^|]*\|\s*(?:sudo\s+)?(?:ba)?sh\b")
HELM_RE = re.compile(r"(?:^|[;&|(`]\s*)helm\s+(install|upgrade)\b")

# helm flags whose value is a separate token, skipped so a value is never
# misread as the chart argument. Boolean flags need no entry.
HELM_VALUE_FLAGS = {
    "-f", "--values", "-n", "--namespace", "--version", "--repo",
    "-o", "--output", "--set", "--set-string", "--set-json", "--kube-context",
}

GIT_RULES = (
    ("git clone", "git-clone",
     "`git clone` fetches unpinned HEAD — include the fetch-pinned-ref-arch "
     "snippet (_snippets/coreweave-cks.md) instead"),
    ("git pull", "git-pull",
     "`git pull` drifts a pinned checkout to branch HEAD — re-run the pinned "
     "fetch/checkout block instead"),
)


def logical_lines(lines: list[str]):
    """Yield (first_lineno, text) with backslash continuations joined."""
    i = 0
    while i < len(lines):
        start, text = i, lines[i]
        while text.rstrip().endswith("\\") and i + 1 < len(lines):
            i += 1
            text = text.rstrip()[:-1] + " " + lines[i]
        yield start + 1, text
        i += 1


def helm_chart(text: str) -> str | None:
    """The chart argument of `helm install NAME CHART ...`, if present."""
    positional, skip_next = [], False
    for token in text.split():
        token = token.strip("`'\".,()")
        if skip_next:
            skip_next = False
        elif not token:
            continue
        elif token.startswith("-"):
            skip_next = "=" not in token and token in HELM_VALUE_FLAGS
        else:
            positional.append(token)
            if len(positional) == 2:  # helm install NAME CHART
                return positional[1]
    return None


def remote_chart(chart: str) -> bool:
    """True for `<repo>/<chart>` refs; local paths (./, /, ~) are exempt."""
    return "/" in chart and not chart.startswith((".", "/", "~"))


def scan(path: Path) -> list[str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    rel = path.relative_to(REPO_ROOT)
    findings: list[str] = []

    def report(lineno: int, rule: str, message: str) -> None:
        allow = ALLOW_RE.search(lines[lineno - 2]) if lineno >= 2 else None
        if allow and rule in {r.strip() for r in allow.group(1).split(",")}:
            return
        findings.append(f"{rel}:{lineno}: [{rule}] {message}")

    for idx, line in enumerate(lines):
        lineno = idx + 1
        prev_tail = lines[idx - 1][-40:] if idx else ""
        if line.startswith("!") and not line.startswith("!["):
            report(lineno, "bang-directive",
                   "load-time `!command` directive — the loader executes this "
                   "before the model reads the body and before any permission "
                   "prompt; write it as an instruction in prose instead")
        for phrase, rule, message in GIT_RULES:
            pos = line.find(phrase)
            if pos >= 0 and not NEGATED_RE.search(prev_tail + " " + line[:pos]):
                report(lineno, rule, message)
        if "/archive/refs/heads/" in line:
            report(lineno, "branch-head-artifact",
                   "branch-head tarball URL re-resolves on every download — "
                   "pin to a commit SHA (/archive/<sha>.tar.gz)")

    for lineno, text in logical_lines(lines):
        if CURL_PIPE_RE.search(text):
            report(lineno, "curl-pipe-shell",
                   "remote script piped straight into a shell — download to a "
                   "file, review and pin it, then execute")
        match = HELM_RE.search(text)
        if not match:
            continue
        rest = text[match.end():]
        if "`" in match.group(0):  # inline code: stop at the closing backtick
            rest = rest.split("`", 1)[0]
        chart = helm_chart(rest)
        if chart and remote_chart(chart) and not re.search(r"--version\b", rest):
            report(lineno, "helm-unpinned",
                   f"`helm {match.group(1)} ... {chart}` floats to the newest "
                   f"published chart — add an explicit `--version`")

    return findings


def main() -> int:
    findings: list[str] = []
    scanned = 0
    for name in SCAN_DIRS:
        root = REPO_ROOT / name
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*.md")):
            scanned += 1
            findings.extend(scan(path))

    if findings:
        for finding in findings:
            print(finding, file=sys.stderr)
        print(
            f"\n{len(findings)} content-safety finding(s). Skill content must "
            "not execute at load time or fetch unpinned remote code — see the "
            "module docstring of scripts/lint_skill_content.py for each rule "
            "and for the per-line escape hatch.",
            file=sys.stderr,
        )
        return 1

    print(f"✓ {scanned} markdown file(s) clean: no load-time directives, no unpinned remote code.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
