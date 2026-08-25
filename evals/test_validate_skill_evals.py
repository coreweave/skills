#!/usr/bin/env python3
"""Negative tests for evals/validate_skill_evals.py (APPSEC-3967).

Every rule in the validator exists to catch a specific way an eval seed
can look fine and gate nothing. A lint whose rules are untested is the
same class of problem, so each tier-dependent rule gets a case that
MUST fail plus the neighbouring legitimate shape that MUST pass — a
tightening that also rejects real seeds is a regression, not a fix.

`test_repo_seed_files_all_validate` is the backstop: the real committed
seeds must stay clean, so a future rule cannot be merged by editing only
this file.

Stdlib unittest, no pytest, matching the validator itself and the
skill-evals-lint workflow (which runs python3 with no pip install):

    python3 -m unittest discover -s evals -p 'test_*.py'
"""

from __future__ import annotations

import importlib.util
import json
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
VALIDATOR = REPO_ROOT / "evals" / "validate_skill_evals.py"

_spec = importlib.util.spec_from_file_location("validate_skill_evals", VALIDATOR)
assert _spec and _spec.loader
v = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(v)

SKILL = "cw-create-cluster"

# A minimal case that passes every rule; each test mutates one field so a
# failure names exactly the rule under test.
JUDGED_CASE = {
    "id": "case-1",
    "scenario": f"tasks/cks/{SKILL}/happy-mock-judged",
    "tier": "mock-judged",
    "blocking": True,
    "user_request": "Create a cluster.",
    "expect": [{"kind": "resource"}],
    "rubric_criteria": [{"key": "programmatic_first", "desc": "Uses the API."}],
    "reward_gates": {"outcome": 1.0, "tq_opus": 0.8, "tq_sonnet": 0.8},
}

MOCK_CASE = {
    "id": "case-1",
    "scenario": f"tasks/cks/{SKILL}/happy-mock",
    "tier": "mock",
    "blocking": True,
    "user_request": "Create a cluster.",
    "expect": [{"kind": "resource"}],
    "reward_gates": {"outcome": 1.0},
}

UNSCRIPTED_CASE = {
    "id": "case-1",
    "scenario": None,
    "tier": "unscripted",
    "blocking": False,
    "user_request": "Create a cluster.",
    "expect": [],
    "rubric_criteria": [{"key": "no_secrets", "desc": "No secrets echoed."}],
}


def case_with(base: dict, **overrides) -> dict:
    """Copy `base`, applying overrides; a value of ... removes the key."""
    case = dict(base)
    for key, value in overrides.items():
        if value is ...:
            case.pop(key, None)
        else:
            case[key] = value
    return case


def errors(case: dict) -> list[str]:
    checker = v.FileChecker("test.json")
    v.check_case(checker, case, 0, set(), SKILL)
    return checker.errors


def with_rules(base: dict, rules, max_user_turns: int = 3) -> dict:
    return case_with(
        base, user_turns={"max_user_turns": max_user_turns, "rules": rules}
    )


class BaselineTest(unittest.TestCase):
    """The fixtures themselves must be clean, or every test below is vacuous."""

    def test_fixtures_pass(self):
        for name, case in (("judged", JUDGED_CASE), ("mock", MOCK_CASE),
                           ("unscripted", UNSCRIPTED_CASE)):
            with self.subTest(fixture=name):
                self.assertEqual(errors(case), [])


