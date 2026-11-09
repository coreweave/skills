"""Regression tests for the environment-protection guard in trigger-evals.yml.

WHY THESE EXIST
---------------
The guard step is the only thing standing between `ANTHROPIC_API_KEY` and
PR-authored code when the `evals-pr` environment has no protection rules —
which is the state the environment was actually in when it was
auto-created by this workflow's own first run. The failure mode it defends
against is silent: an unprotected environment behaves exactly like a
protected one from inside the job, so nothing but an explicit assertion can
tell them apart.

The guard is bash embedded in YAML, so the test extracts the script from the
workflow and runs it for real against a stub GitHub API on localhost. No
network, no credentials, no live environment needed; the assertions are on
the script's exit code and its ::error annotations.
"""
import http.server
import json
import os
import shutil
import subprocess
import threading
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = REPO_ROOT / ".github/workflows/trigger-evals.yml"
GUARD_STEP_NAME = "Refuse to run unless the environment is really protected"

pytestmark = pytest.mark.skipif(
    shutil.which("jq") is None or shutil.which("curl") is None,
    reason="the guard script shells out to jq and curl",
)


def _workflow():
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _guard_script():
    steps = _workflow()["jobs"]["trigger-evals"]["steps"]
    matching = [s for s in steps if s.get("name") == GUARD_STEP_NAME]
    assert len(matching) == 1, f"expected exactly one guard step, got {len(matching)}"
    # It must run before anything checks out the PR's code, or it is deciding
    # with attacker-controlled files already on disk.
    assert steps[0] is matching[0], "the guard step must be the job's FIRST step"
    return matching[0]["run"]


PROTECTED_PR_ENV = {
    "name": "evals-pr",
    "can_admins_bypass": False,
    "protection_rules": [
        {
            "id": 1,
            "node_id": "x",
            "type": "required_reviewers",
            "prevent_self_review": True,
            "reviewers": [{"type": "User", "reviewer": {"login": "maintainer"}}],
        }
    ],
    "deployment_branch_policy": None,
}
UNPROTECTED_PR_ENV = {
    "name": "evals-pr",
    "can_admins_bypass": True,
    "protection_rules": [],
    "deployment_branch_policy": None,
}
PROTECTED_MAIN_ENV = {
    "name": "evals-main",
    "can_admins_bypass": False,
    "protection_rules": [{"id": 2, "node_id": "y", "type": "branch_policy"}],
    "deployment_branch_policy": {"protected_branches": False, "custom_branch_policies": True},
}


def _approval(login, environments=("evals-pr",), state="approved"):
    return {
        "state": state,
        "comment": "",
        "user": {"login": login},
        "environments": [{"name": n} for n in environments],
    }


class _StubAPI:
    """Serves just the two endpoints the guard reads."""

    def __init__(self, routes):
        # routes: path suffix -> (status, payload)
        self.routes = routes
        handler = self._handler()
        self.httpd = http.server.HTTPServer(("127.0.0.1", 0), handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self):
        host, port = self.httpd.server_address[:2]
        return f"http://{host}:{port}"

    def _handler(self):
        routes = self.routes

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_GET(self):
                for suffix, (status, payload) in routes.items():
                    if self.path.endswith(suffix):
                        body = json.dumps(payload).encode()
                        self.send_response(status)
                        self.send_header("Content-Type", "application/json")
                        self.send_header("Content-Length", str(len(body)))
                        self.end_headers()
                        self.wfile.write(body)
                        return
                self.send_response(404)
                self.end_headers()
                self.wfile.write(b"{}")

        return Handler

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def run_guard(*, env_response, approvals_response, event_name="pull_request",
              ref="refs/pull/49/merge", expected_env="evals-pr",
              pr_author="contributor"):
    """Execute the real guard script against a stub API. Returns (rc, output)."""
    routes = {}
    if env_response is not None:
        routes[f"/environments/{expected_env}"] = env_response
    if approvals_response is not None:
        routes["/approvals"] = approvals_response
    stub = _StubAPI(routes)
    try:
        env = dict(os.environ)
        env.update({
            "GH_TOKEN": "stub-token",
            "GITHUB_API_URL": stub.url,
            "GITHUB_REPOSITORY": "coreweave/skills",
            "GITHUB_EVENT_NAME": event_name,
            "GITHUB_REF": ref,
            "GITHUB_RUN_ID": "12345",
            "EXPECTED_ENV": expected_env,
            "PR_AUTHOR": pr_author,
        })
        cp = subprocess.run(["bash", "-c", _guard_script()], env=env,
                            capture_output=True, text=True, timeout=120)
        return cp.returncode, cp.stdout + cp.stderr
    finally:
        stub.close()


# --- the state the repo was actually in when this guard was written --------

def test_unprotected_environment_fails_closed():
    rc, out = run_guard(
        env_response=(200, UNPROTECTED_PR_ENV),
        approvals_response=(200, []),
    )
    assert rc == 1
    assert "NO protection rules" in out
    assert "Required reviewers" in out


def test_unprotected_environment_warns_about_admin_bypass():
    _rc, out = run_guard(
        env_response=(200, UNPROTECTED_PR_ENV),
        approvals_response=(200, []),
    )
    assert "can_admins_bypass=true" in out


# --- the configuration the setup notes tell a maintainer to create ---------

