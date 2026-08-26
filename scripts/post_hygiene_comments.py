#!/usr/bin/env python3
"""Turn hygiene findings into resolvable PR review threads.

WHY THIS EXISTS
---------------
`::warning` annotations are a poor place to put something a human is
supposed to act on. Measured on a real run of this repo's own workflow:

  - GitHub returned exactly **10** annotations and dropped the rest. An
    eleventh finding is not truncated-with-a-notice, it is simply absent.
  - Findings with no `file=` (the PR-text pass) were anchored to the
    WORKFLOW FILE's `run:` step — `path=.github/workflows/...`, lines 61
    to 67 — which points at the scanner instead of at the leak.
  - Nothing appears on the PR conversation. A reviewer sees a coloured
    badge and has to click into the Actions run to learn anything.
  - There is no acknowledgement. Nobody can tell whether a warning was
    read and judged benign, or never opened.

A review thread fixes all four. It sits on the offending line, it
survives past ten, it renders where review happens, and — with "Require
conversation resolution before merging" enabled in branch protection —
somebody must explicitly resolve it. That converts "a warning nobody
opened" into "a human looked at this and said it was fine", which is the
actual control. The warning tier is only defensible *because* of this:
without it, non-blocking means unnoticed.

WHAT IT DOES
------------
Reads the `--format json` report, then for each finding:

  - if the offending line is part of the PR diff, posts an inline review
    comment on that line;
  - otherwise collects it into one summary comment, because GitHub
    cannot anchor a review comment to a line it is not showing.

Idempotent. Every comment carries a hidden marker (rule + path + line),
existing markers are read first, and only unseen findings are posted —
so a re-run after a push does not duplicate a thread a reviewer has
already resolved.

The marker holds a rule name, a path and a line number, never a matched
value. Findings arrive pre-redacted from the scanner and are posted as
they arrive: this writes to a PR that is about to be public, so it must
not be the thing that publishes the secret in full.

    python3 evals/check_eval_hygiene.py --format json > findings.json
    python3 scripts/post_hygiene_comments.py --pr 44 --findings findings.json

Needs `gh` authenticated with `pull-requests: write`.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

MARKER = "hygiene-finding"
# @@ -old,count +new,count @@
HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


def gh(*args: str, method: str = "GET", body: dict | None = None) -> object:
    """Call the GitHub API through `gh`, returning parsed JSON."""
    cmd = ["gh", "api", "--method", method, *args]
    if body is not None:
        cmd += ["--input", "-"]
    result = subprocess.run(
        cmd, input=json.dumps(body) if body is not None else None,
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"gh api failed: {' '.join(cmd)}\n{result.stderr.strip()}")
    return json.loads(result.stdout) if result.stdout.strip() else None


def commentable_lines(repo: str, pr: int) -> dict[str, set[int]]:
    """Map path -> line numbers a review comment can anchor to.

    Only lines the diff actually shows: added lines and the context
    around them. GitHub rejects a review comment on any other line, so
    findings elsewhere have to go in the summary instead.
    """
    lines: dict[str, set[int]] = {}
    files = gh(f"repos/{repo}/pulls/{pr}/files", "--paginate") or []
    for entry in files:
        patch = entry.get("patch")
        if not patch:  # binary, or too large for GitHub to render
            continue
        lines[entry["filename"]] = lines_from_patch(patch)
    return lines


def lines_from_patch(patch: str) -> set[int]:
    """New-file line numbers a review comment can anchor to.

    Added and context lines count; removed lines exist only on the LEFT
    side and GitHub rejects a RIGHT-side comment on them. Pure and
    separate from the API call so the self-test can exercise it — an
    off-by-one here silently reroutes findings into the summary, where
    they are far easier to skim past.
    """
    available: set[int] = set()
    new_line = 0
    for raw in patch.split("\n"):
        hunk = HUNK.match(raw)
        if hunk:
            new_line = int(hunk.group(1))
            continue
        if raw.startswith("-"):
            continue
        if raw.startswith(("+", " ")):
            available.add(new_line)
            new_line += 1
    return available


def existing_markers(repo: str, pr: int) -> set[str]:
    """Markers already posted, so a re-run never duplicates a thread."""
    seen: set[str] = set()
    for endpoint in (f"repos/{repo}/pulls/{pr}/comments",
                     f"repos/{repo}/issues/{pr}/comments"):
        for comment in gh(endpoint, "--paginate") or []:
            seen.update(re.findall(rf"<!-- {MARKER}:(.+?) -->", comment.get("body", "")))
    return seen


def marker(finding: dict) -> str:
    """Stable dedupe key: rule, location, AND what was matched.

    The message digest is load-bearing, not decoration. Keyed on
    rule:path:line alone, this sequence loses a finding silently:
    a thread is posted, a reviewer resolves it as a false positive, a
    later push puts a DIFFERENT value at the same rule and line, the
    marker still matches, no thread is posted — and with the old thread
    already resolved, nothing blocks the merge. Folding the (already
    redacted) message in means a different value is a different thread.

    Hashed rather than inlined so the marker stays a fixed, HTML-comment
    safe length. The input is redacted before it ever reaches here, so
    this is not protecting a secret — it is keeping the marker parseable.
    """
    digest = hashlib.sha256(finding["message"].encode("utf-8")).hexdigest()[:8]
    return f"{finding['rule']}:{finding['path']}:{finding['line']}:{digest}"


def body_for(finding: dict) -> str:
    verb = ("**blocks this PR**" if finding["blocking"]
            else "needs a human look before merge")
    action = (
        "This shape does not occur by accident. Treat the value as "
        "**compromised**: rotate or revoke it first, then remove it. "
        "Editing the file is not enough — it is already in the git history."
        if finding["blocking"] else
        "If this is a real identifier, replace it with a placeholder "
        "(`my org`, `10.0.0.[N]`, `[PROJECT]-[NUMBER]`). If it is a false "
        "positive, resolve this thread — that resolution is the record "
        "that somebody actually looked."
    )
    return (
        f"<!-- {MARKER}:{marker(finding)} -->\n"
        f"**Hygiene: `{finding['rule']}`** — {verb}.\n\n"
        f"{finding['message']}\n\n"
        f"{action}\n\n"
        f"<sub>The value is redacted here on purpose: this PR is public. "
        f"See [`evals/HYGIENE.md`](evals/HYGIENE.md).</sub>"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pr", type=int, required=True)
    parser.add_argument("--findings", type=Path, required=True)
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY"))
    parser.add_argument("--commit", default=os.environ.get("GITHUB_SHA"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    if not args.repo:
        print("error: --repo or GITHUB_REPOSITORY required", file=sys.stderr)
        return 2

    report = json.loads(args.findings.read_text(encoding="utf-8"))
    findings = report.get("findings", [])
    if not findings:
        print("hygiene: no findings, nothing to post.")
        return 0

    diff = commentable_lines(args.repo, args.pr)
    seen = existing_markers(args.repo, args.pr)

    inline, summary, skipped = [], [], 0
    for finding in findings:
        if marker(finding) in seen:
            skipped += 1
            continue
        if finding["line"] in diff.get(finding["path"], ()):
            inline.append(finding)
        else:
            summary.append(finding)

    for finding in inline:
        payload = {
            "body": body_for(finding),
            "commit_id": args.commit,
            "path": finding["path"],
            "line": finding["line"],
            "side": "RIGHT",
        }
        if args.dry_run:
            print(f"[dry-run] inline {finding['path']}:{finding['line']} "
                  f"({finding['rule']})")
            continue
        try:
            gh(f"repos/{args.repo}/pulls/{args.pr}/comments",
               method="POST", body=payload)
        except RuntimeError as exc:
            # A line can stop being commentable between the diff read and
            # the post (a force-push mid-run). Degrade to the summary
            # rather than dropping the finding on the floor.
            print(f"note: inline comment failed, moving to summary: {exc}",
                  file=sys.stderr)
            summary.append(finding)

    if summary:
        rows = "\n".join(
            f"| `{f['rule']}` | `{f['path']}` | {f['line']} | {f['message']} |"
            f"<!-- {MARKER}:{marker(f)} -->"
            for f in summary
        )
        body = (
            "### Hygiene findings outside this diff\n\n"
            "These are in files this PR does not change, so GitHub cannot "
            "anchor a review comment to them. They are pre-existing and "
            "worth a look, but they are not this PR's doing.\n\n"
            "| Rule | File | Line | Detail |\n| --- | --- | --- | --- |\n"
            f"{rows}\n\n"
            "<sub>Values are redacted. See "
            "[`evals/HYGIENE.md`](evals/HYGIENE.md).</sub>"
        )
        if args.dry_run:
            print(f"[dry-run] summary comment with {len(summary)} finding(s)")
        else:
            gh(f"repos/{args.repo}/issues/{args.pr}/comments",
               method="POST", body={"body": body})

    print(f"hygiene: {len(inline)} inline thread(s), {len(summary)} in summary, "
          f"{skipped} already posted.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
