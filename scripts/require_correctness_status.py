#!/usr/bin/env python3
"""Require a green `skills-evals/correctness` status before a skill change merges.

APPSEC-3967 / TM-008. The eval harness lives in wandb/skills-evals and cannot
run here — it needs Docker, Harbor, the mock environments and an Anthropic key,
none of which belong in this repo's PR context. So the verdict arrives as a
COMMIT STATUS posted onto the PR's head SHA by that repo's `correctness-gate`
workflow, and this script is the thing that refuses to let a PR merge without
one.

Without this check the gate is advisory: it runs, it posts, and nothing reads
it. That is the exact shape of the finding — "the evals exist and are
well-designed; the gap is purely that nothing forces them to run before a
change ships".

WHAT IT CHECKS, IN ORDER

  1. RELEVANCE. A PR that touches no skill has nothing to gate, and demanding a
     dispatch for a typo fix would train everyone to route around the check.
     The rule mirrors skills-evals' own `skill_from_changed_path` /
     FANOUT_PREFIXES exactly, so "this PR needs a correctness run" here and
     "the gate would have something to run" there cannot drift apart.

  2. PRESENCE. A status for context `skills-evals/correctness` must exist on
     the head SHA. Absent is a FAILURE with dispatch instructions, never a
     skip: "no verdict" and "a good verdict" must not look the same.

  3. STATE. It must be `success`. `pending` fails too — a gate still running
     has not passed.

  4. AUTHORSHIP. The status must have been posted by an allowed identity.
     THIS IS THE PART THAT MAKES IT A CONTROL. A commit status is just an API
     POST; anyone with write access to this repo could send a green
     `skills-evals/correctness` and walk straight past the gate. Checking who
     posted it is what turns a formality into an enforcement point. The
     allowlist is a repo VARIABLE (not a secret — it is not sensitive, and it
     should be reviewable), and an unset allowlist is a hard failure rather
     than "allow anyone".

FAIL-CLOSED EVERYWHERE. Every uncertainty — no token, API error, unparseable
response, malformed status, missing allowlist — exits non-zero. A check that
cannot tell whether the gate passed has not established that it passed.

Stdlib only, so the job needs no pip install.

Usage (CI — the changed-file list comes from the API, so no PR checkout is
needed and none should exist: the workflow runs the BASE copy of this file):
  python3 scripts/require_correctness_status.py \\
      --repo coreweave/skills --sha <head-sha> --pr-number <n>

Usage (local / tests — supply the paths yourself):
  python3 scripts/require_correctness_status.py \\
      --repo coreweave/skills --sha <head-sha> \\
      --changed-paths-file <file with one path per line>

Exit codes: 0 satisfied (or not applicable), 1 gate failure, 2 usage error.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

STATUS_CONTEXT = "skills-evals/correctness"
API = os.environ.get("GITHUB_API_URL", "https://api.github.com")

# Mirrors wandb/skills-evals scripts/correctness_gate.py. Keep in step: if that
# file learns a new shape, this one has to as well, or a PR that the gate WOULD
# have run will slip through here as "nothing to gate".
FANOUT_PREFIXES = ("_snippets/", "_shared-scripts/", "standalone-skills.yaml", "build.py")

DISPATCH_HINT = (
    "Run the gate: wandb/skills-evals -> Actions -> correctness-gate -> "
    "Run workflow, with skills_sha=%s (and pr_number for a nicer summary). "
    "It posts the status back onto this SHA; re-run this check afterwards."
)


def annotate(kind: str, message: str, title: str = "correctness status") -> None:
    encoded = message.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    print(f"::{kind} title={title}::{encoded}")


# ── relevance ───────────────────────────────────────────────────────────────


def skill_from_changed_path(path: str) -> str | None:
    """The skill a changed path belongs to, or None.

    Same three shapes skills-evals recognises: `skills/<name>/...` (source,
    which covers a manifest edit), `dist/<name>/...` (the built artifact the
    harness mounts), and `evals/standalone/<name>.evals.json`. `_`-prefixed
    directories are templates that build.py skips, so we skip them too.
    """
    parts = path.split("/")
    if len(parts) >= 3 and parts[0] in ("skills", "dist") and not parts[1].startswith("_"):
        return parts[1]
    if len(parts) == 3 and parts[:2] == ["evals", "standalone"] and parts[2].endswith(".evals.json"):
        name = parts[2][: -len(".evals.json")]
        if name and not name.startswith("_"):
            return name
    return None


def needs_correctness(paths) -> tuple[bool, list[str], list[str]]:
    """(is a correctness run required, skills touched, fan-out paths).

    Fan-out paths (shared snippets, the build script, the standalone manifest)
    name no single skill but can change every one of them, so they REQUIRE a
    run rather than exempting the PR.
    """
    skills, fanout = set(), []
    for p in paths:
        name = skill_from_changed_path(p)
        if name:
            skills.add(name)
        elif p.startswith(FANOUT_PREFIXES):
            fanout.append(p)
    return bool(skills or fanout), sorted(skills), sorted(fanout)


# ── the status verdict ──────────────────────────────────────────────────────


def latest_status(payload, context: str = STATUS_CONTEXT):
    """The most recent status for `context`, or None.

    GitHub returns the list newest-first and keeps every historical post, so a
    re-dispatch that went from failure to success leaves both on the SHA. Only
    the newest one is the current verdict — but note this cuts both ways, which
    is why authorship is checked separately: a forged green posted after a real
    red would be the newest.
    """
    if not isinstance(payload, list):
        return None
    for entry in payload:
        if isinstance(entry, dict) and entry.get("context") == context:
            return entry
    return None


def check_status(payload, allowed_creators, sha: str) -> list[str]:
    """Problems with the correctness status on this SHA. Empty means satisfied."""
    if not allowed_creators:
        return ["no allowed status creators configured — refusing to accept any "
                "status. Set the CORRECTNESS_STATUS_CREATORS repository variable "
                "to the login(s) that wandb/skills-evals posts as; an empty "
                "allowlist would let anyone with write access forge a pass."]

    status = latest_status(payload)
    if status is None:
        return [f"no {STATUS_CONTEXT!r} status on {sha} — the correctness evals "
                "have not been run against this commit. " + (DISPATCH_HINT % sha)]

    problems = []
    creator = (status.get("creator") or {}).get("login")
    if creator not in allowed_creators:
        problems.append(
            f"{STATUS_CONTEXT!r} status on {sha} was posted by {creator!r}, which is "
            f"not in the allowlist ({', '.join(sorted(allowed_creators))}). A commit "
            "status is an unauthenticated-looking API POST from this repo's point of "
            "view, so an unexpected author is treated as a forgery attempt, not a pass.")

    state = status.get("state")
    if state == "success":
        pass
    elif state == "pending":
        problems.append(f"{STATUS_CONTEXT!r} is still PENDING on {sha} — the gate is "
                        "running (or died without reporting). Wait for it, or re-dispatch.")
    elif state in ("failure", "error"):
        problems.append(
            f"{STATUS_CONTEXT!r} is {state.upper()} on {sha}: "
            f"{status.get('description') or 'no description'}. "
            f"Evidence: {status.get('target_url') or 'no run URL recorded'}")
    else:
        problems.append(f"{STATUS_CONTEXT!r} on {sha} has an unrecognised state "
                        f"{state!r} — refusing to interpret it as a pass.")
    return problems


# ── I/O ─────────────────────────────────────────────────────────────────────


def fetch_statuses(repo: str, sha: str, token: str):
    """GET the commit's statuses. Raises RuntimeError on any doubt."""
    req = urllib.request.Request(
        f"{API}/repos/{repo}/commits/{sha}/statuses?per_page=100",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "require-correctness-status",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "replace")[:300]
        except Exception:  # noqa: BLE001 - reporting must not mask the HTTP error
            pass
        raise RuntimeError(
            f"GitHub returned {exc.code} listing statuses for {sha}. "
            "The job needs `statuses: read`. " + detail) from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"could not read statuses for {sha}: {exc}") from exc


