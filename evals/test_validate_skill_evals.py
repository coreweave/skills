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

import contextlib
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

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
        """Real seeds must exercise BOTH reply-rule forms.

        Counting `user_turns` blocks is not enough. The seeds once carried
        only specific matcher rules — every bounded `fallback: confirm`
        rule had been dropped in transcription — so the fallback branch of
        check_reply_rule was covered by fixtures alone while the real
        corpus silently reintroduced the confirmation starvation the
        harness fixed upstream. Assert both forms are present.
        """
        files, _ = v.discover()
        specific = fallback = 0
        for path, _skill, _errs in files:
            doc = json.loads(path.read_text(encoding="utf-8"))
            for case in doc.get("cases", []):
                turns = case.get("user_turns") if isinstance(case, dict) else None
                if not isinstance(turns, dict):
                    continue
                for rule in turns.get("rules", []):
                    if not isinstance(rule, dict):
                        continue
                    if rule.get("fallback") is not None:
                        fallback += 1
                    elif rule.get("when_reply_matches"):
                        specific += 1
        self.assertGreater(specific, 0, "no seeded case has a matcher rule")
        self.assertGreater(fallback, 0, "no seeded case has a fallback rule")


class DocumentTest(unittest.TestCase):
    """Whole-file rules: the top-level envelope and the JSON parse itself.

    These were negative-tested by hand when the validator landed but had
    no automated cover, so a refactor could drop any of them silently.
    """

    def write(self, body: str) -> Path:
        path = Path(self.tmp.name) / "evals.json"
        path.write_text(body, encoding="utf-8")
        return path

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def doc_errors(self, body: str) -> list[str]:
        return v.check_file(self.write(body), SKILL, []).errors

    def valid_doc(self, **overrides) -> dict:
        doc = {
            "schema_version": 1,
            "skill": SKILL,
            "harness": {"repo": "wandb/skills-evals", "runner": "./run.sh"},
            "cases": [MOCK_CASE],
        }
        doc.update(overrides)
        return doc

    def test_valid_document_passes(self):
        self.assertEqual(self.doc_errors(json.dumps(self.valid_doc())), [])

    def test_duplicate_json_keys_rejected(self):
        # A merge-conflict artifact: json keeps only the last "cases", so
        # half the suite would vanish with the file still parsing.
        body = ('{"schema_version": 1, "skill": "%s", '
                '"harness": {"repo": "r", "runner": "x"}, '
                '"cases": [], "cases": []}' % SKILL)
        found = self.doc_errors(body)
        self.assertTrue(any("duplicate JSON key" in e for e in found), found)

    def test_unparseable_json_rejected(self):
        found = self.doc_errors("{not json")
        self.assertTrue(any("cannot parse as JSON" in e for e in found), found)

    def test_non_object_top_level_rejected(self):
        found = self.doc_errors("[]")
        self.assertTrue(any("must be a JSON object" in e for e in found),
                        found)

    def test_unsupported_schema_version_rejected(self):
        found = self.doc_errors(json.dumps(self.valid_doc(schema_version=2)))
        self.assertTrue(any("schema_version" in e for e in found), found)

    def test_skill_must_match_location(self):
        found = self.doc_errors(json.dumps(self.valid_doc(skill="other")))
        self.assertTrue(any("does not match its location" in e for e in found),
                        found)

    def test_harness_fields_required(self):
        found = self.doc_errors(json.dumps(self.valid_doc(harness={})))
        self.assertTrue(any("'repo'" in e for e in found), found)
        self.assertTrue(any("'runner'" in e for e in found), found)

    def test_empty_cases_rejected(self):
        found = self.doc_errors(json.dumps(self.valid_doc(cases=[])))
        self.assertTrue(any("cases must not be empty" in e for e in found),
                        found)

    def test_duplicate_case_ids_rejected(self):
        doc = self.valid_doc(cases=[MOCK_CASE, dict(MOCK_CASE)])
        found = self.doc_errors(json.dumps(doc))
        self.assertTrue(any("duplicate case id" in e for e in found), found)

    def test_notes_must_be_a_string(self):
        found = self.doc_errors(json.dumps(self.valid_doc(notes=[])))
        self.assertTrue(any("notes must be a string" in e for e in found),
                        found)


