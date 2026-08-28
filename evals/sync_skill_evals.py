#!/usr/bin/env python3
"""Derive the per-skill eval seeds from the skills-evals harness (APPSEC-3967).

`validate_skill_evals.py` lints the *shape* of the seeds. It cannot lint
their *fidelity*: the fields that carry the signal — `expect`,
`rubric_criteria`, `user_turns` — are copies of what
wandb/skills-evals asserts, and that repo is private and is not checked
out in this repo's CI. A copy that has drifted still passes the shape
lint, and a drifted seed is worse than no seed at all: it reads as
coverage while gating something the harness no longer checks.

So the copies are not hand-maintained. This script regenerates them from
a local skills-evals checkout, and in its default mode fails when the
committed files disagree with it:

    python3 evals/sync_skill_evals.py                 # --check (default)
    python3 evals/sync_skill_evals.py --write         # regenerate in place
    python3 evals/sync_skill_evals.py --harness ~/src/skills-evals --ref master

`--check` is the CI-shaped half. It is NOT wired into a workflow here,
because the harness is not available to this repo's runners — wiring it
needs either a scoped cross-repo token or a vendored copy of the answer
keys, both of which are their own decision. Until then it is a local and
nightly-job tool, and the drift it catches is real: at the time this
script was written the committed seeds disagreed with the harness in 18
places, including a blocking pushback case that had lost the one
deterministic assertion that made it a pushback case.

## What is copied, and from where

Per scenario, from `<scenario>/tests/scenario_answer_key.json`:

    expect, rubric_criteria, user_turns   verbatim
    user_request                          verbatim; when the key omits it,
                                          the scenario's instruction.md is
                                          used instead — the same fallback
                                          scripts/rejudge.py applies

`user_request` in an answer key is deliberately NOT the prompt the agent
receives: scripts/lint_scenarios.py errors (`intent-copy`) when it is
more than 85% similar to instruction.md. It is the intent summary, and
it is what a field named `user_request` in the key means, so it is what
is copied here.

## What is derived

    tier       "mock-judged" when the answer key declares a non-empty
               `rubric_criteria`, else "mock". That is the harness's own
               definition (tasks/scaffold.py::_is_judged reads that list
               and nothing else to decide whether judge.py is vendored
               into the scenario). The `-judged` directory suffix is a
               naming convention and is not consulted.
    id         the scenario directory minus its `-mock`/`-mock-judged`
               suffix; when a skill has both arms of the same base name,
               the judged one keeps a `-judged` marker. An id already
               committed for that scenario path wins, so renaming a case
               by hand is not undone by a sync.
    gates      outcome 1.0 for every scripted tier, plus tq_opus and
               tq_sonnet at 0.7 for the judged tiers.
    blocking   preserved from the committed file. A scenario not yet
               committed defaults to blocking iff its directory name
               starts with `pushback-` — those are the cases APPSEC-3967
               names as must-block. Overriding that is a hand edit the
               sync then preserves.

Real-tier scenarios (`*-real*`) provision live infrastructure and are
manual-only, so they are never emitted. `_`-prefixed directories are
templates and are skipped, mirroring build.py.

Stdlib only. The harness is read through `git show <ref>:<path>` so a
dirty or feature-branched checkout cannot leak into the seeds; pass
`--ref HEAD` deliberately if that is what you want.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILLS_DIR = REPO_ROOT / "skills"
STANDALONE_EVALS_DIR = REPO_ROOT / "evals" / "standalone"

DEFAULT_REF = "origin/master"
HARNESS_REPO = "wandb/skills-evals"

RUNNER = ("./run.sh <mode> <scenario> (Harbor; mode 'judged' for "
          "mock-judged cases, 'claude' for plain mock — see run.sh usage)")

JUDGE_GATES = {"tq_opus": 0.7, "tq_sonnet": 0.7}

# Fields copied verbatim out of the answer key. Order matters: it is the
# key order of the emitted case.
COPIED = ("user_request", "user_turns", "expect", "rubric_criteria")


class HarnessError(RuntimeError):
    pass


def git(harness: Path, *args: str) -> str:
    proc = subprocess.run(["git", "-C", str(harness), *args],
                          capture_output=True, text=True)
    if proc.returncode != 0:
        raise HarnessError(f"git {' '.join(args)} failed: "
                           f"{proc.stderr.strip() or proc.stdout.strip()}")
    return proc.stdout


def resolve_harness(explicit: str | None) -> Path:
    """Locate the skills-evals checkout: --harness, then $SKILLS_EVALS_REPO,
    then the conventional sibling directory."""
    candidates = []
    if explicit:
        candidates.append(Path(explicit).expanduser())
    env = os.environ.get("SKILLS_EVALS_REPO")
    if env:
        candidates.append(Path(env).expanduser())
    candidates.append(REPO_ROOT.parent / "skills-evals")

    for candidate in candidates:
        if (candidate / ".git").exists():
            return candidate.resolve()
    raise HarnessError(
        f"no {HARNESS_REPO} checkout found (looked at: "
        f"{', '.join(str(c) for c in candidates)}) — pass --harness "
        f"<path>, or set SKILLS_EVALS_REPO"
    )


def read_json(harness: Path, ref: str, path: str):
    return json.loads(git(harness, "show", f"{ref}:{path}"))


def discover_scenarios(harness: Path, ref: str) -> dict[str, list[str]]:
    """{skill: [scenario path, ...]} for every mock-tier scenario on `ref`."""
    listing = git(harness, "ls-tree", "-r", "--name-only", ref, "tasks/")
    by_skill: dict[str, list[str]] = {}
    for line in listing.splitlines():
        if not line.endswith("/tests/scenario_answer_key.json"):
            continue
        scenario = line[: -len("/tests/scenario_answer_key.json")]
        parts = scenario.split("/")
        if len(parts) != 4:
            continue
        _, _, skill, name = parts
        # Mirror build.py on `_`-prefixed templates, and skip the real
        # tier — those provision live infrastructure and are manual-only.
        if name.startswith("_") or skill.startswith("_") or "-real" in name:
            continue
        by_skill.setdefault(skill, []).append(scenario)
    for scenarios in by_skill.values():
        scenarios.sort()
    return by_skill


def derive_ids(scenarios: list[str]) -> dict[str, str]:
    """{scenario path: case id}. Strips the tier suffix; when a skill ships
    both arms of one base name, the judged arm keeps a `-judged` marker so
    the two ids stay distinct (e.g. happy / happy-judged)."""
    bases: dict[str, list[str]] = {}
    for scenario in scenarios:
        name = scenario.split("/")[-1]
        if name.endswith("-mock-judged"):
            base = name[: -len("-mock-judged")]
        elif name.endswith("-mock"):
            base = name[: -len("-mock")]
        else:
            base = name
        bases.setdefault(base, []).append(scenario)

    ids: dict[str, str] = {}
    for base, group in bases.items():
        for scenario in group:
            judged_dir = scenario.endswith("-mock-judged")
            if len(group) > 1 and judged_dir:
                ids[scenario] = f"{base}-judged"
            else:
                ids[scenario] = base
    return ids


def build_case(harness: Path, ref: str, scenario: str, case_id: str,
               blocking: bool) -> dict:
    key = read_json(harness, ref, f"{scenario}/tests/scenario_answer_key.json")

    rubric = key.get("rubric_criteria") or []
    judged = bool(rubric)
    tier = "mock-judged" if judged else "mock"

    gates = {"outcome": 1.0}
    if judged:
        gates.update(JUDGE_GATES)

    case = {
        "id": case_id,
        "scenario": scenario,
        "tier": tier,
        "blocking": blocking,
    }
    for field in COPIED:
        if field == "user_request":
            value = key.get("user_request")
            if not value:
                # Same fallback scripts/rejudge.py applies when a key
                # predates the field.
                value = git(harness, "show",
                            f"{ref}:{scenario}/instruction.md").strip()
            case["user_request"] = value
            continue
        if field == "rubric_criteria":
            # Forbidden on the deterministic tiers — the validator rejects
            # the key at all there, empty or not.
            if judged:
                case["rubric_criteria"] = rubric
            continue
        if field in key:
            case[field] = key[field]

    case["reward_gates"] = gates
    return case


def target_for(skill: str, standalone_names: set[str]) -> Path | None:
    if (SKILLS_DIR / skill / "skill.yaml").is_file():
        return SKILLS_DIR / skill / "evals" / "evals.json"
    if skill in standalone_names:
        return STANDALONE_EVALS_DIR / f"{skill}.evals.json"
    return None


NOTES = (
    "Generated by evals/sync_skill_evals.py from the {repo} answer keys — "
    "do not hand-edit `user_request`, `user_turns`, `expect` or "
    "`rubric_criteria`; re-run the sync instead, and fix the answer key "
    "upstream if a value is wrong. Cases cover every mock-tier Harbor "
    "scenario for this skill. Real-tier scenarios (*-real*) exist upstream "
    "but are manual-only (they provision real infrastructure) and are "
    "intentionally not listed. A case's tier reflects whether the scenario "
    "actually runs the LLM judges — the answer key declaring a non-empty "
    "rubric_criteria is what makes a scenario judged, not the directory's "
    "*-judged suffix, so a few judged scenarios carry a plain -mock name. "
    "`blocking` is hand-maintained and preserved across syncs."
)


def build_document(skill: str, cases: list[dict]) -> dict:
    return {
        "schema_version": 1,
        "skill": skill,
        "harness": {"repo": HARNESS_REPO, "runner": RUNNER},
        "notes": NOTES.format(repo=HARNESS_REPO),
        "cases": cases,
    }


def load_existing(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def standalone_skill_names() -> set[str]:
    """Reuse the validator's manifest scanner so the two scripts cannot
    disagree about what a standalone skill is."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from validate_skill_evals import (  # noqa: E402
        standalone_skill_names as names,
    )
    return names()


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__.split("\n\n")[0],
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--harness", help=f"path to a {HARNESS_REPO} checkout "
                                          f"(default: $SKILLS_EVALS_REPO, "
                                          f"then ../skills-evals)")
    parser.add_argument("--ref", default=DEFAULT_REF,
                        help=f"git ref to read the harness at "
                             f"(default: {DEFAULT_REF})")
    parser.add_argument("--write", action="store_true",
                        help="rewrite the seed files (default: check only)")
    args = parser.parse_args()

    harness = resolve_harness(args.harness)
    git(harness, "rev-parse", "--verify", f"{args.ref}^{{commit}}")
    print(f"harness: {harness} @ {args.ref} "
          f"({git(harness, 'rev-parse', '--short', args.ref).strip()})")

    standalone_names = standalone_skill_names()
    by_skill = discover_scenarios(harness, args.ref)
    if not by_skill:
        print(f"error: no scenarios found under tasks/ at {args.ref}",
              file=sys.stderr)
        return 2

    drifted: list[str] = []
    unmapped: list[str] = []

    for skill, scenarios in sorted(by_skill.items()):
        path = target_for(skill, standalone_names)
        if path is None:
            unmapped.append(skill)
            continue

        existing = load_existing(path)
        existing_by_scenario = {c.get("scenario"): c
                                for c in existing.get("cases", [])
                                if isinstance(c, dict)}
        ids = derive_ids(scenarios)

        cases = []
        for scenario in scenarios:
            prior = existing_by_scenario.get(scenario, {})
            case_id = prior.get("id") or ids[scenario]
            blocking = prior.get("blocking")
            if not isinstance(blocking, bool):
                blocking = scenario.split("/")[-1].startswith("pushback-")
            cases.append(build_case(harness, args.ref, scenario, case_id,
                                    blocking))

        doc = build_document(skill, cases)
        rendered = json.dumps(doc, indent=2, ensure_ascii=False) + "\n"
        rel = path.relative_to(REPO_ROOT).as_posix()

        if path.is_file() and path.read_text(encoding="utf-8") == rendered:
            print(f"  ✓ {rel} ({len(cases)} case(s))")
            continue

        drifted.append(rel)
        # `None` is an unscripted case's scenario, not a stale path — it is
        # dropped when the harness grows a real scenario for that skill,
        # which is a replacement rather than something gone missing.
        stale = sorted(set(existing_by_scenario) - set(scenarios) - {None})
        added = sorted(set(scenarios) - set(existing_by_scenario))
        detail = []
        if added:
            detail.append(f"{len(added)} scenario(s) not seeded")
        if stale:
            detail.append(f"{len(stale)} seeded scenario(s) not in the "
                          f"harness")
        suffix = f" — {'; '.join(detail)}" if detail else " — content differs"
        if args.write:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(rendered, encoding="utf-8")
            print(f"  ↻ {rel} ({len(cases)} case(s)){suffix}")
        else:
            print(f"  ✗ {rel} ({len(cases)} case(s)){suffix}")
            for scenario in added:
                print(f"      + {scenario}")
            for scenario in stale:
                print(f"      - {scenario}")

    if unmapped:
        # Not an error: the harness may carry scenarios for a skill this
        # repo has not shipped yet. Loud, though — the alternative is a
        # scenario suite nobody notices is unrepresented.
        print(f"\nnote: no eval file target for {len(unmapped)} harness "
              f"skill(s) not shipped by this repo: {', '.join(unmapped)}")

    if not drifted:
        print("\nseeds are in sync with the harness.")
        return 0
    if args.write:
        print(f"\nrewrote {len(drifted)} file(s). Review the diff, then run "
              f"python3 evals/validate_skill_evals.py")
        return 0
    print(f"\n{len(drifted)} file(s) drifted from the harness. Re-run with "
          f"--write to regenerate.", file=sys.stderr)
    for rel in drifted:
        print(f"::error file={rel}::eval seed has drifted from the "
              f"{HARNESS_REPO} answer keys — run "
              f"`python3 evals/sync_skill_evals.py --write`", file=sys.stderr)
    return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except HarnessError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(2)
