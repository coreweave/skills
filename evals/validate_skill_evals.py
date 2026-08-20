#!/usr/bin/env python3
"""Validate per-skill correctness eval seeds (APPSEC-3967).

Checks every

    skills/<name>/evals/evals.json          (workflow skills)
    evals/standalone/<name>.evals.json      (standalone/snippet skills)

against the schema set by the first seeded files (see
skills/cw-create-cluster/evals/evals.json for the template):

    {
      "schema_version": 1,
      "skill": "<must match the directory / filename>",
      "harness": {"repo": "...", "runner": "..."},
      "notes": "...",                       # optional
      "cases": [
        {
          "id": "<unique within the file>",
          "scenario": "tasks/<family>/<skill>/<scenario>" | null,
                                            # null only for tier "unscripted"
          "tier": "mock" | "mock-judged" | "real" | "real-judged" | "unscripted",
          "blocking": true | false,
          "user_request": "...",
          "user_turns": {...},              # optional (multiturn scenarios;
                                            # verbatim harness reply rules)
          "expect": [...],                  # non-empty unless "unscripted"
          "rubric_criteria": [{"key": "...", "desc": "..."}],  # optional
          "reward_gates": {"outcome": 1.0, ...}  # required unless unscripted;
                                            # keys from GATE_KEYS below
        }
      ]
    }

Skill names are validated against the same sources build.py reads, so the
two definitions of "what is a skill" cannot drift:

  - a workflow skill's evals.json must sit inside a real skill source dir
    (skills/<name>/ with a skill.yaml; `_`-prefixed dirs are templates and
    are skipped, mirroring build.py);
  - a standalone eval file's name must match a `frontmatter.name` declared
    in standalone-skills.yaml — the manifest build.py and
    scripts/check_plugin_parity.py already read.

Scenario paths point into the wandb/skills-evals repo, which is not
checked out in this repo's CI — their existence CANNOT be verified here.
This script only lints their shape: exactly
`tasks/<family>/<skill>/<scenario>` with the skill segment matching the
file's skill (a copied case pointing at another skill's scenario is the
likeliest editing mistake). Cross-repo existence is enforced by the
skills-evals harness itself when the suite runs.

Unknown extra keys are permitted (forward compatibility) — required
fields, types, and value ranges are what is enforced.

Stdlib only — no dependencies (standalone-skills.yaml is scanned with a
narrow line parser rather than PyYAML), so CI needs nothing beyond the
runner's python3.

Exit codes:
    0 — all eval files valid
    1 — validation failures (reported per file; all files are checked)
    2 — internal/usage error (no eval files found, unreadable tree, ...)

Failures are emitted as `::error file=<path>::<message>` workflow
annotations on stderr, unconditionally — harmless locally, same
convention as scripts/check_plugin_parity.py.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILLS_DIR = REPO_ROOT / "skills"
STANDALONE_EVALS_DIR = REPO_ROOT / "evals" / "standalone"
STANDALONE_MANIFEST = REPO_ROOT / "standalone-skills.yaml"

TIERS = {"mock", "mock-judged", "real", "real-judged", "unscripted"}

# Reward keys the skills-evals harness emits (tests/test.sh: outcome and
# call_valid from the deterministic verifier, tq_opus/tq_sonnet from the
# LLM judges). A typoed key would gate nothing and stay green forever, so
# unknown keys are errors — extend this set when the harness grows a new
# reward, in the same PR that starts gating on it.
GATE_KEYS = {"outcome", "call_valid", "tq_opus", "tq_sonnet"}

# Exactly tasks/<family>/<skill>/<scenario>. A segment starts with an
# alphanumeric (so `..` and dotfiles can't appear) — traversal-safe.
_SEG = r"[A-Za-z0-9][A-Za-z0-9._-]*"
SCENARIO_RE = re.compile(rf"tasks/{_SEG}/{_SEG}/{_SEG}\Z")

TYPE_NAMES = {
    int: "an integer",
    str: "a string",
    dict: "an object",
    list: "an array",
    bool: "a boolean",
}


def _rel(path: Path) -> str:
    """Repo-relative POSIX path for messages and `::error file=` annotations
    (same helper as build.py — GitHub only matches forward-slash paths)."""
    try:
        return path.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def _escape_annotation(text: str) -> str:
    """Escape a workflow-command message: GitHub percent-decodes annotation
    data, so a bare `%` is eaten and a raw newline/CR ends the ::error
    command and lets message content be parsed as a fresh workflow command
    (command injection). `%` must be escaped first."""
    return (text.replace("%", "%25")
                .replace("\r", "%0D")
                .replace("\n", "%0A"))


def _no_dup_keys(pairs):
    """json object_pairs_hook: reject duplicate keys instead of silently
    keeping the last one — a merge-conflict artifact like two `"cases":`
    arrays would otherwise shrink the suite with no signal."""
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError(f"duplicate JSON key {key!r} — merge-conflict "
                             f"artifact? json keeps only the last duplicate")
        obj[key] = value
    return obj


def standalone_skill_names() -> set[str]:
    """`frontmatter.name` values declared in standalone-skills.yaml.

    Same manifest build.py and scripts/check_plugin_parity.py read — but
    parsed with a narrow stdlib scanner instead of PyYAML so this script
    stays dependency-free. The manifest's shape is fixed (top-level entry,
    2-space `frontmatter:`, 4-space `name:`), and this reads exactly that:

        <entry-key>:
          frontmatter:
            name: <skill-name>
    """
    names: set[str] = set()
    if not STANDALONE_MANIFEST.is_file():
        return names
    in_frontmatter = False
    for line in STANDALONE_MANIFEST.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" "))
        if indent == 2 and line.strip() == "frontmatter:":
            in_frontmatter = True
            continue
        if indent <= 2:
            in_frontmatter = False
            continue
        if in_frontmatter and indent == 4:
            match = re.match(r"^    name:\s*(.+?)\s*$", line)
            if match:
                names.add(match.group(1).strip("'\""))
    return names


class FileChecker:
    def __init__(self, relpath: str):
        self.relpath = relpath
        self.errors: list[str] = []

    def error(self, message: str) -> None:
        self.errors.append(message)

    # -- typed field helpers -------------------------------------------------

    def require(self, obj: dict, where: str, field: str, typ):
        """Return obj[field] if present and of type `typ`, else record an
        error and return None. NOTE: passing bool for an int field is an
        error (bool is checked explicitly)."""
        type_name = TYPE_NAMES[typ]
        if field not in obj:
            self.error(f"{where}: missing required field '{field}'")
            return None
        value = obj[field]
        if typ is not bool and isinstance(value, bool):
            self.error(f"{where}: field '{field}' must be {type_name}, "
                       f"got bool")
            return None
        if not isinstance(value, typ):
            self.error(f"{where}: field '{field}' must be {type_name}, "
                       f"got {type(value).__name__}")
            return None
        return value

    def require_nonempty_str(self, obj: dict, where: str, field: str):
        value = self.require(obj, where, field, str)
        if value is not None and not value.strip():
            self.error(f"{where}: field '{field}' must be a non-empty string")
            return None
        return value


def check_case(fc: FileChecker, case, index: int, seen_ids: set,
               expected_skill: str) -> None:
    where = f"cases[{index}]"
    if not isinstance(case, dict):
        fc.error(f"{where}: each case must be an object, "
                 f"got {type(case).__name__}")
        return

    case_id = fc.require_nonempty_str(case, where, "id")
    if case_id is not None:
        if case_id in seen_ids:
            fc.error(f"{where}: duplicate case id {case_id!r}")
        seen_ids.add(case_id)
        where = f"cases[{index}] (id={case_id!r})"

    tier = fc.require_nonempty_str(case, where, "tier")
    if tier is not None and tier not in TIERS:
        fc.error(f"{where}: tier {tier!r} not in {sorted(TIERS)}")
        tier = None
    # From here on `tier` is either a known tier or None (already reported);
    # tier-dependent checks are skipped when it is None rather than firing
    # misleading follow-on errors.

    # scenario: Harbor task path, or null for unscripted checklist cases.
    if "scenario" not in case:
        fc.error(f"{where}: missing required field 'scenario' "
                 f"(use null for unscripted cases)")
    else:
        scenario = case["scenario"]
        if scenario is None:
            if tier is not None and tier != "unscripted":
                fc.error(f"{where}: scenario may be null only when "
                         f"tier is 'unscripted' (tier is {tier!r})")
        elif not isinstance(scenario, str):
            fc.error(f"{where}: scenario must be a string or null, "
                     f"got {type(scenario).__name__}")
        elif not SCENARIO_RE.match(scenario):
            # Lint only: wandb/skills-evals is not checked out here, so
            # existence cannot be verified — shape is the best we can do.
            fc.error(f"{where}: scenario {scenario!r} does not look like a "
                     f"Harbor task path (expected exactly 'tasks/<family>/"
                     f"<skill>/<scenario>', no '..', no leading '/')")
        elif scenario.split("/")[2] != expected_skill:
            fc.error(f"{where}: scenario {scenario!r} points at skill "
                     f"{scenario.split('/')[2]!r}, not this file's skill "
                     f"{expected_skill!r} — copied from another skill's "
                     f"evals.json?")

    fc.require(case, where, "blocking", bool)

    fc.require_nonempty_str(case, where, "user_request")

    if "user_turns" in case:
        # Copied verbatim from the harness answer key: either an object of
        # simulated-user reply rules ({"max_user_turns": ..., "rules": ...})
        # or a plain array of scripted turns. A malformed block would
        # silently degrade a multiturn scenario to single-turn, so the
        # object form's keys are checked.
        turns = case["user_turns"]
        if isinstance(turns, dict):
            unknown = sorted(set(turns) - {"max_user_turns", "rules"})
            if unknown:
                fc.error(f"{where}: user_turns has unknown key(s) {unknown} "
                         f"(harness reply rules use 'max_user_turns' and "
                         f"'rules')")
            if "max_user_turns" in turns:
                max_turns = turns["max_user_turns"]
                if (isinstance(max_turns, bool)
                        or not isinstance(max_turns, int) or max_turns < 1):
                    fc.error(f"{where}: user_turns.max_user_turns must be a "
                             f"positive integer, got {max_turns!r}")
            rules = turns.get("rules")
            if not isinstance(rules, list) or not rules:
                fc.error(f"{where}: user_turns.rules must be a non-empty "
                         f"array of reply rules")
            else:
                for j, rule in enumerate(rules):
                    if not isinstance(rule, dict) or not isinstance(
                            rule.get("reply"), str) or not rule["reply"].strip():
                        fc.error(f"{where}: user_turns.rules[{j}] must be an "
                                 f"object with a non-empty string 'reply'")
        elif isinstance(turns, list):
            if not turns or not all(
                    isinstance(t, str) and t.strip() for t in turns):
                fc.error(f"{where}: user_turns as an array must be a "
                         f"non-empty array of non-empty strings")
        else:
            fc.error(f"{where}: user_turns must be an object (harness reply "
                     f"rules) or an array, got {type(turns).__name__}")

    expect = fc.require(case, where, "expect", list)
    if (expect is not None and not expect
            and tier is not None and tier != "unscripted"):
        fc.error(f"{where}: expect must not be empty for tier {tier!r} — a "
                 f"scripted case that asserts nothing always passes (only "
                 f"'unscripted' checklist cases may have expect: [])")

    if "rubric_criteria" in case:
        rubric = case["rubric_criteria"]
        if not isinstance(rubric, list):
            fc.error(f"{where}: rubric_criteria must be an array")
        else:
            for j, crit in enumerate(rubric):
                if not isinstance(crit, dict):
                    fc.error(f"{where}: rubric_criteria[{j}] must be an "
                             f"object with 'key' and 'desc'")
                    continue
                fc.require_nonempty_str(crit, f"{where}.rubric_criteria[{j}]",
                                        "key")
                fc.require_nonempty_str(crit, f"{where}.rubric_criteria[{j}]",
                                        "desc")

    # reward_gates: required for scripted (Harbor-backed) tiers, optional
    # for unscripted checklist cases.
    if "reward_gates" in case:
        gates = case["reward_gates"]
        if not isinstance(gates, dict) or not gates:
            fc.error(f"{where}: reward_gates must be a non-empty object")
        else:
            for gate, threshold in gates.items():
                if gate not in GATE_KEYS:
                    fc.error(f"{where}: unknown reward gate {gate!r} — the "
                             f"harness emits {sorted(GATE_KEYS)}; a typoed "
                             f"key gates nothing (extend GATE_KEYS in this "
                             f"validator if the harness grew a new reward)")
                if isinstance(threshold, bool) or not isinstance(
                        threshold, (int, float)):
                    fc.error(f"{where}: reward_gates[{gate!r}] must be a "
                             f"number, got {type(threshold).__name__}")
                elif not 0 <= threshold <= 1:
                    fc.error(f"{where}: reward_gates[{gate!r}] must be in "
                             f"[0, 1], got {threshold}")
    elif tier is not None and tier != "unscripted":
        fc.error(f"{where}: reward_gates is required for tier {tier!r} "
                 f"(only 'unscripted' cases may omit it)")


def check_file(path: Path, expected_skill: str,
               location_errors: list[str]) -> FileChecker:
    fc = FileChecker(_rel(path))
    for message in location_errors:
        fc.error(message)

    try:
        doc = json.loads(path.read_text(encoding="utf-8"),
                         object_pairs_hook=_no_dup_keys)
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        # ValueError covers json.JSONDecodeError and _no_dup_keys rejections.
        fc.error(f"cannot parse as JSON: {exc}")
        return fc

    if not isinstance(doc, dict):
        fc.error(f"top level must be a JSON object, "
                 f"got {type(doc).__name__}")
        return fc

    version = fc.require(doc, "top level", "schema_version", int)
    if version is not None and version != 1:
        fc.error(f"top level: unsupported schema_version {version} "
                 f"(this validator understands 1)")

    skill = fc.require_nonempty_str(doc, "top level", "skill")
    if skill is not None and skill != expected_skill:
        fc.error(f"top level: skill {skill!r} does not match its location "
                 f"(expected {expected_skill!r})")

    harness = fc.require(doc, "top level", "harness", dict)
    if harness is not None:
        fc.require_nonempty_str(harness, "harness", "repo")
        fc.require_nonempty_str(harness, "harness", "runner")

    if "notes" in doc and not isinstance(doc["notes"], str):
        fc.error("top level: notes must be a string")

    cases = fc.require(doc, "top level", "cases", list)
    if cases is not None:
        if not cases:
            fc.error("top level: cases must not be empty")
        seen_ids: set = set()
        for i, case in enumerate(cases):
            check_case(fc, case, i, seen_ids, expected_skill)

    return fc


def discover() -> tuple[list[tuple[Path, str, list[str]]],
                        list[tuple[Path, str]]]:
    """(files, strays).

    files:  (path, expected_skill, location_errors) for every eval file.
            Location errors — an evals.json whose surroundings say it can't
            belong to a real skill — are attributed to the file so they are
            reported and annotated alongside its schema errors.
    strays: (path, message) for eval-like files the validator would NOT
            check — a misnamed or misplaced file would otherwise silently
            shrink the suite while the job stays green, so each is an error.
    """
    found: list[tuple[Path, str, list[str]]] = []
    strays: list[tuple[Path, str]] = []

    if SKILLS_DIR.is_dir():
        for source_dir in sorted(SKILLS_DIR.iterdir()):
            # Mirror build.py: `_`-prefixed dirs (e.g. _example-skill-template)
            # are not skills and are never built — skip them entirely.
            if not source_dir.is_dir() or source_dir.name.startswith("_"):
                continue
            evals_dir = source_dir / "evals"
            if not evals_dir.is_dir():
                continue  # evals are optional; build.py doesn't require them
            path = evals_dir / "evals.json"
            for entry in sorted(evals_dir.rglob("*")):
                if entry.is_file() and entry != path:
                    strays.append((entry, (
                        f"unexpected file in skills/{source_dir.name}/evals/ "
                        f"— the validator only checks evals/evals.json, so "
                        f"this file would never be validated; rename or "
                        f"remove it")))
            if not path.is_file():
                continue
            location_errors = []
            if not (source_dir / "skill.yaml").is_file():
                location_errors.append(
                    f"located in skills/{source_dir.name}/, which has no "
                    f"skill.yaml — build.py does not treat that directory "
                    f"as a skill"
                )
            found.append((path, source_dir.name, location_errors))

    manifest_names = standalone_skill_names()
    if STANDALONE_EVALS_DIR.is_dir():
        for path in sorted(STANDALONE_EVALS_DIR.iterdir()):
            if not path.is_file() or not path.name.endswith(".evals.json"):
                strays.append((path, (
                    "unexpected entry in evals/standalone/ — only "
                    "<skill-name>.evals.json files live here, so this "
                    "would never be validated; rename or remove it")))
                continue
            stem = path.name[: -len(".evals.json")]
            location_errors = []
            if stem not in manifest_names:
                location_errors.append(
                    f"no standalone skill named {stem!r} is declared in "
                    f"standalone-skills.yaml (frontmatter.name values: "
                    f"{sorted(manifest_names) or 'none found'}) — rename "
                    f"this file to match the manifest, or add the skill "
                    f"there first"
                )
            found.append((path, stem, location_errors))

    return found, strays


def main() -> int:
    files, strays = discover()
    if not files and not strays:
        print("error: no eval files found under skills/*/evals/evals.json "
              "or evals/standalone/*.evals.json — the seeded files should "
              "exist, so this usually means the script moved or the "
              "checkout is broken", file=sys.stderr)
        return 2

    annotations: list[tuple[str, str]] = []
    failed = 0
    for path, message in strays:
        failed += 1
        relpath = _rel(path)
        print(f"FAIL {relpath}")
        print(f"  - {message}")
        annotations.append((relpath, message))
    for path, expected_skill, location_errors in files:
        fc = check_file(path, expected_skill, location_errors)
        if fc.errors:
            failed += 1
            print(f"FAIL {fc.relpath}")
            for message in fc.errors:
                print(f"  - {message}")
                annotations.append((fc.relpath, message))
        else:
            print(f"  ✓ {fc.relpath}")

    print(f"\n{len(files) + len(strays)} file(s) checked, {failed} failed, "
          f"{len(annotations)} error(s)")

    if annotations:
        # Workflow annotations, emitted unconditionally on stderr in one
        # block at the end — harmless locally, same convention as
        # scripts/check_plugin_parity.py. Message content is escaped
        # (`%`/CR/LF) so a value embedded in an error can never terminate
        # the ::error command and inject a workflow command of its own.
        print(file=sys.stderr)
        for relpath, message in annotations:
            print(f"::error file={_escape_annotation(relpath)}::"
                  f"{_escape_annotation(message)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001 — CI must distinguish crashes
        print(f"internal error: {exc}", file=sys.stderr)
        sys.exit(2)