class ReplyRuleTest(unittest.TestCase):
    """user_turns reply rules — mirrors eval_agents/user_turns.py, which
    raises on each of these instead of running the scenario."""

    def test_specific_rule_needs_a_matcher(self):
        # The bug from review: `reply` alone parsed fine, but the harness
        # can never fire the rule, so the simulated user stays silent and
        # the multiturn scenario degrades to single-turn.
        found = errors(with_rules(JUDGED_CASE, [{"reply": "Yes, go ahead."}]))
        self.assertTrue(any("when_reply_matches" in e for e in found), found)

    def test_empty_matcher_rejected(self):
        found = errors(with_rules(
            JUDGED_CASE, [{"when_reply_matches": "   ", "reply": "Yes."}]))
        self.assertTrue(any("when_reply_matches" in e for e in found), found)

    def test_non_string_matcher_rejected(self):
        found = errors(with_rules(
            JUDGED_CASE, [{"when_reply_matches": 12, "reply": "Yes."}]))
        self.assertTrue(any("when_reply_matches" in e for e in found), found)

    def test_uncompilable_matcher_rejected(self):
        found = errors(with_rules(
            JUDGED_CASE, [{"when_reply_matches": "([oops", "reply": "Yes."}]))
        self.assertTrue(any("valid regex" in e for e in found), found)

    def test_specific_rule_with_matcher_and_reply_passes(self):
        self.assertEqual(errors(with_rules(JUDGED_CASE, [
            {"when_reply_matches": "install.*kubectl", "reply": "Yes."},
        ])), [])

    def test_confirmation_fallback_needs_no_matcher(self):
        # The legitimate second form: the harness's own answer keys pair a
        # specific rule with one bounded `fallback: confirm` rule, which
        # must NOT carry a matcher. Requiring when_reply_matches
        # unconditionally would reject real scenarios.
        self.assertEqual(errors(with_rules(JUDGED_CASE, [
            {"when_reply_matches": "13A|switch.*zone", "reply": "Use 13A."},
            {"fallback": "confirm", "reply": "Yes, proceed.", "max_uses": 2},
        ])), [])

    def test_explicit_null_fallback_reads_as_a_specific_rule(self):
        # The harness does `rule.get("fallback") is not None`, so a written
        # null is a specific rule and still needs a matcher — matching that
        # exactly keeps the lint from inventing a failure the run wouldn't
        # have.
        self.assertEqual(errors(with_rules(JUDGED_CASE, [
            {"fallback": None, "when_reply_matches": "x", "reply": "Yes."},
        ])), [])
        found = errors(with_rules(
            JUDGED_CASE, [{"fallback": None, "reply": "Yes."}]))
        self.assertTrue(any("when_reply_matches" in e for e in found), found)

    def test_unknown_fallback_mode_rejected(self):
        found = errors(with_rules(
            JUDGED_CASE, [{"fallback": "grant-anything", "reply": "Yes."}]))
        self.assertTrue(any("unsupported fallback" in e for e in found), found)

    def test_fallback_cannot_also_match(self):
        found = errors(with_rules(JUDGED_CASE, [
            {"fallback": "confirm", "when_reply_matches": "x", "reply": "Y."},
        ]))
        self.assertTrue(any("cannot combine" in e for e in found), found)

    def test_at_most_one_fallback(self):
        found = errors(with_rules(JUDGED_CASE, [
            {"fallback": "confirm", "reply": "Yes."},
            {"fallback": "confirm", "reply": "Sure."},
        ]))
        self.assertTrue(any("at most one" in e for e in found), found)

    def test_reply_still_required(self):
        found = errors(with_rules(
            JUDGED_CASE, [{"when_reply_matches": "x", "reply": "  "}]))
        self.assertTrue(any("'reply'" in e for e in found), found)

    def test_max_uses_must_be_a_positive_int(self):
        for bad in ("1", 0, -1, 1.5, True, None):
            with self.subTest(max_uses=bad):
                found = errors(with_rules(JUDGED_CASE, [
                    {"when_reply_matches": "x", "reply": "Y.", "max_uses": bad},
                ]))
                self.assertTrue(any("max_uses" in e for e in found), found)

    def test_max_uses_omitted_is_fine(self):
        # The harness defaults it to 1.
        self.assertEqual(errors(with_rules(
            JUDGED_CASE, [{"when_reply_matches": "x", "reply": "Y."}])), [])

    def test_non_object_rule_rejected(self):
        found = errors(with_rules(JUDGED_CASE, ["just a string"]))
        self.assertTrue(any("must be an object" in e for e in found), found)


