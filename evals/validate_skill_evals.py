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
                                            # null for tier "unscripted",
                                            # a path for every other tier
          "tier": "mock" | "mock-judged" | "real" | "real-judged" | "unscripted",
          "blocking": true | false,
          "user_request": "...",
          "user_turns": {...},              # optional (multiturn scenarios;
                                            # verbatim harness reply rules)
          "expect": [...],                  # non-empty for the scripted
                                            # tiers, [] for "unscripted"
          "rubric_criteria": [{"key": "...", "desc": "..."}],
                                            # required and non-empty for the
                                            # rubric-driven tiers; the key
                                            # itself (even as []) is
                                            # forbidden for "mock"/"real"
          "reward_gates": {"outcome": 1.0, ...}  # required for the scripted
                                            # tiers, forbidden for
                                            # "unscripted"; keys from
                                            # GATE_KEYS below
        }
      ]
    }

Tier-dependent requirements (a case that satisfies the types but not
these is vacuous — it would sit in the suite gating nothing):

  - "mock" / "real": deterministic only. Must gate `outcome`; must NOT
    gate tq_opus/tq_sonnet (no judge runs) and must NOT carry a
    `rubric_criteria` key at all — an empty one is rejected too, since a
    rubric here is a leftover from a copied judged case either way.
  - "mock-judged" / "real-judged": must gate `outcome`, `tq_opus` and
    `tq_sonnet`, and must carry a non-empty `rubric_criteria` — in
    skills-evals a scenario is judged exactly when its answer key
    declares a non-empty rubric.
  - "unscripted": nothing executes, so the case carries none of the
    scripted machinery — `scenario` must be null, `expect` must be `[]`
    and `reward_gates` must be absent. The non-empty `rubric_criteria` IS
    the manual checklist. Note this covers the deterministic rewards too,
    not just tq_*: with no scenario there is no verifier run, so
    `outcome`/`call_valid` are never reported either and a gate on them
    is as unsatisfiable as a judge gate.

`user_turns.rules` entries are linted against the two mutually
exclusive forms eval_agents/user_turns.py accepts — a specific
`{"when_reply_matches": "<regex>", "reply": ...}` rule (matcher
required, and it must compile) or at most one bounded
`{"fallback": "confirm", "reply": ...}` rule (which may not also carry a
matcher). `max_uses` defaults to 1 and must be a positive integer when
written. A rule with a `reply` but no way to fire leaves the agent's
question unanswered, silently degrading a multiturn scenario to
single-turn.

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

# Tiers backed by a Harbor scenario, i.e. something actually runs and the
# deterministic verifier emits `outcome`. "unscripted" is the only tier
# with no harness run behind it (a human checklist).
SCRIPTED_TIERS = {"mock", "mock-judged", "real", "real-judged"}

# Tiers whose scenario carries the dual LLM judge. In skills-evals a
# scenario is judged exactly when its answer key declares a non-empty
# `rubric_criteria` — tasks/scaffold.py::_is_judged reads that list and
# nothing else to decide whether judge.py plus the judged tests/test.sh
# are vendored into the scenario — and only judged scenarios emit
# tq_opus/tq_sonnet.
#
# The `-judged` directory suffix is NOT what makes a scenario judged: it
# is a naming convention. scripts/lint_scenarios.py's `judged-naming`
# check only emits an informational WARNING (exit 0 unless --strict) when
# a rubric-bearing scenario dir lacks the suffix, and calls the suffix
# "interim per docs/DESIGN.md and NOT authoritative" in its own docstring.
# So a `-judged` tier here may legitimately name a scenario dir without
# the suffix: upstream `tasks/cks/cw-create-node-pool/happy-mock` carries
# a 4-criterion rubric and IS judged, while
# `tasks/cks/cw-create-cluster/happy-mock` carries none and is
# deterministic — which is why this repo seeds the first as `mock-judged`
# and the second as `mock`. The rubric is the contract; the suffix is
# cosmetic, so tier and directory name are checked against nothing here.
JUDGED_TIERS = {"mock-judged", "real-judged"}

# Tiers whose signal comes from a rubric rather than from `expect` alone:
# the judged tiers feed `rubric_criteria` to the LLM judges, and
# "unscripted" cases have no scenario, no expect and no gates, so the
# rubric IS the manual checklist. A case in one of these tiers with no
# rubric asserts nothing at all.
RUBRIC_TIERS = JUDGED_TIERS | {"unscripted"}

# Reward keys the skills-evals harness emits (tests/test.sh: outcome and
# call_valid from the deterministic verifier, tq_opus/tq_sonnet from the
# LLM judges). A typoed key would gate nothing and stay green forever, so
# unknown keys are errors — extend this set when the harness grows a new
# reward, in the same PR that starts gating on it.
GATE_KEYS = {"outcome", "call_valid", "tq_opus", "tq_sonnet"}