class DiscoveryTest(unittest.TestCase):
    """discover() — the rules that decide which files get checked at all.

    A file the validator never looks at is the worst failure mode here:
    the job stays green while the suite quietly shrinks. Each rule below
    exists to make that loud.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.skills = self.root / "skills"
        self.standalone = self.root / "evals" / "standalone"
        self.manifest = self.root / "standalone-skills.yaml"
        for attr, value in (("REPO_ROOT", self.root),
                            ("SKILLS_DIR", self.skills),
                            ("STANDALONE_EVALS_DIR", self.standalone),
                            ("STANDALONE_MANIFEST", self.manifest)):
            patcher = mock.patch.object(v, attr, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def add_skill(self, name: str, *, skill_yaml: bool = True,
                  evals: bool = True) -> Path:
        source = self.skills / name
        (source / "evals").mkdir(parents=True, exist_ok=True)
        if skill_yaml:
            (source / "skill.yaml").write_text("name: x\n", encoding="utf-8")
        path = source / "evals" / "evals.json"
        if evals:
            path.write_text("{}", encoding="utf-8")
        return path

    def add_manifest(self, *names: str) -> None:
        lines = []
        for name in names:
            lines += [f"{name}-entry:", "  frontmatter:", f"    name: {name}"]
        self.manifest.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def test_workflow_skill_is_discovered(self):
        self.add_skill("cw-thing")
        files, strays = v.discover()
        self.assertEqual([s for s, _ in ((e[1], e) for e in files)],
                         ["cw-thing"])
        self.assertEqual(strays, [])

    def test_underscore_dirs_are_skipped_like_build_py(self):
        self.add_skill("_example-skill-template")
        files, strays = v.discover()
        self.assertEqual((files, strays), ([], []))

    def test_missing_skill_yaml_is_a_location_error(self):
        self.add_skill("orphan", skill_yaml=False)
        files, _ = v.discover()
        self.assertEqual(len(files), 1)
        self.assertTrue(any("no skill.yaml" in e for e in files[0][2]),
                        files[0][2])

    def test_stray_file_beside_evals_json_is_an_error(self):
        self.add_skill("cw-thing")
        (self.skills / "cw-thing" / "evals" / "evals.json.bak").write_text(
            "{}", encoding="utf-8")
        _, strays = v.discover()
        self.assertEqual(len(strays), 1)
        self.assertIn("would never be validated", strays[0][1])

    def test_misnamed_standalone_file_is_a_stray(self):
        self.standalone.mkdir(parents=True)
        self.add_manifest("thing")
        (self.standalone / "thing.json").write_text("{}", encoding="utf-8")
        files, strays = v.discover()
        self.assertEqual(files, [])
        self.assertEqual(len(strays), 1)

    def test_standalone_file_must_match_the_manifest(self):
        self.standalone.mkdir(parents=True)
        self.add_manifest("declared")
        (self.standalone / "undeclared.evals.json").write_text(
            "{}", encoding="utf-8")
        files, _ = v.discover()
        self.assertEqual(len(files), 1)
        self.assertTrue(any("standalone-skills.yaml" in e
                            for e in files[0][2]), files[0][2])

    def test_declared_standalone_file_has_no_location_error(self):
        self.standalone.mkdir(parents=True)
        self.add_manifest("declared")
        (self.standalone / "declared.evals.json").write_text(
            "{}", encoding="utf-8")
        files, _ = v.discover()
        self.assertEqual(files[0][1:], ("declared", []))

    def test_manifest_scanner_reads_frontmatter_names(self):
        self.manifest.write_text(
            "# a comment\n"
            "first-entry:\n"
            "  plugin: some-plugin\n"
            "  frontmatter:\n"
            "    name: first\n"
            "    description: not a name\n"
            "second-entry:\n"
            "  frontmatter:\n"
            "    name: 'second'\n",
            encoding="utf-8")
        self.assertEqual(v.standalone_skill_names(), {"first", "second"})

    def test_manifest_scanner_tolerates_a_missing_manifest(self):
        self.assertEqual(v.standalone_skill_names(), set())

    def test_empty_tree_is_an_internal_error(self):
        # Exit 2, not 0: "nothing to check" almost always means the script
        # moved or the checkout is broken, which must not read as a pass.
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(v.main(), 2)


class AnnotationEscapingTest(unittest.TestCase):
    """Message content reaches a `::error` workflow command, so a raw
    newline in it would end the command and let the rest be parsed as a
    fresh one."""

    def test_percent_and_newlines_escaped(self):
        self.assertEqual(v._escape_annotation("100% done\r\nnext"),
                         "100%25 done%0D%0Anext")

    def test_percent_escaped_first(self):
        # If `\n` -> `%0A` ran before `%` -> `%25`, the escape's own percent
        # would be double-escaped into a literal `%250A`.
        self.assertEqual(v._escape_annotation("\n"), "%0A")


if __name__ == "__main__":
    unittest.main()