class RubricTest(unittest.TestCase):
    """rubric_criteria is the signal for judged + unscripted tiers, and is
    what makes a scenario judged at all in skills-evals."""

    def test_judged_tier_requires_rubric(self):
        for tier in ("mock-judged", "real-judged"):
            with self.subTest(tier=tier):
                found = errors(case_with(
                    JUDGED_CASE, tier=tier, rubric_criteria=...))
                self.assertTrue(
                    any("rubric_criteria is required" in e for e in found),
                    found)

    def test_judged_tier_rejects_empty_rubric(self):
        found = errors(case_with(JUDGED_CASE, rubric_criteria=[]))
        self.assertTrue(any("must not be empty" in e for e in found), found)

    def test_unscripted_requires_rubric(self):
        found = errors(case_with(UNSCRIPTED_CASE, rubric_criteria=...))
        self.assertTrue(
            any("rubric_criteria is required" in e for e in found), found)

    def test_unscripted_rejects_empty_rubric(self):
        found = errors(case_with(UNSCRIPTED_CASE, rubric_criteria=[]))
        self.assertTrue(any("must not be empty" in e for e in found), found)

    def test_deterministic_tier_rejects_a_rubric(self):
        # A rubric on a non-judged scenario is scored by nobody: the
        # harness vendors judge.py only for scenarios whose answer key
        # declares a non-empty rubric (tasks/scaffold.py::_is_judged).
        found = errors(case_with(
            MOCK_CASE, rubric_criteria=[{"key": "k", "desc": "d"}]))
        self.assertTrue(any("not judged" in e for e in found), found)

    def test_deterministic_tier_rejects_an_empty_rubric_key(self):
        # The docstring says the key is forbidden on mock/real, so an
        # empty one is rejected too — it is the same copied-case leftover
        # with the criteria deleted, and nothing reads it.
        found = errors(case_with(MOCK_CASE, rubric_criteria=[]))
        self.assertTrue(any("not judged" in e for e in found), found)

    def test_deterministic_tier_without_the_key_passes(self):
        # The neighbouring legitimate shape: no rubric key at all.
        self.assertEqual(errors(case_with(MOCK_CASE, rubric_criteria=...)), [])

    def test_rubric_entries_still_need_key_and_desc(self):
        found = errors(case_with(
            JUDGED_CASE, rubric_criteria=[{"key": "k"}]))
        self.assertTrue(any("'desc'" in e for e in found), found)

    def test_unknown_tier_does_not_fire_rubric_rules(self):
        # tier is already reported as invalid; tier-dependent rules must
        # stay quiet rather than pile on misleading errors.
        found = errors(case_with(JUDGED_CASE, tier="bogus",
                                 rubric_criteria=...))
        self.assertEqual([e for e in found if "rubric" in e], [], found)


class ScenarioTierTest(unittest.TestCase):
    """`scenario` is null exactly for "unscripted" — both directions."""

    def test_scripted_tier_rejects_a_null_scenario(self):
        found = errors(case_with(MOCK_CASE, scenario=None))
        self.assertTrue(
            any("scenario may be null only" in e for e in found), found)

    def test_unscripted_rejects_a_scenario_path(self):
        # The review gap: an unscripted case could carry a real Harbor
        # path and validate, advertising a scenario that never runs.
        found = errors(case_with(
            UNSCRIPTED_CASE, scenario=f"tasks/cks/{SKILL}/happy-mock"))
        self.assertTrue(
            any("requires scenario: null" in e for e in found), found)

    def test_unscripted_with_null_scenario_passes(self):
        self.assertEqual(errors(UNSCRIPTED_CASE), [])

    def test_unknown_tier_does_not_fire_scenario_tier_rules(self):
        found = errors(case_with(UNSCRIPTED_CASE, tier="bogus"))
        self.assertEqual(
            [e for e in found if "scenario" in e], [], found)

    def test_scenario_must_point_at_this_skill(self):
        found = errors(case_with(
            MOCK_CASE, scenario="tasks/cks/cw-create-node-pool/happy-mock"))
        self.assertTrue(any("points at skill" in e for e in found), found)


class ExpectTierTest(unittest.TestCase):
    """`expect` is non-empty for scripted tiers and [] for "unscripted"."""

    def test_scripted_tier_rejects_an_empty_expect(self):
        found = errors(case_with(MOCK_CASE, expect=[]))
        self.assertTrue(
            any("expect must not be empty" in e for e in found), found)

    def test_unscripted_rejects_a_non_empty_expect(self):
        # Nothing deterministic runs, so these assertions are evaluated by
        # nobody — the checklist belongs in rubric_criteria.
        found = errors(case_with(
            UNSCRIPTED_CASE, expect=[{"kind": "resource"}]))
        self.assertTrue(
            any("expect must be []" in e for e in found), found)

    def test_unknown_tier_does_not_fire_expect_rules(self):
        found = errors(case_with(JUDGED_CASE, tier="bogus", expect=[]))
        self.assertEqual([e for e in found if "expect" in e], [], found)