# Gates only a judged scenario can ever satisfy: without judge.py the
# harness emits `outcome` alone (docs/DESIGN.md), so gating tq_* on a
# non-judged tier gates a reward that is never reported.
JUDGE_GATE_KEYS = {"tq_opus", "tq_sonnet"}

# The one `fallback` mode eval_agents/user_turns.py accepts; any other
# value makes the harness raise instead of running.
FALLBACK_MODES = {"confirm"}

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


def check_reply_rule(fc: FileChecker, rule, where: str, index: int) -> bool:
    """Lint one simulated-user reply rule; return True if it is a fallback
    rule (the caller enforces the at-most-one budget).

    Mirrors eval_agents/user_turns.py::_compile_rules in skills-evals,
    which raises — aborting the run — on every shape rejected here. Two
    rule forms exist and they are mutually exclusive:

        {"when_reply_matches": "<regex>", "reply": "...", "max_uses": 1}
        {"fallback": "confirm",           "reply": "...", "max_uses": 2}

    A specific rule with no matcher is the dangerous case: the harness
    never fires it, so the simulated user stays silent and a multiturn
    scenario silently degrades to single-turn (agent hangs on its
    question, case fails or passes for the wrong reason). `reply` alone
    is therefore not enough.
    """
    at = f"{where}: user_turns.rules[{index}]"
    if not isinstance(rule, dict):
        fc.error(f"{at} must be an object, got {type(rule).__name__}")
        return False

    reply = rule.get("reply")
    if not isinstance(reply, str) or not reply.strip():
        fc.error(f"{at} must have a non-empty string 'reply'")

    pattern = rule.get("when_reply_matches")
    # `.get(...) is not None`, not `in`, so an explicit `"fallback": null`
    # is read as a specific rule — exactly how the harness reads it.
    fallback = rule.get("fallback")
    is_fallback = fallback is not None

    if is_fallback:
        if fallback not in FALLBACK_MODES:
            fc.error(f"{at}: unsupported fallback {fallback!r} — the harness "
                     f"accepts only {sorted(FALLBACK_MODES)}")
        if pattern is not None:
            fc.error(f"{at} cannot combine 'fallback' with "
                     f"'when_reply_matches' — the harness rejects rules that "
                     f"are both a bounded confirmation fallback and a "
                     f"specific matcher")
    elif not isinstance(pattern, str) or not pattern.strip():
        fc.error(f"{at} must have a non-empty string 'when_reply_matches' "
                 f"(or be a {{'fallback': 'confirm'}} rule) — a rule the "
                 f"simulated user can never match leaves the agent's "
                 f"question unanswered and degrades the scenario to "
                 f"single-turn")
    else:
        try:
            re.compile(pattern)
        except re.error as exc:
            fc.error(f"{at}: when_reply_matches {pattern!r} is not a valid "
                     f"regex ({exc}) — the harness compiles it and would "
                     f"abort the run")

    # max_uses is optional (the harness defaults it to 1) but must be a
    # positive integer when written. The authoring dashboard normalises it
    # to an int on save, so a string here means hand-editing.
    if "max_uses" in rule:
        max_uses = rule["max_uses"]
        if (isinstance(max_uses, bool) or not isinstance(max_uses, int)
                or max_uses < 1):
            fc.error(f"{at}: max_uses must be a positive integer, "
                     f"got {max_uses!r}")

    return is_fallback


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
        elif tier == "unscripted":
            # Mirror of the rule above: `null` is allowed only for
            # "unscripted", and "unscripted" allows only `null`. No Harbor
            # run happens for a checklist case, so a task path here either
            # is a leftover from a copied scripted case or advertises a
            # scenario that never executes — and it would make the case
            # look Harbor-backed to anyone reading the suite.
            fc.error(f"{where}: tier 'unscripted' requires scenario: null, "
                     f"got {scenario!r} — nothing runs for a checklist "
                     f"case, so the path would never be executed (drop it, "
                     f"or move the case to a scripted tier)")
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
                fallbacks = 0
                for j, rule in enumerate(rules):
                    if check_reply_rule(fc, rule, where, j):
                        fallbacks += 1
                if fallbacks > 1:
                    fc.error(f"{where}: user_turns.rules declares "
                             f"{fallbacks} 'fallback' rules — the harness "
                             f"allows at most one")
        elif isinstance(turns, list):
            if not turns or not all(
                    isinstance(t, str) and t.strip() for t in turns):
                fc.error(f"{where}: user_turns as an array must be a "
                         f"non-empty array of non-empty strings")
        else:
            fc.error(f"{where}: user_turns must be an object (harness reply "
                     f"rules) or an array, got {type(turns).__name__}")

    expect = fc.require(case, where, "expect", list)
    if expect is not None and tier is not None:
        if not expect and tier != "unscripted":
            fc.error(f"{where}: expect must not be empty for tier {tier!r} "
                     f"— a scripted case that asserts nothing always passes "
                     f"(only 'unscripted' checklist cases may have "
                     f"expect: [])")
        elif expect and tier == "unscripted":
            # Same asymmetry as scenario/reward_gates: no deterministic
            # verifier runs for a checklist case, so `expect` entries are
            # read by nobody. The manual checklist is rubric_criteria.
            fc.error(f"{where}: expect must be [] for tier 'unscripted' — "
                     f"no deterministic verifier runs for a checklist case, "
                     f"so these assertions are never evaluated (put the "
                     f"checks in rubric_criteria)")

    # rubric_criteria: the judge rubric (judged tiers) or the manual
    # checklist (unscripted). Required and non-empty for those tiers,
    # forbidden for the deterministic-only tiers — in skills-evals a
    # scenario is judged exactly when its answer key declares a non-empty
    # rubric, so a rubric on a "mock"/"real" case either wants a judge the
    # scenario does not have or is a leftover from a copied case.
    if "rubric_criteria" in case:
        rubric = case["rubric_criteria"]
        if not isinstance(rubric, list):
            fc.error(f"{where}: rubric_criteria must be an array")
        elif not rubric and tier in RUBRIC_TIERS:
            source = ("LLM-judge criteria" if tier in JUDGED_TIERS
                      else "the manual checklist")
            fc.error(f"{where}: rubric_criteria must not be empty for tier "
                     f"{tier!r} — that tier's signal comes from the rubric "
                     f"({source}), so an empty rubric makes the case vacuous")
        elif tier is not None and tier not in RUBRIC_TIERS:
            # The key at all, not just a non-empty one: `rubric_criteria:
            # []` on a deterministic tier is the same copied-case leftover
            # with its criteria deleted, and nothing in the harness reads
            # it, so "forbidden" is enforced literally.
            detail = ("is set but" if rubric
                      else "is present (even empty) but")
            fc.error(f"{where}: rubric_criteria {detail} tier {tier!r} is "
                     f"not judged — only {sorted(RUBRIC_TIERS)} consume a "
                     f"rubric; a non-judged scenario runs no judge, so these "
                     f"criteria would be scored by nobody (rename the tier "
                     f"or drop the rubric)")
        if isinstance(rubric, list):
            for j, crit in enumerate(rubric):
                if not isinstance(crit, dict):
                    fc.error(f"{where}: rubric_criteria[{j}] must be an "
                             f"object with 'key' and 'desc'")
                    continue
                fc.require_nonempty_str(crit, f"{where}.rubric_criteria[{j}]",
                                        "key")
                fc.require_nonempty_str(crit, f"{where}.rubric_criteria[{j}]",
                                        "desc")
    elif tier in RUBRIC_TIERS:
        fc.error(f"{where}: rubric_criteria is required for tier {tier!r} — "
                 f"that tier's signal comes from the rubric, so a case "
                 f"without one asserts nothing")

    # reward_gates: required for the scripted (Harbor-backed) tiers,
    # forbidden for unscripted checklist cases (nothing runs, so nothing
    # reports a reward to gate).
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

            # Tier-specific required keys, not just the allowlist: the
            # deterministic verifier emits `outcome` for every Harbor
            # scenario, and the dual judge adds tq_opus/tq_sonnet on the
            # judged tiers. A gate object that omits them still parses,
            # but nothing gates the reward the tier exists to measure.
            if tier in SCRIPTED_TIERS:
                required = {"outcome"}
                if tier in JUDGED_TIERS:
                    required |= JUDGE_GATE_KEYS
                missing = sorted(required - set(gates))
                if missing:
                    fc.error(f"{where}: reward_gates is missing {missing} — "
                             f"tier {tier!r} must gate "
                             f"{sorted(required)} (the harness reports "
                             f"those; an ungated reward can regress to 0 "
                             f"with the suite still green)")
            # "unscripted" has no scenario, so NO reward is reported for
            # it — not tq_* and not the deterministic outcome/call_valid
            # either. Gating anything here is unsatisfiable, so the whole
            # block is rejected rather than only its tq_* half (which is
            # what the judge-gate mirror below would have caught).
            if tier == "unscripted":
                fc.error(f"{where}: reward_gates sets {sorted(gates)} but "
                         f"tier 'unscripted' runs no scenario — neither the "
                         f"deterministic verifier nor a judge executes, so "
                         f"no reward is ever reported and every gate here "
                         f"is unsatisfiable (drop reward_gates; the rubric "
                         f"is the checklist)")
            elif tier is not None and tier not in JUDGED_TIERS:
                judge_gates = sorted(JUDGE_GATE_KEYS & set(gates))
                if judge_gates:
                    fc.error(f"{where}: reward_gates sets {judge_gates} but "
                             f"tier {tier!r} runs no judge — the harness "
                             f"never emits those rewards here, so the gate "
                             f"is unsatisfiable (use a '-judged' tier)")
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
