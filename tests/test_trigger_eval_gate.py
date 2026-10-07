"""Execute the workflow's actual detector and aggregate scripts without secrets."""
import itertools
import os
from pathlib import Path
import subprocess

import pytest
import yaml

WORKFLOW = Path(__file__).resolve().parents[1] / '.github/workflows/trigger-evals.yml'
# BaseLoader preserves GitHub's `on` key rather than reading it as YAML 1.1 true.
CONFIG = yaml.load(WORKFLOW.read_text(), Loader=yaml.BaseLoader)
JOBS = CONFIG['jobs']
DETECT = JOBS['changes']['steps'][1]['run']
AGGREGATE = JOBS['require-trigger-evals']['steps'][0]['run']


def test_workflow_always_reports_and_preserves_environment_gate():
    assert CONFIG['on']['pull_request'] in ('', {})
    gate = JOBS['require-trigger-evals']
    assert gate['if'] == '${{ always() }}'
    assert set(gate['needs']) == {'changes', 'preflight', 'trigger-evals'}
    assert JOBS['preflight']['needs'] == 'changes'
    assert JOBS['preflight']['if'] == "needs.changes.outputs.relevant == 'true'"
    assert JOBS['trigger-evals']['needs'] == 'preflight'
    assert JOBS['trigger-evals']['environment']['name'] == (
        "${{ github.event_name == 'pull_request' && 'evals-pr' || 'evals-main' }}"
    )
    assert 'head.repo.full_name == github.repository' in JOBS['trigger-evals']['if']


RESULTS = ('success', 'failure', 'cancelled', 'skipped')


@pytest.mark.parametrize('changes,relevant,preflight,evals', [
    *itertools.product(RESULTS, ('true', 'false', ''), RESULTS, RESULTS),
])
def test_aggregate_fails_closed(changes, relevant, preflight, evals):
    result = subprocess.run(['bash', '-e', '-o', 'pipefail', '-c', AGGREGATE],
                            env={**os.environ, 'CHANGES_RESULT': changes,
                                 'RELEVANT': relevant, 'PREFLIGHT_RESULT': preflight,
                                 'EVAL_RESULT': evals}, capture_output=True, text=True)
    expected = changes == 'success' and (
        (relevant, preflight, evals) == ('false', 'skipped', 'skipped') or
        (relevant, preflight, evals) == ('true', 'success', 'success')
    )
    assert (result.returncode == 0) == expected, result.stdout + result.stderr


def git(repo, *args):
    return subprocess.check_output(['git', '-C', str(repo), *args], text=True).strip()


@pytest.fixture
def repo(tmp_path):
    git(tmp_path, 'init', '-q', '-b', 'main')
    git(tmp_path, 'config', 'user.email', 'test@example.invalid')
    git(tmp_path, 'config', 'user.name', 'Test')
    (tmp_path / 'README.md').write_text('base\n')
    (tmp_path / 'evals').mkdir()
    (tmp_path / 'evals/corpus.jsonl').write_text('base\n')
    git(tmp_path, 'add', '.')
    git(tmp_path, 'commit', '-qm', 'base')
    return tmp_path


def detect(repo, base, head, event='pull_request'):
    output = repo / 'output'
    output.unlink(missing_ok=True)
    result = subprocess.run(['bash', '-e', '-o', 'pipefail', '-c', DETECT], cwd=repo,
                            env={**os.environ, 'BASE_SHA': base, 'HEAD_SHA': head,
                                 'GITHUB_EVENT_NAME': event, 'GITHUB_OUTPUT': str(output)},
                            capture_output=True, text=True)
    return result, output.read_text() if output.exists() else ''


@pytest.mark.parametrize('path,relevant', [
    ('README.md', False), ('docs/guide.md', False), ('skills-lookalike.md', False),
    ('dist/demo/SKILL.md', True), ('plugins/demo/plugin.json', True),
    ('skills/demo/body.md', True), ('_snippets/shared.md', True),
    ('evals/corpus.jsonl', True), ('standalone-skills.yaml', True),
    ('pyproject.toml', True), ('.github/workflows/trigger-evals.yml', True),
    ('scripts/check_plugin_parity.py', True), ('tests/test_trigger_eval_gate.py', True),
    ('evals/odd\nname.jsonl', True),
])
def test_relevance(repo, path, relevant):
    base = git(repo, 'rev-parse', 'HEAD')
    file = repo / path
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text('changed\n')
    git(repo, 'add', '.')
    git(repo, 'commit', '-qm', 'change')
    result, output = detect(repo, base, git(repo, 'rev-parse', 'HEAD'))
    assert result.returncode == 0, result.stderr
    assert output == f'relevant={str(relevant).lower()}\n'


@pytest.mark.parametrize('operation', ['delete', 'rename'])
def test_removing_a_relevant_path_still_requires_evals(repo, operation):
    base = git(repo, 'rev-parse', 'HEAD')
    if operation == 'delete':
        git(repo, 'rm', 'evals/corpus.jsonl')
    else:
        git(repo, 'mv', 'evals/corpus.jsonl', 'moved.txt')
    git(repo, 'commit', '-qm', operation)
    result, output = detect(repo, base, git(repo, 'rev-parse', 'HEAD'))
    assert result.returncode == 0, result.stderr
    assert output == 'relevant=true\n'


def test_diff_ignores_base_only_changes(repo):
    git(repo, 'branch', 'pr')
    (repo / 'evals/corpus.jsonl').write_text('base advanced\n')
    git(repo, 'commit', '-qam', 'main changed evals')
    base = git(repo, 'rev-parse', 'HEAD')
    git(repo, 'checkout', '-q', 'pr')
    (repo / 'README.md').write_text('docs only\n')
    git(repo, 'commit', '-qam', 'PR changed docs')
    result, output = detect(repo, base, git(repo, 'rev-parse', 'HEAD'))
    assert result.returncode == 0, result.stderr
    assert output == 'relevant=false\n'


def test_invalid_diff_does_not_report_unrelated(repo):
    result, output = detect(repo, 'missing-ref', 'HEAD')
    assert result.returncode != 0
    assert output == ''


@pytest.mark.parametrize('event', ['push', 'schedule'])
def test_non_pr_runs_always_require_evals(repo, event):
    result, output = detect(repo, '', '', event)
    assert result.returncode == 0, result.stderr
    assert output == 'relevant=true\n'