def read_changed_paths(path: str) -> list[str]:
    with open(path, encoding="utf-8") as fh:
        return [line.strip() for line in fh if line.strip()]


# GitHub caps this endpoint at 3000 files. A PR that large is not something we
# can enumerate, so it is treated as REQUIRING a run rather than exempted —
# "too big to check" must never mean "allowed through".
FILES_CAP = 3000


def fetch_changed_paths(repo: str, pr_number: str, token: str) -> tuple[list[str], bool]:
    """(paths, truncated) for a PR, straight from the API.

    Deliberately NOT a git diff of a checkout: this job must not need the PR's
    working tree at all (see the workflow header — it runs the BASE copy of this
    script precisely so a PR cannot edit its own gate), and a base-only checkout
    has no credential to fetch the PR ref with.
    """
    paths: list[str] = []
    for page in range(1, (FILES_CAP // 100) + 2):
        req = urllib.request.Request(
            f"{API}/repos/{repo}/pulls/{pr_number}/files?per_page=100&page={page}",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "require-correctness-status",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                batch = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.HTTPError, urllib.error.URLError,
                TimeoutError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"could not list files for PR #{pr_number}: {exc}") from exc
        if not isinstance(batch, list):
            raise RuntimeError(f"unexpected files payload for PR #{pr_number}")
        paths.extend(f.get("filename", "") for f in batch if isinstance(f, dict))
        if len(batch) < 100:
            return paths, False
    return paths, True


def parse_creators(raw: str | None) -> set[str]:
    return {p.strip() for p in (raw or "").replace("\n", ",").split(",") if p.strip()}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", required=True, help="owner/name")
    ap.add_argument("--sha", required=True, help="the PR's HEAD sha (not the merge sha)")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--pr-number", help="list changed files from the API (the CI path)")
    src.add_argument("--changed-paths-file", help="one path per line (tests, local use)")
    ap.add_argument("--allowed-creators", default=os.environ.get("ALLOWED_STATUS_CREATORS"))
    args = ap.parse_args(argv)

    token = os.environ.get("GITHUB_TOKEN", "")
    truncated = False
    if args.changed_paths_file:
        try:
            paths = read_changed_paths(args.changed_paths_file)
        except OSError as exc:
            annotate("error", f"cannot read the changed-paths file: {exc}")
            return 2
    else:
        if not token:
            annotate("error", "GITHUB_TOKEN is not set — cannot list the PR's files, "
                              "so this check cannot establish that the gate passed.")
            return 1
        try:
            paths, truncated = fetch_changed_paths(args.repo, args.pr_number, token)
        except RuntimeError as exc:
            annotate("error", str(exc))
            return 1

    required, skills, fanout = needs_correctness(paths)
    if truncated:
        annotate("warning", f"PR #{args.pr_number} changes more files than the API will "
                            "list; requiring a correctness run rather than assuming none "
                            "of the unlisted files touch a skill.")
        required = True
    if not required:
        print(f"No skill sources changed in {len(paths)} path(s) — "
              "a correctness run is not required for this PR.")
        annotate("notice", "no skill sources changed; correctness gate not required")
        return 0

    what = ", ".join(skills) if skills else f"shared sources ({', '.join(fanout)})"
    print(f"Skill changes present ({what}) — a green {STATUS_CONTEXT} status is required.")

    if not token:
        annotate("error", "GITHUB_TOKEN is not set — cannot read commit statuses, "
                          "so this check cannot establish that the gate passed.")
        return 1

    try:
        payload = fetch_statuses(args.repo, args.sha, token)
    except RuntimeError as exc:
        annotate("error", str(exc))
        return 1

    problems = check_status(payload, parse_creators(args.allowed_creators), args.sha)
    if problems:
        for p in problems:
            annotate("error", p)
        print(f"FAIL: {len(problems)} problem(s) with the correctness status.")
        return 1

    status = latest_status(payload)
    print(f"PASS: {STATUS_CONTEXT} is success on {args.sha} "
          f"(by {(status.get('creator') or {}).get('login')}) — "
          f"{status.get('description') or 'no description'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
