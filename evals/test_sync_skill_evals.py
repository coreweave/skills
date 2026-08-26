#!/usr/bin/env python3
"""Tests for evals/sync_skill_evals.py (APPSEC-3967).

The sync is what makes the seeds trustworthy, so its own derivations get
the same treatment the validator's rules do. These tests build a throwaway
git repo shaped like wandb/skills-evals — no network, no private checkout
— so they run anywhere the validator's tests do.

    python3 -m unittest discover -s evals -p 'test_*.py'
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
SYNC = REPO_ROOT / "evals" / "sync_skill_evals.py"

_spec = importlib.util.spec_from_file_location("sync_skill_evals", SYNC)
assert _spec and _spec.loader
s = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(s)


class DeriveIdsTest(unittest.TestCase):
    """Case ids come off the scenario directory name. The interesting case
    is a skill that ships both arms of one base name — cw-create-cluster
    has happy-mock AND happy-mock-judged, and collapsing both to `happy`
    would produce a duplicate id the validator then rejects."""

    def test_tier_suffix_stripped(self):
        ids = s.derive_ids([
            "tasks/cks/x/pushback-gpu-operator-mock-judged",
            "tasks/cks/x/complex-two-nodepools-mock",
        ])
        self.assertEqual(set(ids.values()),
                         {"pushback-gpu-operator", "complex-two-nodepools"})

    def test_both_arms_of_one_base_stay_distinct(self):
        ids = s.derive_ids([
            "tasks/cks/x/happy-mock",
            "tasks/cks/x/happy-mock-judged",
        ])
        self.assertEqual(ids["tasks/cks/x/happy-mock"], "happy")
        self.assertEqual(ids["tasks/cks/x/happy-mock-judged"], "happy-judged")
        self.assertEqual(len(set(ids.values())), 2)

    def test_unsuffixed_directory_keeps_its_name(self):
        ids = s.derive_ids(["tasks/cks/x/oddly-named"])
        self.assertEqual(ids["tasks/cks/x/oddly-named"], "oddly-named")


class FakeHarness:
    """A git repo shaped like wandb/skills-evals, committed on `master`."""

    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        self._git("init", "-q", "-b", "master")
        self._git("config", "user.email", "t@example.com")
        self._git("config", "user.name", "t")

    def _git(self, *args: str) -> None:
        subprocess.run(["git", "-C", str(self.root), *args], check=True,
                       capture_output=True)

    def add_scenario(self, path: str, key: dict, instruction: str = "") -> None:
        tests = self.root / path / "tests"
        tests.mkdir(parents=True, exist_ok=True)
        (tests / "scenario_answer_key.json").write_text(
            json.dumps(key), encoding="utf-8")
        (self.root / path / "instruction.md").write_text(
            instruction, encoding="utf-8")

    def commit(self) -> None:
        self._git("add", "-A")
        self._git("commit", "-qm", "scenarios")


KEY_JUDGED = {
    "user_request": "Do the judged thing.",
    "expect": [{"resource": "clusters", "match": {"name": "x"}}],
    "rubric_criteria": [{"key": "honest_final_report", "desc": "Honest."}],
}

KEY_PLAIN = {
    "user_request": "Do the deterministic thing.",
    "expect": [{"resource": "clusters", "match": {"name": "y"}}],
}


class SyncTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)

        self.harness = FakeHarness(root / "harness")
        self.repo = root / "repo"
        (self.repo / "skills" / "cw-thing" / "evals").mkdir(parents=True)
        (self.repo / "skills" / "cw-thing" / "skill.yaml").write_text(
            "name: cw-thing\n", encoding="utf-8")
        (self.repo / "evals" / "standalone").mkdir(parents=True)

        for attr, value in (
            ("REPO_ROOT", self.repo),
            ("SKILLS_DIR", self.repo / "skills"),
            ("STANDALONE_EVALS_DIR", self.repo / "evals" / "standalone"),
        ):
            patcher = mock.patch.object(s, attr, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = mock.patch.object(s, "standalone_skill_names",
                                    lambda: {"standalone-thing"})
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_sync(self, *argv: str) -> int:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            with mock.patch("sys.argv", ["sync", "--harness",
                                         str(self.harness.root),
                                         "--ref", "master", *argv]):
                code = s.main()
        self.output = out.getvalue() + err.getvalue()
        return code

    def seed(self, name: str = "cw-thing") -> dict:
        return json.loads(
            (self.repo / "skills" / name / "evals" / "evals.json")
            .read_text(encoding="utf-8"))

    # -- derivations ---------------------------------------------------

    def test_tier_follows_the_rubric_not_the_directory_name(self):
        """The harness's own rule: a scenario is judged exactly when its
        answer key declares a non-empty rubric. A `-mock` directory with a
        rubric IS judged (upstream has several), and a `-mock-judged`
        directory without one is not."""
        self.harness.add_scenario("tasks/cks/cw-thing/rubric-in-plain-mock",
                                  KEY_JUDGED)
        self.harness.add_scenario("tasks/cks/cw-thing/no-rubric-mock-judged",
                                  KEY_PLAIN)
        self.harness.commit()
        self.assertEqual(self.run_sync("--write"), 0)

        tiers = {c["id"]: c["tier"] for c in self.seed()["cases"]}
        self.assertEqual(tiers["rubric-in-plain"], "mock-judged")
        self.assertEqual(tiers["no-rubric"], "mock")

    def test_judged_cases_gate_both_judges_and_plain_ones_do_not(self):
        self.harness.add_scenario("tasks/cks/cw-thing/a-mock-judged",
                                  KEY_JUDGED)
        self.harness.add_scenario("tasks/cks/cw-thing/b-mock", KEY_PLAIN)
        self.harness.commit()
        self.run_sync("--write")

        gates = {c["id"]: c["reward_gates"] for c in self.seed()["cases"]}
        self.assertEqual(gates["a"],
                         {"outcome": 1.0, "tq_opus": 0.7, "tq_sonnet": 0.7})
        self.assertEqual(gates["b"], {"outcome": 1.0})

    def test_rubric_is_omitted_entirely_on_a_plain_tier(self):
        # The validator rejects the key at all there, empty or not.
        self.harness.add_scenario("tasks/cks/cw-thing/b-mock", KEY_PLAIN)
        self.harness.commit()
        self.run_sync("--write")
        self.assertNotIn("rubric_criteria", self.seed()["cases"][0])

    def test_user_request_falls_back_to_instruction_md(self):
        key = {k: val for k, val in KEY_JUDGED.items() if k != "user_request"}
        self.harness.add_scenario("tasks/cks/cw-thing/a-mock-judged", key,
                                  instruction="  What the agent is asked.\n")
        self.harness.commit()
        self.run_sync("--write")
        self.assertEqual(self.seed()["cases"][0]["user_request"],
                         "What the agent is asked.")

    def test_real_tier_and_templates_are_never_emitted(self):
        self.harness.add_scenario("tasks/cks/cw-thing/a-mock-judged",
                                  KEY_JUDGED)
        self.harness.add_scenario("tasks/cks/cw-thing/a-real-judged",
                                  KEY_JUDGED)
        self.harness.add_scenario("tasks/cks/cw-thing/_template", KEY_JUDGED)
        self.harness.commit()
        self.run_sync("--write")
        self.assertEqual([c["id"] for c in self.seed()["cases"]], ["a"])

    # -- blocking ------------------------------------------------------

    def test_pushback_scenarios_default_to_blocking(self):
        """APPSEC-3967 names the pushback scenarios as the ones that must
        block a merge, so a newly-added one is blocking without anyone
        having to remember."""
        self.harness.add_scenario(
            "tasks/cks/cw-thing/pushback-something-mock-judged", KEY_JUDGED)
        self.harness.add_scenario("tasks/cks/cw-thing/happy-mock-judged",
                                  KEY_JUDGED)
        self.harness.commit()
        self.run_sync("--write")
        blocking = {c["id"]: c["blocking"] for c in self.seed()["cases"]}
        self.assertEqual(blocking, {"pushback-something": True,
                                    "happy": False})

    def test_hand_set_blocking_survives_a_resync(self):
        self.harness.add_scenario("tasks/cks/cw-thing/happy-mock-judged",
                                  KEY_JUDGED)
        self.harness.commit()
        self.run_sync("--write")

        path = self.repo / "skills" / "cw-thing" / "evals" / "evals.json"
        doc = json.loads(path.read_text(encoding="utf-8"))
        doc["cases"][0]["blocking"] = True
        path.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8")

        self.run_sync("--write")
        self.assertTrue(self.seed()["cases"][0]["blocking"])

    def test_hand_renamed_case_id_survives_a_resync(self):
        self.harness.add_scenario("tasks/cks/cw-thing/happy-mock-judged",
                                  KEY_JUDGED)
        self.harness.commit()
        self.run_sync("--write")

        path = self.repo / "skills" / "cw-thing" / "evals" / "evals.json"
        doc = json.loads(path.read_text(encoding="utf-8"))
        doc["cases"][0]["id"] = "renamed-by-hand"
        path.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8")

        self.run_sync("--write")
        self.assertEqual(self.seed()["cases"][0]["id"], "renamed-by-hand")

    # -- drift detection -----------------------------------------------

    def test_check_mode_passes_when_in_sync_and_writes_nothing(self):
        self.harness.add_scenario("tasks/cks/cw-thing/a-mock-judged",
                                  KEY_JUDGED)
        self.harness.commit()
        self.run_sync("--write")
        before = (self.repo / "skills" / "cw-thing" / "evals" / "evals.json"
                  ).read_text(encoding="utf-8")

        self.assertEqual(self.run_sync(), 0)
        self.assertEqual(
            (self.repo / "skills" / "cw-thing" / "evals" / "evals.json"
             ).read_text(encoding="utf-8"), before)

    def test_check_mode_fails_when_an_assertion_is_dropped(self):
        """The regression this whole script exists for: a blocking pushback
        case that quietly lost the `expect` entry making it a pushback
        case still passes the shape lint."""
        self.harness.add_scenario(
            "tasks/cks/cw-thing/pushback-something-mock-judged",
            {**KEY_JUDGED, "expect": [
                {"resource": "buckets", "match": {"name": "b"}},
                {"resource": "bucket_policies", "match": {"public": True},
                 "exists": False},
            ]})
        self.harness.commit()
        self.run_sync("--write")

        path = self.repo / "skills" / "cw-thing" / "evals" / "evals.json"
        doc = json.loads(path.read_text(encoding="utf-8"))
        doc["cases"][0]["expect"] = doc["cases"][0]["expect"][:1]
        path.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8")

        self.assertEqual(self.run_sync(), 1)
        self.assertIn("::error file=", self.output)

    def test_check_mode_fails_on_a_scenario_that_no_longer_exists(self):
        """A renamed upstream directory leaves a seed pointing at nothing.
        The validator cannot see this — it never checks out the harness."""
        self.harness.add_scenario("tasks/cks/cw-thing/renamed-mock-judged",
                                  KEY_JUDGED)
        self.harness.commit()
        self.run_sync("--write")

        path = self.repo / "skills" / "cw-thing" / "evals" / "evals.json"
        doc = json.loads(path.read_text(encoding="utf-8"))
        doc["cases"][0]["scenario"] = "tasks/cks/cw-thing/old-name-mock-judged"
        path.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8")

        self.assertEqual(self.run_sync(), 1)
        self.assertIn("old-name-mock-judged", self.output)

    def test_check_mode_fails_on_an_unseeded_scenario(self):
        self.harness.add_scenario("tasks/cks/cw-thing/a-mock-judged",
                                  KEY_JUDGED)
        self.harness.commit()
        self.run_sync("--write")

        self.harness.add_scenario("tasks/cks/cw-thing/b-mock-judged",
                                  KEY_JUDGED)
        self.harness.commit()
        self.assertEqual(self.run_sync(), 1)
        self.assertIn("b-mock-judged", self.output)

    # -- routing -------------------------------------------------------

    def test_standalone_skill_writes_to_the_standalone_layout(self):
        self.harness.add_scenario(
            "tasks/platform/standalone-thing/happy-mock-judged", KEY_JUDGED)
        self.harness.commit()
        self.assertEqual(self.run_sync("--write"), 0)
        path = (self.repo / "evals" / "standalone"
                / "standalone-thing.evals.json")
        self.assertTrue(path.is_file())
        self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["skill"],
                         "standalone-thing")

    def test_unshipped_harness_skill_is_reported_not_silently_dropped(self):
        self.harness.add_scenario("tasks/cks/not-in-this-repo/a-mock-judged",
                                  KEY_JUDGED)
        self.harness.commit()
        self.assertEqual(self.run_sync(), 0)
        self.assertIn("not-in-this-repo", self.output)

    def test_missing_harness_checkout_is_an_internal_error(self):
        with self.assertRaises(s.HarnessError):
            s.resolve_harness(str(self.repo / "nope"))


if __name__ == "__main__":
    unittest.main()
