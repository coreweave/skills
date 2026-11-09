"""Regression tests for run_router_evals.py's credential-free preflight.

These cover the checks the `preflight` CI job is the only guard for. Every
one of them runs without an API key, which is the whole point of that job.
"""
import importlib.util
import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
CORPUS = REPO_ROOT / "evals/trigger-evals.jsonl"


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "run_router_evals", REPO_ROOT / "evals/run_router_evals.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


runner = _load_module()

DIST_NAMES = {"cw-create-cluster", "cw-other"}
CANDIDATES = {"cw-create-cluster", "cw-other"}
BASE = {"query": "create a cluster", "expected_skill": "cw-create-cluster"}


def write(tmp_path, rows):
    path = tmp_path / "corpus.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return path


def load(tmp_path, rows):
    return runner.load_evals(write(tmp_path, rows), DIST_NAMES, CANDIDATES)


# --- near-duplicate queries ------------------------------------------------
#
# The reviewed hazard was a duplicate query being routed twice and
# double-weighted in accuracy. Exact duplicates were rejected; anything that
# differed by a space or a capital was not, so the same double-weighting was
# one keystroke away.

@pytest.mark.parametrize("variant", [
    "create a cluster ",
    " create a cluster",
    "Create A Cluster",
    "create  a   cluster",
    "\tcreate a cluster\n",
])
def test_near_duplicate_queries_are_rejected(tmp_path, variant):
    with pytest.raises(runner.ConfigError) as exc:
        load(tmp_path, [BASE, {"query": variant, "expected_skill": "cw-create-cluster",
                               "id": "second"}])
    assert "same" in str(exc.value)


def test_a_real_phrasing_variant_is_still_allowed(tmp_path):
    entries = load(tmp_path, [
        BASE,
        {"query": "spin up a cluster for me", "expected_skill": "cw-create-cluster"},
    ])
    assert len(entries) == 2


def test_normalization_does_not_alter_the_query_sent_to_the_model(tmp_path):
    entries = load(tmp_path, [{"query": "  Mixed  Case Query  ",
                               "expected_skill": None}])
    assert entries[0]["query"] == "  Mixed  Case Query  "


def test_the_committed_corpus_still_loads():
    """The normalization must not reject anything already in the corpus.

    The expected count is read from the corpus file, not written here as a
    literal. A literal says nothing about normalization -- it only records how
    many rows happened to exist the day it was typed -- and it couples this
    test to the corpus size, so a branch that grows the corpus turns this red
    for pure bookkeeping and the fix depends on which branch merges second.
    Comparing against the file's own row count is the claim the docstring
    actually makes: every committed row survives loading. A row silently
    dropped by normalization still fails, which is the regression this guards.
    """
    dist = REPO_ROOT / "dist"
    candidates, dist_names, _unshipped = runner.load_candidates(dist, False)
    entries = runner.load_evals(CORPUS, dist_names, set(candidates))
    rows = [ln for ln in CORPUS.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert entries, "the committed corpus must not be empty"
    assert len(entries) == len(rows)


# --- explicit JSON null on the optional fields ----------------------------
#
# `required: null` used to be a hard exit-2 config error while `id: null`
# and `notes: null` were accepted, so a generator that emitted null for
# every unset optional field passed or failed depending on which field it
# left unset.

@pytest.mark.parametrize("field", ["id", "required", "notes"])
def test_explicit_null_means_absent(tmp_path, capsys, field):
    entries = load(tmp_path, [dict(BASE, **{field: None})])
    assert len(entries) == 1
    assert entries[0]["id"] is None
    assert entries[0]["required"] is False
    assert entries[0]["notes"] is None
    warned = capsys.readouterr().err
    assert f"'{field}': null" in warned, "an explicit null must be reported"


def test_null_required_cannot_veto_the_gate(tmp_path):
    entries = load(tmp_path, [dict(BASE, required=None)])
    assert entries[0]["required"] is False


# --- the type checks that must NOT be relaxed by the null rule ------------

@pytest.mark.parametrize("row,needle", [
    ({"required": "false"}, "must be the JSON literal true or false"),
    ({"required": 0}, "must be the JSON literal true or false"),
    ({"id": []}, "'id' must be a string or absent"),
    ({"id": "  "}, "'id' is blank"),
    ({"notes": 5}, "'notes' must be a string or absent"),
])
def test_wrong_typed_optional_values_are_still_config_errors(tmp_path, row, needle):
    with pytest.raises(runner.ConfigError) as exc:
        load(tmp_path, [dict(BASE, **row)])
    assert needle in str(exc.value)


# --- the results-key namespace stays RAW ----------------------------------

def test_results_key_collision_is_still_detected(tmp_path):
    with pytest.raises(runner.ConfigError) as exc:
        load(tmp_path, [dict(BASE, id="dup"),
                        {"query": "another query", "expected_skill": None, "id": "dup"}])
    assert "results key" in str(exc.value)


def test_cross_namespace_key_collision_is_still_detected(tmp_path):
    with pytest.raises(runner.ConfigError) as exc:
        load(tmp_path, [BASE,
                        {"query": "another query", "expected_skill": None,
                         "id": BASE["query"]}])
    assert "results key" in str(exc.value)
