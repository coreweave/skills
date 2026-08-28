"""Regression tests for the correctness-status gate (APPSEC-3967).

WHY THESE EXIST
---------------
This check is the only thing that stops a skill change merging without the
pushback/correctness evals having run on it. Its failure modes are all silent:
a relevance rule that quietly exempts a real skill change, an absent status
read as "fine", a forged status accepted because nobody checks who posted it.
None of those look broken from the outside — the check goes green either way —
so they need explicit assertions.

Pure: no network, no credentials. The HTTP call is the one thing not covered
here; everything that DECIDES is a pure function taking the payload.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "require_correctness_status.py"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "correctness-required.yml"

sys.path.insert(0, str(REPO_ROOT / "scripts"))
import require_correctness_status as rcs  # noqa: E402

BOT = {"login": "skills-evals-bot"}
ALLOWED = {"skills-evals-bot"}
SHA = "a" * 40


def status(state="success", creator=BOT, context=rcs.STATUS_CONTEXT, **kw):
    return {"context": context, "state": state, "creator": creator,
            "description": "6/6 cases passed", "target_url": "https://example/run/1", **kw}


# ── relevance: what needs a correctness run ─────────────────────────────────


@pytest.mark.parametrize("path", [
    "skills/cw-create-cluster/skill.yaml",
    "skills/cw-create-cluster/body.md",
    "skills/cw-create-cluster/evals/evals.json",
    "dist/cw-create-cluster/SKILL.md",
    "evals/standalone/cw-add-users.evals.json",
])
def test_skill_changes_require_a_correctness_run(path):
    required, skills, _ = rcs.needs_correctness([path])
    assert required
    assert skills


@pytest.mark.parametrize("path", [
    "_snippets/checkpoint.md",
    "_shared-scripts/probe.sh",
    "standalone-skills.yaml",
    "build.py",
])
def test_shared_sources_require_a_run_even_though_they_name_no_skill(path):
    # These can change every skill at once. Exempting them would be the widest
    # possible hole in the gate.
    required, skills, fanout = rcs.needs_correctness([path])
    assert required
    assert skills == []
    assert fanout == [path]


@pytest.mark.parametrize("path", [
    "README.md",
    "docs/CONTRIBUTING.md",
    ".github/workflows/build.yml",
    "tests/test_lint_skill_content.py",
    "skills/_template/body.md",          # build.py skips _-prefixed dirs
    "dist/_scratch/SKILL.md",
    "evals/standalone/_draft.evals.json",
    "evals/trigger-evals.jsonl",         # the other eval layer, gated elsewhere
])
def test_non_skill_changes_do_not_require_a_run(path):
    required, _, _ = rcs.needs_correctness([path])
    assert not required


def test_a_mixed_pr_still_requires_a_run():
    required, skills, _ = rcs.needs_correctness(
        ["README.md", "skills/cw-create-cluster/body.md"])
    assert required
    assert skills == ["cw-create-cluster"]


def test_an_empty_diff_requires_nothing():
    assert rcs.needs_correctness([])[0] is False


# ── presence and state ──────────────────────────────────────────────────────


def test_a_green_status_from_an_allowed_creator_satisfies_the_gate():
    assert rcs.check_status([status()], ALLOWED, SHA) == []


def test_an_absent_status_is_a_failure_not_a_skip():
    problems = rcs.check_status([], ALLOWED, SHA)
    assert len(problems) == 1
    assert "have not been run" in problems[0]
    assert "correctness-gate" in problems[0], "the error must say how to fix it"


def test_a_status_for_some_other_context_does_not_count():
    problems = rcs.check_status([status(context="ci/other")], ALLOWED, SHA)
    assert len(problems) == 1
    assert "have not been run" in problems[0]


@pytest.mark.parametrize("state", ["failure", "error"])
def test_a_red_status_fails_and_carries_the_evidence_link(state):
    problems = rcs.check_status([status(state=state)], ALLOWED, SHA)
    assert len(problems) == 1
    assert "https://example/run/1" in problems[0]


def test_a_pending_status_is_not_a_pass():
    # A gate that is still running has not passed, and a gate that died without
    # reporting leaves `pending` behind forever.
    problems = rcs.check_status([status(state="pending")], ALLOWED, SHA)
    assert len(problems) == 1
    assert "PENDING" in problems[0]


def test_an_unrecognised_state_is_refused_rather_than_guessed():
    problems = rcs.check_status([status(state="succeeded")], ALLOWED, SHA)
    assert len(problems) == 1
    assert "unrecognised state" in problems[0]


def test_the_newest_status_wins():
    # GitHub returns newest-first and keeps history; a red that was re-dispatched
    # to green must pass, or nobody could ever fix a failing gate.
    payload = [status(state="success"), status(state="failure")]
    assert rcs.check_status(payload, ALLOWED, SHA) == []


def test_an_older_green_does_not_rescue_a_newer_red():
    payload = [status(state="failure"), status(state="success")]
    assert rcs.check_status(payload, ALLOWED, SHA)


# ── authorship: the part that makes it a control ────────────────────────────


def test_a_status_from_an_unexpected_login_is_rejected():
    # The forgery case: write access to this repo is enough to POST a green
    # status, so the author is what distinguishes a verdict from a claim.
    problems = rcs.check_status([status(creator={"login": "some-contributor"})], ALLOWED, SHA)
    assert len(problems) == 1
    assert "not in the allowlist" in problems[0]


def test_a_forged_green_is_rejected_even_though_its_state_is_success():
    problems = rcs.check_status([status(state="success", creator={"login": "attacker"})],
                                ALLOWED, SHA)
    assert problems, "a green state must not excuse an unknown author"


def test_a_status_with_no_creator_is_rejected():
    for creator in (None, {}, {"login": None}):
        assert rcs.check_status([status(creator=creator)], ALLOWED, SHA)


def test_an_unset_allowlist_fails_closed_rather_than_allowing_anyone():
    problems = rcs.check_status([status()], set(), SHA)
    assert len(problems) == 1
    assert "refusing to accept any status" in problems[0]


def test_multiple_allowed_creators_are_supported():
    assert rcs.check_status([status()], {"other-bot", "skills-evals-bot"}, SHA) == []


@pytest.mark.parametrize("raw,expected", [
    ("a,b", {"a", "b"}),
    (" a , b ", {"a", "b"}),
    ("a\nb", {"a", "b"}),
    ("", set()),
    (None, set()),
])
def test_creator_allowlist_parsing(raw, expected):
    assert rcs.parse_creators(raw) == expected


# ── malformed input must never read as a pass ───────────────────────────────


@pytest.mark.parametrize("payload", [None, {}, "not a list", [None], ["a string"], [[]]])
def test_a_malformed_payload_is_not_a_pass(payload):
    assert rcs.check_status(payload, ALLOWED, SHA)


# ── end to end through the CLI ──────────────────────────────────────────────


def test_cli_exits_zero_when_no_skill_changed(tmp_path, capsys):
    f = tmp_path / "paths.txt"
    f.write_text("README.md\ndocs/x.md\n")
    code = rcs.main(["--repo", "coreweave/skills", "--sha", SHA,
                     "--changed-paths-file", str(f)])
    assert code == 0
    assert "not required" in capsys.readouterr().out


def test_cli_fails_without_a_token_when_a_skill_changed(tmp_path, monkeypatch, capsys):
    # No token means the check cannot establish that the gate passed, which is
    # a failure, not a pass.
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    f = tmp_path / "paths.txt"
    f.write_text("skills/cw-create-cluster/body.md\n")
    code = rcs.main(["--repo", "coreweave/skills", "--sha", SHA,
                     "--changed-paths-file", str(f)])
    assert code == 1
    assert "GITHUB_TOKEN is not set" in capsys.readouterr().out


def test_a_pr_too_large_to_enumerate_requires_a_run(tmp_path, monkeypatch, capsys):
    # The API caps the file list at 3000. "Too big to check" must not become
    # "allowed through", so a truncated list forces the requirement on.
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    monkeypatch.setattr(rcs, "fetch_changed_paths",
                        lambda repo, pr, token: (["README.md"], True))
    monkeypatch.setattr(rcs, "fetch_statuses", lambda repo, sha, token: [])
    code = rcs.main(["--repo", "coreweave/skills", "--sha", SHA, "--pr-number", "7"])
    out = capsys.readouterr().out
    assert code == 1, "a truncated file list must not exempt the PR"
    assert "more files than the API will list" in out


def test_a_listing_failure_fails_closed(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("GITHUB_TOKEN", "t")

    def boom(repo, pr, token):
        raise RuntimeError("API said no")

    monkeypatch.setattr(rcs, "fetch_changed_paths", boom)
    assert rcs.main(["--repo", "coreweave/skills", "--sha", SHA, "--pr-number", "7"]) == 1
    assert "API said no" in capsys.readouterr().out


def test_the_two_path_sources_are_mutually_exclusive():
    with pytest.raises(SystemExit):
        rcs.main(["--repo", "r", "--sha", SHA, "--pr-number", "1",
                  "--changed-paths-file", "x"])
    with pytest.raises(SystemExit):
        rcs.main(["--repo", "r", "--sha", SHA])


def test_cli_rejects_an_unreadable_paths_file(tmp_path, capsys):
    code = rcs.main(["--repo", "coreweave/skills", "--sha", SHA,
                     "--changed-paths-file", str(tmp_path / "nope.txt")])
    assert code == 2


def test_the_script_is_stdlib_only():
    # The job runs it with no pip install; a third-party import would only fail
    # in CI, on the PR that needed the gate most.
    src = SCRIPT.read_text(encoding="utf-8")
    banned = ("import requests", "import yaml", "from requests", "import httpx")
    assert not any(b in src for b in banned)


def test_the_script_runs_standalone():
    p = subprocess.run([sys.executable, str(SCRIPT), "--help"],
                       capture_output=True, text=True)
    assert p.returncode == 0


# ── the workflow wiring the ruleset depends on ──────────────────────────────


def test_the_workflow_job_id_matches_the_required_check_name():
    # The ruleset requires the check by name, and the job id IS the name.
    # Renaming the job silently stops enforcing; this is the tripwire.
    import yaml
    wf = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    assert "require-correctness" in wf["jobs"], (
        "the ruleset requires a check named `require-correctness`")


def test_the_workflow_asks_for_no_more_permission_than_it_needs():
    import yaml
    wf = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    assert wf["permissions"] == {"contents": "read", "statuses": "read"}


def test_the_workflow_reads_the_head_sha_not_the_merge_sha():
    # github.sha on a pull_request is the merge commit; the status is posted on
    # the head. Checking the wrong SHA would fail every PR, forever.
    src = WORKFLOW.read_text(encoding="utf-8")
    assert "github.event.pull_request.head.sha" in src


def test_the_workflow_runs_the_gate_from_the_base_not_the_pr():
    # THE load-bearing assertion. If this job ever checks out the PR head, the
    # PR can edit this very script to `exit 0` in the same diff that changes a
    # skill, and the gate passes itself. The checkout must pin the base.
    src = WORKFLOW.read_text(encoding="utf-8")
    assert "ref: ${{ github.event.pull_request.base.sha }}" in src
    assert "ref: ${{ github.event.pull_request.head.sha }}" not in src


def test_a_missing_base_gate_fails_rather_than_falling_back_to_the_pr_copy():
    # The bootstrap case. Falling back to the PR's own copy when the base has
    # none would reopen the bypass permanently, so the workflow exits 1 with an
    # explanation instead. Asserted on the workflow text because the branch is
    # shell, and its whole point is what it does NOT do.
    src = WORKFLOW.read_text(encoding="utf-8")
    assert 'if [ ! -f "$gate" ]; then' in src
    assert "exit 1" in src
    assert "::error title=correctness status::" in src


def test_the_workflow_does_not_fetch_the_pr_ref():
    # A base-only checkout has no credential to fetch with (this repo is
    # internal), which is what broke the first revision. The file list comes
    # from the API instead; a reintroduced `git fetch`/`git diff` would fail
    # closed on every PR.
    # Match executed lines, not prose: the comment above the checkout step
    # explains why there is no git diff, and that mention is not a violation.
    commands = [ln.strip() for ln in WORKFLOW.read_text(encoding="utf-8").splitlines()
                if not ln.strip().startswith("#")]
    assert not any(c.startswith(("git fetch", "git diff")) for c in commands)
