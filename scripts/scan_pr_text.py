#!/usr/bin/env python3
"""Scan a PR's text surfaces, applying gate-vs-alarm semantics.

WHY THIS IS A SCRIPT AND NOT SHELL
----------------------------------
This logic used to live inside a ``run:`` block in
``.github/workflows/pr-text-hygiene.yml``: a shell function branching on
``gate`` vs ``alarm``, piping through ``tee``, and promoting a notice
with ``grep -q '^::warning::'``. It was the only real decision-making in
the hygiene suite with no test coverage at all, and its failure mode is
the bad kind — a typo in the branch, or a ``grep`` pattern that stops
matching, silently downgrades the PR body from a *gate* to an *alarm*.
Nothing goes red. The check keeps reporting success while enforcing
nothing.

As a script it is importable, so ``scripts/check_eval_hygiene_selftest.py``
can plant text in each surface and assert the exit code and the
annotation level, the same way every other rule in this suite is pinned.

THE SEMANTICS IT ENFORCES
-------------------------
- **The PR body is a GATE.** A finding fails the check. The author edits
  the description and it goes green — a real, clearable gate.
- **Comments and review bodies are ALARMS.** A finding is reported as a
  ``::warning`` and does NOT fail. A comment was public the moment it
  posted, so failing could never be cleared, and a check that is
  permanently red is one people learn to ignore — which costs more than
  the signal is worth. It also stops a review conversation *about* these
  rules (quoting a token shape, an example address) from red-gating the
  PR that hardens them.

An alarm still means *handle a disclosure*: rotate the credential, scrub
what you can, and remember GitHub keeps edit history that anyone with
repo access can read.

Paste-residue rules are skipped on every surface — see
``PASTE_RESIDUE_RULES`` in the scanner. "Did this arrive by paste?" is a
sharp question about a committed corpus file and a meaningless one about
a comment somebody typed into a web box.

    python3 scripts/scan_pr_text.py --body body.txt --comments c.txt
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCANNER = REPO_ROOT / "evals" / "check_eval_hygiene.py"

_spec = importlib.util.spec_from_file_location("check_eval_hygiene", SCANNER)
assert _spec and _spec.loader
hygiene = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hygiene)

# (flag, label suffix, is_gate). The body is the only gate; see above.
SURFACES = (
    ("body", "body", True),
    ("comments", "comments", False),
    ("reviews", "review bodies", False),
    ("review_comments", "review comments", False),
)


def scan_surfaces(paths: dict[str, Path], pr: str, allowlist, github: bool
                  ) -> tuple[int, int]:
    """Return (gate_findings, alarm_findings) across every given surface."""
    gated = alarmed = 0
    for attr, suffix, is_gate in SURFACES:
        path = paths.get(attr)
        if path is None:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            # A surface the workflow did not fetch is not an error; a
            # PR legitimately has no reviews yet.
            continue
        except UnicodeDecodeError:
            # Fail closed, exactly as the file pass does: text we cannot
            # decode is text we cannot verify.
            print(f"::error::{pr} {suffix}: not valid UTF-8, cannot verify",
                  file=sys.stderr)
            gated += 1
            continue
        rc = hygiene.scan_stdin(text, f"{pr} {suffix}", allowlist, github,
                                warn_only=not is_gate)
        if is_gate:
            gated += rc  # scan_stdin returns 1 on a gated finding
        else:
            # warn_only always returns 0, so the exit code cannot tell an
            # alarm from a clean surface. Count directly.
            alarmed += _count_findings(text, f"{pr} {suffix}", allowlist)
    return gated, alarmed


def _count_findings(text: str, label: str, allowlist) -> int:
    """How many findings a surface produced, independent of exit code.

    ``scan_stdin`` deliberately returns 0 in warn-only mode, so the exit
    code cannot tell an alarm from a clean surface. Counting directly
    keeps the promotion honest — the old shell inferred this by grepping
    its own output for ``^::warning::``, which broke the moment the
    annotation format changed.
    """
    return sum(
        1
        for lineno, line in enumerate(text.splitlines(), 1)
        for finding in hygiene.scan_line(
            Path(label), lineno, line,
            skip_rules=frozenset(hygiene.PASTE_RESIDUE_RULES))
        if not hygiene.is_allowed(line, finding, allowlist)
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Scan PR text surfaces.")
    for attr, suffix, _ in SURFACES:
        parser.add_argument(f"--{attr.replace('_', '-')}", type=Path,
                            help=f"file holding the PR's {suffix}")
    parser.add_argument("--pr", default="PR", help="label prefix, e.g. 'PR #44'")
    parser.add_argument("--allowlist", type=Path, default=None)
    args = parser.parse_args(argv)

    github = bool(os.environ.get("GITHUB_ACTIONS"))
    try:
        allowlist = hygiene.load_allowlist(
            args.allowlist or hygiene.DEFAULT_ALLOWLIST)
    except hygiene.ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2

    paths = {attr: getattr(args, attr) for attr, _, _ in SURFACES}
    gated, alarmed = scan_surfaces(paths, args.pr, allowlist, github)

    if alarmed:
        print(
            "::warning::A comment or review body on this PR matched a hygiene "
            "rule. That text is ALREADY PUBLIC, so this is an alarm, not a "
            "gate: if it is a real identifier or credential, handle it as a "
            "disclosure per evals/HYGIENE.md (rotate first, then scrub what "
            "you can, remembering GitHub keeps edit history). If it is review "
            "discussion quoting a shape on purpose, no action is needed."
        )
    if gated:
        print("::error::The PR BODY carries something the hygiene rules flag. "
              "Edit the description to clear this check.")
        return 1
    if alarmed:
        print(f"::notice::{alarmed} finding(s) on already-published text are "
              "reported as warnings above; they do not block this PR.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
