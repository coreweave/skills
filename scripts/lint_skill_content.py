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
    browser-consent       A source file that has the agent drive the
                          customer's authenticated Console session, without
                          the shared `browser-consent` block in scope. That
                          block is the announce-and-WAIT / hand-auth-back /
                          page-content-is-untrusted contract, and it lives
                          once, in _snippets/coreweave-platform.md. Written
                          out by hand instead, the copies drift: APPSEC-3962
                          raised the bar for the read-only quota check and
                          the flow that mints a full-user-scope API token
                          kept the weaker wording for weeks. Add
                          `{{include:browser-consent}}` (body.md, a
                          references/*.md file, or a snippet that nests it)
                          rather than restating the rules.
    broken-shell-guard    The message of a `${VAR:?message}` fail-closed
                          guard is not literal text: bash tokenizes it as
                          shell words even when the expansion sits inside
                          double quotes. Two ways that bites, both verified
                          against bash in tests/test_lint_skill_content.py:
                            - An UNBALANCED quote (an odd number of ' or ")
                              opens a string that never closes, so the WHOLE
                              command is a syntax error -- on the happy path
                              as much as on the guard path. A guard that
                              cannot parse never runs, and an agent facing an
                              unrunnable gate improvises around it, most
                              naturally by deleting the guard. This is how
                              "this cluster's kubeconfig" silently disarmed
                              both kubeconfig gates (APPSEC-3970).
                            - A backtick or `$(` command-substitutes when the
                              guard fires, running a command from inside the
                              thing whose whole job is to refuse to run.
                          Reword the message in plain unquoted prose.

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

# `${VAR:?msg}` / `${VAR?msg}` fail-closed guards. Group 2 is the message,
# which bash tokenizes as shell words -- a stray quote there is a parse error,
# not literal text.
SHELL_GUARD_RE = re.compile(r"\$\{(\w+):?\?([^}]*)\}")

# helm flags whose value is a separate token, skipped so a value is never
# misread as the chart argument. Boolean flags need no entry.
HELM_VALUE_FLAGS = {
    "-f", "--values", "-n", "--namespace", "--version", "--repo",
    "-o", "--output", "--set", "--set-string", "--set-json", "--kube-context",
    # Skills now bind the target cluster explicitly (`helm --kubeconfig <path>
    # --kube-context <name> install ...`) so an install can never fall back to
    # an ambient ~/.kube/config. Without this entry a path written BEFORE the
    # release name is read as the release, the real chart ref falls outside the
    # first two positionals, and an unpinned remote chart slips the rule.
    "--kubeconfig",
}

# Source dirs the browser-consent rule applies to. dist/ and plugins/ are
# build output: build.py resolves the include for them, and CI already fails a
# PR whose dist/ is stale, so checking the generated copies would only ever
# restate what the source check found.
BROWSER_CONSENT_SCAN_DIRS = ("skills", "_snippets")

# Phrases that mean "the agent itself drives the browser". Deliberately narrow:
# prose that merely mentions a browser ("send them straight to a browser") is
# not automation and must not trip the rule.
BROWSER_DRIVEN_RE = re.compile(
    r"browser automation|driv(?:e|es|ing) the browser|browser tools"
    r"|authenticated (?:web )?browser",
    re.IGNORECASE,
)

# Satisfied by the include marker, by a snippet that nests it, or by the
# snippet's own definition. One substring covers all three spellings:
# `{{include:browser-consent}}` and `<!-- snippet:browser-consent -->`.
BROWSER_CONSENT_TOKEN = "browser-consent"

# A body that navigates nothing itself, and instead hands the whole browser
# flow to one of its own reference files, is covered by the block in THAT
# file. cw-create-cluster's Step 1 is the real case: it probes for tool
# availability, then says to read references/quota-check.md and follow its
# safety rules before touching the page. One hop only, and only to a
# reference file of the same skill -- enough for the handoff shape, not
# enough to launder the requirement through an arbitrary mention.
REFERENCE_LINK_RE = re.compile(r"references/([A-Za-z0-9._-]+\.md)")

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


def _consent_in_scope(path: Path, text: str) -> bool:
    """True if the browser-consent block reaches this file's browser flow.

    Directly (the file carries the include marker, nests a snippet that
    does, or is the snippet definition), or through a single hop to one of
    the same skill's reference files.
    """
    if BROWSER_CONSENT_TOKEN in text:
        return True
    for name in set(REFERENCE_LINK_RE.findall(text)):
        target = path.parent / "references" / name
        if not target.is_file():
            target = path.parent / name
        if target.is_file() and BROWSER_CONSENT_TOKEN in target.read_text(
            encoding="utf-8"
        ):
            return True
    return False


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
        for guard in SHELL_GUARD_RE.finditer(line):
            name, message = guard.group(1), guard.group(2)
            unbalanced = next((q for q in ("'", '"') if message.count(q) % 2), None)
            if unbalanced:
                report(lineno, "broken-shell-guard",
                       f"`${{{name}:?...}}` guard message has an unbalanced "
                       f"{unbalanced} — bash reads it as a string that never "
                       "closes, so the whole command is a syntax error and the "
                       "guard never runs (the happy path breaks too); reword "
                       "the message without quotes")
            elif "`" in message or "$(" in message:
                report(lineno, "broken-shell-guard",
                       f"`${{{name}:?...}}` guard message command-substitutes — "
                       "it runs a command at the moment the guard fires; "
                       "reword the message as plain prose")

    # File-level: a source file that drives the browser must have the shared
    # consent block in scope. Reported once, on the first line that shows the
    # file drives it, so the escape hatch can sit above that line.
    if (
        rel.parts[0] in BROWSER_CONSENT_SCAN_DIRS
        and not _consent_in_scope(path, "\n".join(lines))
    ):
        hit = next(
            (i + 1 for i, line in enumerate(lines) if BROWSER_DRIVEN_RE.search(line)),
            None,
        )
        if hit is not None:
            report(hit, "browser-consent",
                   "this file has the agent drive the customer's authenticated "
                   "Console session but never pulls in the shared consent "
                   "contract — add `{{include:browser-consent}}` (and declare "
                   "it in skill.yaml's `includes:`) instead of restating the "
                   "announce/auth-hand-off/untrusted-page rules by hand")

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
