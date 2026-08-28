"""Regression fixtures for the broken-shell-guard rule (APPSEC-3970).

WHY THIS EXISTS
---------------
The fail-closed kubeconfig gates are the whole TM-011 control: before a
cluster-mutating `helm install`, the agent resolves the target context from
the file it is about to hand `helm`, and `${KCFG:?...}` is what stops the
check from silently resolving against the AMBIENT kubeconfig when `KCFG` is
unset or empty (`kubectl --kubeconfig ""` exits 0 and answers from
~/.kube/config; `set -u` does not catch set-but-empty).

That guard shipped in #43 and #57 carrying an apostrophe in its message --
"set KCFG to this cluster's kubeconfig". Bash tokenizes the message of a
`${VAR:?message}` expansion as shell words even when the expansion sits
inside double quotes, so the apostrophe opened a single-quoted string that
never closed and the ENTIRE command became a syntax error. Not just when the
guard fired: on the happy path too, with `KCFG` correctly set. The gate could
never run at all.

That failure is quiet in the worst way. It looks fail-closed -- the command
errors, and the checkpoint prose says to STOP on an error -- but the real
consequence is an unrunnable gate, and an agent that cannot execute the
documented command improvises, most naturally by dropping the guard back to
the bare `"$KCFG"` that reintroduces the exact hole. The review that shipped
it verified a DIFFERENT, apostrophe-free message string than the one in the
diff, which is precisely why a human pass missed it twice.

So the cases below do not just assert the linter's regex. They shell out to
bash and adjudicate the rule against what bash actually does, so the rule
cannot drift into flagging strings bash accepts, or blessing ones it
rejects.

Run with `pytest tests/`.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_lint_module():
    """Import scripts/lint_skill_content.py by path, not as a package."""
    spec = importlib.util.spec_from_file_location(
        "_lint_under_test", REPO_ROOT / "scripts" / "lint_skill_content.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


lint = _load_lint_module()

# The guard exactly as it shipped to main in #43 (Traefik) and #57 (Step 5).
SHIPPED_BROKEN = (
    'kubectl --kubeconfig "${KCFG:?set KCFG to this cluster\'s kubeconfig '
    'before running this gate}" --context prod config view --minify'
)
SHIPPED_FIXED = (
    'kubectl --kubeconfig "${KCFG:?set KCFG to the kubeconfig for this cluster '
    'before running this gate}" --context prod config view --minify'
)


def lint_findings(tmp_path, monkeypatch, content: str) -> list[str]:
    """Run the content lint over one synthetic markdown file."""
    monkeypatch.setattr(lint, "REPO_ROOT", tmp_path)
    fixture = tmp_path / "fixture.md"
    fixture.write_text(content, encoding="utf-8")
    return lint.scan(fixture)


def guard_findings(tmp_path, monkeypatch, content: str) -> list[str]:
    """Only the broken-shell-guard findings, so other rules can't mask a miss."""
    return [f for f in lint_findings(tmp_path, monkeypatch, content)
            if "[broken-shell-guard]" in f]


def bash_parses(snippet: str) -> bool:
    """True if bash can parse the snippet at all (`bash -n`, no execution)."""
    return subprocess.run(
        ["bash", "-n"], input=snippet, text=True, capture_output=True
    ).returncode == 0


# --- the regression that motivated the rule ----------------------------------

def test_shipped_guard_was_a_bash_syntax_error():
    """The apostrophe form does not parse -- the gate could never have run."""
    assert not bash_parses(SHIPPED_BROKEN)


def test_fixed_guard_parses():
    """The reworded message is valid shell, so the gate actually executes."""
    assert bash_parses(SHIPPED_FIXED)


def test_lint_catches_the_shipped_guard(tmp_path, monkeypatch):
    findings = guard_findings(tmp_path, monkeypatch, SHIPPED_BROKEN + "\n")
    assert len(findings) == 1
    assert "KCFG" in findings[0]


def test_lint_passes_the_fixed_guard(tmp_path, monkeypatch):
    assert guard_findings(tmp_path, monkeypatch, SHIPPED_FIXED + "\n") == []


def test_rule_agrees_with_bash_on_the_pair(tmp_path, monkeypatch):
    """The linter's verdict must track bash's, not merely differ from it."""
    for snippet in (SHIPPED_BROKEN, SHIPPED_FIXED):
        flagged = bool(guard_findings(tmp_path, monkeypatch, snippet + "\n"))
        assert flagged is not bash_parses(snippet), snippet


# --- shapes that must NOT be flagged -----------------------------------------

