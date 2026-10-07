"""Run the target guard against two same-name clusters, without network or tokens."""
import json
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
GUARD = ROOT / '_shared-scripts/check-kubeconfig-target.sh'


@pytest.fixture
def target(tmp_path):
    stub = tmp_path / 'kubectl'
    stub.write_text('''#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
if os.environ.get('KUBECTL_FAIL'):
    sys.exit(1)
assert args[0] == '--kubeconfig'
data = json.load(open(args[1]))
if args[2:] == ['config', 'current-context']:
    print(data['current-context'])
    sys.exit(0)
assert args[2] == '--context'
assert args[4:7] == ['config', 'view', '--minify']
assert args[7] == '-o' and '--raw' not in args
ctx = next((c for c in data['contexts'] if c['name'] == args[3]), None)
if ctx is None:
    sys.exit(1)
if args[8] == 'jsonpath={.contexts[0].name}':
    print(ctx['name'])
else:
    assert args[8] == 'jsonpath={.clusters[0].cluster.server}'
    cluster = next(c for c in data['clusters'] if c['name'] == ctx['context']['cluster'])
    print(cluster['cluster']['server'])
''')
    stub.chmod(0o755)
    cfg = tmp_path / 'target.json'
    data = {'current-context': 'research',
            'contexts': [{'name': 'research', 'context': {'cluster': 'west'}}],
            'clusters': [{'name': 'west', 'cluster': {'server': 'https://west.example'}}],
            'users': [{'name': 'token', 'user': {'token': 'never-print-this'}}]}
    cfg.write_text(json.dumps(data))
    return cfg, data, {**os.environ, 'PATH': str(tmp_path) + os.pathsep + os.environ['PATH']}


def run(target, context='research', server='https://west.example', current=False):
    cfg, _, env = target
    marker = cfg.parent / 'acted'
    script = 'set -euo pipefail\nbash "$1" "$2" "$3" "$4"\nprintf acted > "$5"'
    args = [str(GUARD), str(cfg), context, server, str(marker)]
    if current:
        script = script.replace('bash "$1"', 'bash "$1" --current')
    proc = subprocess.run(['bash', '-c', script, 'guard-test', *args],
                          text=True, capture_output=True, env=env)
    assert 'never-print-this' not in proc.stdout + proc.stderr
    return proc, marker.exists()


@pytest.mark.parametrize('server', ['https://west.example', 'west.example', 'https://west.example/'])
def test_correct_endpoint_allows_legacy_alias(target, server):
    proc, acted = run(target, server=server, current=True)
    assert proc.returncode == 0, proc.stderr
    assert acted


def test_same_context_name_wrong_zone_cannot_act(target):
    proc, acted = run(target, server='https://east.example', current=True)
    assert proc.returncode != 0
    assert not acted


def test_downloaded_alias_can_differ_from_display_name(target):
    cfg, data, _ = target
    data['contexts'][0]['name'] = 'qa/research/west'
    data['current-context'] = 'other'
    cfg.write_text(json.dumps(data))
    proc, acted = run(target, context='qa/research/west')
    assert proc.returncode == 0, proc.stderr
    assert acted
    proc, _ = run(target, context='qa/research/west', current=True)
    assert proc.returncode != 0  # Terraform reads current-context, not --context.


@pytest.mark.parametrize('context,server', [('missing', 'https://west.example'),
    ('research', ''), ('research', 'http://west.example'),
    ('research', 'https://user:secret@west.example'), ('research', 'https://')])
def test_missing_or_invalid_target_blocks_action(target, context, server):
    proc, acted = run(target, context=context, server=server)
    assert proc.returncode != 0
    assert not acted


def test_unreadable_metadata_blocks_action(target):
    target[2]['KUBECTL_FAIL'] = '1'
    proc, acted = run(target)
    assert proc.returncode != 0
    assert not acted


def test_missing_file_blocks_ambient_fallback(target):
    target[0].unlink()
    proc, acted = run(target)
    assert proc.returncode != 0
    assert not acted


@pytest.mark.parametrize('name', ['cw-create-cluster', 'cw-create-node-pool',
    'cw-self-managed-inference', 'cw-get-kubeconfig', 'cw-verify-workload-health'])
def test_built_consumers_ship_guard(name):
    assert (ROOT / 'dist' / name / 'scripts' / GUARD.name).read_bytes() == GUARD.read_bytes()