class RewardGateTest(unittest.TestCase):
    """reward_gates must gate the rewards the tier actually emits."""

    def test_judged_tier_requires_outcome_and_both_judges(self):
        found = errors(case_with(
            JUDGED_CASE, reward_gates={"call_valid": 1.0}))
        self.assertTrue(any(
            "missing ['outcome', 'tq_opus', 'tq_sonnet']" in e for e in found),
            found)

    def test_judged_tier_rejects_a_single_missing_judge(self):
        found = errors(case_with(
            JUDGED_CASE,
            reward_gates={"outcome": 1.0, "tq_opus": 0.8}))
        self.assertTrue(
            any("missing ['tq_sonnet']" in e for e in found), found)

    def test_scripted_tier_requires_outcome(self):
        found = errors(case_with(MOCK_CASE, reward_gates={"call_valid": 1.0}))
        self.assertTrue(any("missing ['outcome']" in e for e in found), found)

    def test_non_judged_tier_cannot_gate_judge_rewards(self):
        # Without judge.py the harness emits `outcome` alone, so a tq_*
        # gate here can never be satisfied.
        found = errors(case_with(
            MOCK_CASE, reward_gates={"outcome": 1.0, "tq_opus": 0.8}))
        self.assertTrue(any("runs no judge" in e for e in found), found)

    def test_scripted_tier_may_add_call_valid(self):
        self.assertEqual(errors(case_with(
            MOCK_CASE,
            reward_gates={"outcome": 1.0, "call_valid": 1.0})), [])

    def test_unscripted_may_omit_gates(self):
        self.assertEqual(errors(UNSCRIPTED_CASE), [])

    def test_unscripted_rejects_every_gate_not_only_judge_gates(self):
        # The review gap: the tq_* mirror rule caught only judge gates, so
        # {"outcome": 1.0} / {"call_valid": 1.0} on an unscripted case
        # still validated even though no scenario runs and the
        # deterministic verifier never reports either reward.
        for gates in ({"outcome": 1.0}, {"call_valid": 1.0},
                      {"outcome": 1.0, "call_valid": 1.0},
                      {"tq_opus": 0.8, "tq_sonnet": 0.8}):
            with self.subTest(gates=sorted(gates)):
                found = errors(case_with(UNSCRIPTED_CASE,
                                         reward_gates=gates))
                self.assertTrue(
                    any("runs no scenario" in e for e in found), found)

    def test_unscripted_gate_rejection_still_validates_gate_shape(self):
        # An unknown key inside a forbidden block is still named, so the
        # author does not fix the tier and then hit a second surprise.
        found = errors(case_with(UNSCRIPTED_CASE,
                                 reward_gates={"tq_haiku": 0.8}))
        self.assertTrue(any("unknown reward gate" in e for e in found), found)
        self.assertTrue(any("runs no scenario" in e for e in found), found)

    def test_unknown_gate_key_still_rejected(self):
        found = errors(case_with(
            JUDGED_CASE,
            reward_gates={"outcome": 1.0, "tq_opus": 0.8, "tq_sonnet": 0.8,
                          "tq_haiku": 0.8}))
        self.assertTrue(any("unknown reward gate" in e for e in found), found)

    def test_unknown_tier_does_not_fire_gate_rules(self):
        found = errors(case_with(JUDGED_CASE, tier="bogus",
                                 reward_gates={"call_valid": 1.0}))
        self.assertEqual([e for e in found if "missing" in e], [], found)


class RepoSeedTest(unittest.TestCase):
    def test_repo_seed_files_all_validate(self):
        """The committed seeds must satisfy every rule above.

        This is what keeps a tightening honest: a rule that also rejects a
        real seed means either the rule or the seed is wrong, and this
        test forces that decision instead of letting it land.
        """
        files, strays = v.discover()
        self.assertEqual([v._rel(p) for p, _ in strays], [])
        self.assertTrue(files, "no eval seed files discovered")
        for path, expected_skill, location_errors in files:
            with self.subTest(path=v._rel(path)):
                checker = v.check_file(path, expected_skill, location_errors)
                self.assertEqual(checker.errors, [])

    def test_seeded_multiturn_rules_exercise_the_reply_rule_checks(self):
        """At least one real seed has a user_turns block, so the reply-rule
        path is covered by real data and not only by fixtures."""
        files, _ = v.discover()
        seen = 0
        for path, _skill, _errs in files:
            doc = json.loads(path.read_text(encoding="utf-8"))
            for case in doc.get("cases", []):
                if isinstance(case, dict) and "user_turns" in case:
                    seen += 1
        self.assertGreater(seen, 0, "no seeded case exercises user_turns")


if __name__ == "__main__":
    unittest.main()