def test_protected_environment_with_third_party_approval_passes():
    rc, out = run_guard(
        env_response=(200, PROTECTED_PR_ENV),
        approvals_response=(200, [_approval("maintainer")]),
    )
    assert rc == 0, out
    assert "environment protection verified" in out


# --- the holes the guard is specifically there to close -------------------

def test_self_approval_is_rejected():
    rc, out = run_guard(
        env_response=(200, PROTECTED_PR_ENV),
        approvals_response=(200, [_approval("contributor")]),
        pr_author="contributor",
    )
    assert rc == 1
    assert "Self-approval is not review" in out


def test_prevent_self_review_disabled_is_rejected():
    env = json.loads(json.dumps(PROTECTED_PR_ENV))
    env["protection_rules"][0]["prevent_self_review"] = False
    rc, out = run_guard(
        env_response=(200, env),
        approvals_response=(200, [_approval("maintainer")]),
    )
    assert rc == 1
    assert "allows self-review" in out


def test_missing_prevent_self_review_field_warns_but_does_not_fail():
    """The field is documented but not a required response field."""
    env = json.loads(json.dumps(PROTECTED_PR_ENV))
    del env["protection_rules"][0]["prevent_self_review"]
    rc, out = run_guard(
        env_response=(200, env),
        approvals_response=(200, [_approval("maintainer")]),
    )
    assert rc == 0, out
    assert "Could not read 'prevent_self_review'" in out


def test_wait_timer_only_is_not_a_human_gate():
    env = {
        "name": "evals-pr",
        "can_admins_bypass": False,
        "protection_rules": [{"id": 3, "node_id": "z", "type": "wait_timer", "wait_timer": 30}],
        "deployment_branch_policy": None,
    }
    rc, out = run_guard(
        env_response=(200, env),
        approvals_response=(200, [_approval("maintainer")]),
    )
    assert rc == 1
    assert "none of type 'required_reviewers'" in out


def test_approval_for_a_different_environment_does_not_count():
    """A PR that rewrites `environment:` to name evals-main must not pass."""
    rc, out = run_guard(
        env_response=(200, PROTECTED_PR_ENV),
        approvals_response=(200, [_approval("maintainer", environments=("evals-main",))]),
    )
    assert rc == 1
    assert "WITHOUT any recorded approval" in out


def test_pending_state_is_not_an_approval():
    rc, out = run_guard(
        env_response=(200, PROTECTED_PR_ENV),
        approvals_response=(200, [_approval("maintainer", state="pending")]),
    )
    assert rc == 1
    assert "WITHOUT any recorded approval" in out


# --- unreadable is unverified is failure ----------------------------------

def test_unreadable_environment_endpoint_is_a_failure():
    rc, out = run_guard(
        env_response=(403, {"message": "Resource not accessible by integration"}),
        approvals_response=(200, [_approval("maintainer")]),
    )
    assert rc == 1
    assert "config_verified=0" in out
    assert "HTTP 403" in out


def test_unreadable_approvals_endpoint_is_a_failure():
    rc, out = run_guard(
        env_response=(200, PROTECTED_PR_ENV),
        approvals_response=(403, {"message": "Resource not accessible by integration"}),
    )
    assert rc == 1
    assert "approval_verified=0" in out


def test_absent_environment_is_a_failure():
    """Naming a non-existent environment auto-creates an unprotected one."""
    rc, out = run_guard(
        env_response=(404, {"message": "Not Found"}),
        approvals_response=(200, []),
    )
    assert rc == 1


# --- the unattended (push/schedule) arm -----------------------------------

def test_main_environment_with_branch_policy_passes():
    rc, out = run_guard(
        env_response=(200, PROTECTED_MAIN_ENV),
        approvals_response=None,
        event_name="push",
        ref="refs/heads/main",
        expected_env="evals-main",
    )
    assert rc == 0, out


def test_main_environment_without_branch_policy_fails():
    env = json.loads(json.dumps(PROTECTED_MAIN_ENV))
    env["deployment_branch_policy"] = None
    rc, out = run_guard(
        env_response=(200, env),
        approvals_response=None,
        event_name="push",
        ref="refs/heads/main",
        expected_env="evals-main",
    )
    assert rc == 1
    assert "no deployment-branch policy" in out


def test_unattended_environment_from_a_non_main_ref_fails():
    rc, out = run_guard(
        env_response=(200, PROTECTED_MAIN_ENV),
        approvals_response=None,
        event_name="push",
        ref="refs/heads/attacker",
        expected_env="evals-main",
    )
    assert rc == 1
    assert "may only be used from refs/heads/main" in out


# --- the two copies of the environment expression must not drift ----------

def test_expected_env_matches_the_environment_expression():
    job = _workflow()["jobs"]["trigger-evals"]
    declared = job["environment"]["name"]
    guard = [s for s in job["steps"] if s.get("name") == GUARD_STEP_NAME][0]
    assert guard["env"]["EXPECTED_ENV"] == declared, (
        "the guard verifies EXPECTED_ENV, so if it drifts from the "
        "`environment:` the job actually uses, the guard checks the wrong "
        "environment"
    )


def test_guard_job_can_read_the_actions_api():
    job = _workflow()["jobs"]["trigger-evals"]
    assert job["permissions"]["actions"] == "read", (
        "the run-approval history lives under /actions, so the job needs "
        "actions: read or the guard cannot verify anything"
    )