@pytest.mark.parametrize("line", [
    # Every real guard shipping in skills/ and _snippets/ today.
    'kubectl --kubeconfig "${CKS_KCFG:?cks_kubeconfig_path is not set}" config current-context',
    'test -f "${CKS_KCFG:?cks_kubeconfig_path is not set in terraform.tfvars}"',
    'curl -H "Authorization: Bearer ${CW_API_TOKEN:?set CW_API_TOKEN first}" "$URL"',
    'echo "${CW_ORG_ID:?run.env not sourced}/${CW_BUCKET:?}/${CW_AZ:?}"',
    # Prose referring to a guard rather than spelling one out.
    'The `${KCFG:?...}` guard is load-bearing here.',
    # An apostrophe in ordinary prose on the same line as a clean guard.
    'Set the cluster\'s path, then `test -f "${KCFG:?set KCFG first}"`.',
])
def test_clean_guards_are_not_flagged(tmp_path, monkeypatch, line):
    assert guard_findings(tmp_path, monkeypatch, line + "\n") == []


def test_every_clean_fixture_actually_parses():
    """Guard against a fixture that is 'clean' only because the rule is blind."""
    for line in (
        'kubectl --kubeconfig "${CKS_KCFG:?cks_kubeconfig_path is not set}" config current-context',
        'echo "${CW_ORG_ID:?run.env not sourced}/${CW_BUCKET:?}/${CW_AZ:?}"',
    ):
        assert bash_parses(line), line


# --- the rule is UNBALANCED quotes, not quote characters ---------------------
#
# An even number of quotes parses fine (bash just strips them from the
# message), so flagging every quote would be a false positive. Only an odd
# count breaks the command. Each case below is adjudicated against bash.

@pytest.mark.parametrize("line, breaks", [
    # odd count -> string never closes -> syntax error
    ("echo \"${KCFG:?set this cluster's kubeconfig}\"", True),
    ('echo "${KCFG:?set the "target kubeconfig}"', True),
    # even count -> parses; bash strips the quotes from the message
    ("echo \"${KCFG:?set the 'target' kubeconfig}\"", False),
    ('echo "${KCFG:?set the "target" kubeconfig}"', False),
])
def test_rule_tracks_bash_on_quote_balance(tmp_path, monkeypatch, line, breaks):
    assert bash_parses(line) is not breaks, line
    assert bool(guard_findings(tmp_path, monkeypatch, line + "\n")) is breaks, line


def test_colonless_guard_is_covered(tmp_path, monkeypatch):
    """`${VAR?msg}` fires only on unset, but breaks on a stray quote identically."""
    line = 'echo "${KCFG?set this cluster\'s kubeconfig}"'
    assert not bash_parses(line)
    assert len(guard_findings(tmp_path, monkeypatch, line + "\n")) == 1


# --- a guard message must not execute anything -------------------------------

@pytest.mark.parametrize("message", [
    "run `pwd` first",
    "run $(id) first",
])
def test_command_substitution_in_message_is_flagged(tmp_path, monkeypatch, message):
    """A guard that runs a command when it fires is the opposite of fail-closed."""
    line = 'echo "${KCFG:?%s}"' % message
    assert bash_parses(line)  # parses -- which is exactly why a human misses it
    findings = guard_findings(tmp_path, monkeypatch, line + "\n")
    assert len(findings) == 1
    assert "command-substitutes" in findings[0]


def test_each_offending_guard_reports_its_own_line(tmp_path, monkeypatch):
    """Two broken gates in one file must not collapse into one finding."""
    content = SHIPPED_BROKEN + "\n\nsome prose\n\n" + SHIPPED_BROKEN + "\n"
    findings = guard_findings(tmp_path, monkeypatch, content)
    assert len(findings) == 2
    assert [f.split(":")[1] for f in findings] == ["1", "5"]


# --- the escape hatch still works --------------------------------------------

def test_allow_comment_suppresses_the_rule(tmp_path, monkeypatch):
    content = "<!-- content-lint-allow: broken-shell-guard -->\n" + SHIPPED_BROKEN + "\n"
    assert guard_findings(tmp_path, monkeypatch, content) == []


# --- the real tree stays clean -----------------------------------------------

def test_repo_content_has_no_broken_guards():
    """dist/ and plugins/ are build output; a stale mirror must fail here too."""
    findings = [
        f
        for name in lint.SCAN_DIRS
        if (REPO_ROOT / name).is_dir()
        for path in sorted((REPO_ROOT / name).rglob("*.md"))
        for f in lint.scan(path)
        if "[broken-shell-guard]" in f
    ]
    assert findings == [], "\n".join(findings)
