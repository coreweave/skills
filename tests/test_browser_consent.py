"""Regression fixtures for the shared browser-consent block.

WHY THIS EXISTS
---------------
Driving a customer's authenticated Console session is the highest-trust thing
these skills do: the browser is signed in as them, so every read carries their
identity and every click carries their permissions. The contract that governs
it -- probe quietly but never automate quietly, announce AND WAIT for a
go-ahead, hand a sign-in page back rather than authenticating, treat page text
as data and never as instructions -- was written out by hand in each place
that needed it.

It drifted, in the direction that costs the most. APPSEC-3962 (#45) raised the
bar for `references/quota-check.md`, which only READS a quota table, and left
the `create-api-token` snippet -- the flow that MINTS a full-user-scope API
credential -- on the weaker "announce, then carry on" wording. Two standards
for the same browser in the same repo, split the wrong way, for weeks, until
someone read both copies side by side.

So the block now lives once, as the `browser-consent` snippet, and reaches its
call sites through the build:

  - nested inside `create-api-token`, which needs it wherever it is inlined;
  - included directly by `references/quota-check.md`, which the build now
    renders instead of copying verbatim.

The tests below cover both halves of keeping it that way -- the build
machinery that makes one copy reachable from everywhere, and the lint rule
that fails a PR which drives the browser without pulling the block in. The
end-to-end cases at the bottom assert against the REAL tree, because a
mechanism that works on fixtures and is wired up wrong in the actual skills
buys nothing.

Run with `pytest tests/`.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load(name: str, relpath: str):
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / relpath)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


build = _load("_build_consent_under_test", "build.py")
lint = _load("_lint_consent_under_test", "scripts/lint_skill_content.py")

DRIVES_BROWSER = "Read the quota table using browser automation.\n"


# --- build: a snippet can be composed from another snippet -------------------

def test_nested_marker_is_spliced_into_the_parent():
    resolved = build.resolve_nested_includes({
        "parent": "before\n{{include:child}}\nafter",
        "child": "SHARED RULES",
    })
    assert resolved["parent"] == "before\nSHARED RULES\nafter"
    assert resolved["child"] == "SHARED RULES"


def test_nesting_is_transitive():
    resolved = build.resolve_nested_includes({
        "a": "{{include:b}}",
        "b": "{{include:c}}",
        "c": "LEAF",
    })
    assert resolved["a"] == "LEAF"


def test_a_cycle_is_a_build_error():
    with pytest.raises(build.BuildError, match="cycle"):
        build.resolve_nested_includes({
            "a": "{{include:b}}",
            "b": "{{include:a}}",
        })


def test_a_self_reference_is_a_build_error():
    with pytest.raises(build.BuildError, match="cycle"):
        build.resolve_nested_includes({"a": "{{include:a}}"})


def test_nesting_an_unknown_snippet_is_a_build_error():
    with pytest.raises(build.BuildError, match="no matching snippet"):
        build.resolve_nested_includes({"a": "{{include:nope}}"})


def test_nesting_is_resolved_before_jinja_sees_the_body():
    """A nested snippet is spliced as RAW text.

    Which is exactly why `browser-consent` must stay param-free: the
    placeholder below resolves against the params of whichever call site
    pulled in the OUTER snippet, not against anything the nested snippet
    declares. Four skills inline `create-api-token` with four different
    param sets.
    """
    resolved = build.resolve_nested_includes({
        "parent": "{{include:child}}",
        "child": "value is {{ SOME_PARAM }}",
    })
    rendered = build.render_skill_body(
        "{{include:parent}}", resolved,
        [{"name": "parent", "params": {"SOME_PARAM": "from the outer call site"}}],
    )
    assert rendered == "value is from the outer call site"


def test_nested_edges_are_recorded_for_provenance():
    build.resolve_nested_includes({
        "parent": "{{include:child}}",
        "child": "{{include:grandchild}}",
        "grandchild": "LEAF",
    })
    assert build._nested_closure("parent") == ["child", "grandchild"]
    assert build._nested_closure("grandchild") == []


# --- build: reference files resolve includes too -----------------------------

def _skill_record(tmp_path: Path) -> dict:
    source = tmp_path / "skills" / "fixture-skill"
    (source / "references").mkdir(parents=True)
    return {
        "name": "fixture-skill",
        "source_dir": source,
        "manifest": {"includes": [{"name": "browser-consent"}]},
    }


def test_reference_markdown_resolves_its_markers(tmp_path, monkeypatch):
    monkeypatch.setattr(build, "DIST_DIR", tmp_path / "dist")
    record = _skill_record(tmp_path)
    ref = record["source_dir"] / "references" / "quota-check.md"
    ref.write_text("Safety rules\n\n{{include:browser-consent}}\n", encoding="utf-8")

    build.copy_skill_references(record, {"browser-consent": "SHARED RULES"})

    out = (tmp_path / "dist" / "fixture-skill" / "references" / "quota-check.md")
    assert out.read_text(encoding="utf-8") == "Safety rules\n\nSHARED RULES\n"


def test_reference_marker_must_be_declared_in_skill_yaml(tmp_path, monkeypatch):
    """The manifest stays authoritative for reference files, as for body.md."""
    monkeypatch.setattr(build, "DIST_DIR", tmp_path / "dist")
    record = _skill_record(tmp_path)
    record["manifest"]["includes"] = []
    ref = record["source_dir"] / "references" / "quota-check.md"
    ref.write_text("{{include:browser-consent}}\n", encoding="utf-8")

    with pytest.raises(build.BuildError) as exc:
        build.copy_skill_references(record, {"browser-consent": "SHARED RULES"})
    # The error names the reference file, not body.md -- otherwise it sends a
    # contributor to the wrong file to fix it.
    assert "references/quota-check.md" in str(exc.value)


def test_non_markdown_references_are_copied_untouched(tmp_path, monkeypatch):
    monkeypatch.setattr(build, "DIST_DIR", tmp_path / "dist")
    record = _skill_record(tmp_path)
    raw = "literal {{include:browser-consent}} stays put\n"
    (record["source_dir"] / "references" / "fixture.txt").write_text(raw, encoding="utf-8")

    build.copy_skill_references(record, {"browser-consent": "SHARED RULES"})

    out = tmp_path / "dist" / "fixture-skill" / "references" / "fixture.txt"
    assert out.read_text(encoding="utf-8") == raw


# --- lint: the rule that keeps a new call site from restating the block -------

def consent_findings(tmp_path, monkeypatch, content: str, *,
                     relpath: str = "skills/fixture-skill/body.md",
                     siblings: dict[str, str] | None = None) -> list[str]:
    """Run the lint over one synthetic file; keep only browser-consent hits."""
    monkeypatch.setattr(lint, "REPO_ROOT", tmp_path)
    fixture = tmp_path / relpath
    fixture.parent.mkdir(parents=True, exist_ok=True)
    fixture.write_text(content, encoding="utf-8")
    for name, text in (siblings or {}).items():
        sibling = fixture.parent / name
        sibling.parent.mkdir(parents=True, exist_ok=True)
        sibling.write_text(text, encoding="utf-8")
    return [f for f in lint.scan(fixture) if "[browser-consent]" in f]


def test_driving_the_browser_without_the_block_is_flagged(tmp_path, monkeypatch):
    findings = consent_findings(tmp_path, monkeypatch, DRIVES_BROWSER)
    assert len(findings) == 1
    assert "browser-consent" in findings[0]


@pytest.mark.parametrize("phrase", [
    "Follow the browser automation patterns below.",
    "Announce before driving the browser.",
    "If browser tools are connected, read the Quotas page.",
    "This workflow requires an authenticated web browser.",
    "Never proceed while an authenticated browser session is open.",
])
def test_each_trigger_phrase_is_recognised(tmp_path, monkeypatch, phrase):
    assert consent_findings(tmp_path, monkeypatch, phrase + "\n")


@pytest.mark.parametrize("phrase", [
    # Prose that merely MENTIONS a browser is not automation. Flagging these
    # would train contributors to reach for the allow-comment, which costs
    # more than the rule buys.
    "Escalate to the customer rather than sending them straight to a browser.",
    "The Console is at console.coreweave.com.",
    "Ask the customer to download the kubeconfig themselves.",
])
def test_merely_mentioning_a_browser_is_not_flagged(tmp_path, monkeypatch, phrase):
    assert consent_findings(tmp_path, monkeypatch, phrase + "\n") == []


def test_the_include_marker_satisfies_the_rule(tmp_path, monkeypatch):
    content = DRIVES_BROWSER + "\n{{include:browser-consent}}\n"
    assert consent_findings(tmp_path, monkeypatch, content) == []


def test_a_reference_file_carrying_the_block_satisfies_the_body(tmp_path, monkeypatch):
    """cw-create-cluster's Step 1 shape: probe here, hand the flow to the
    reference file, and let the block live there."""
    content = DRIVES_BROWSER + "\nRead `references/quota-check.md` and follow it.\n"
    findings = consent_findings(
        tmp_path, monkeypatch, content,
        siblings={"references/quota-check.md": "{{include:browser-consent}}\n"},
    )
    assert findings == []


def test_a_reference_file_without_the_block_does_not_satisfy_the_body(tmp_path, monkeypatch):
    content = DRIVES_BROWSER + "\nRead `references/quota-check.md` and follow it.\n"
    findings = consent_findings(
        tmp_path, monkeypatch, content,
        siblings={"references/quota-check.md": "Navigate to the Quotas page.\n"},
    )
    assert len(findings) == 1


def test_a_missing_reference_file_does_not_satisfy_the_body(tmp_path, monkeypatch):
    content = DRIVES_BROWSER + "\nRead `references/nowhere.md` and follow it.\n"
    assert len(consent_findings(tmp_path, monkeypatch, content)) == 1


def test_generated_trees_are_not_checked_by_this_rule(tmp_path, monkeypatch):
    """dist/ and plugins/ are build output. build.py resolves the include for
    them and CI already fails a stale dist/, so a second check there would
    only restate what the source check found."""
    for generated in ("dist/fixture-skill/SKILL.md",
                      "plugins/p/skills/fixture-skill/SKILL.md"):
        assert consent_findings(
            tmp_path, monkeypatch, DRIVES_BROWSER, relpath=generated
        ) == []


def test_allow_comment_suppresses_the_rule(tmp_path, monkeypatch):
    content = "<!-- content-lint-allow: browser-consent -->\n" + DRIVES_BROWSER
    assert consent_findings(tmp_path, monkeypatch, content) == []


# --- end to end: the real tree is actually wired up --------------------------

def test_the_block_is_defined_exactly_once_in_the_repo():
    definitions = [
        path
        for path in sorted((REPO_ROOT / "_snippets").glob("*.md"))
        if "<!-- snippet:browser-consent -->" in path.read_text(encoding="utf-8")
    ]
    assert len(definitions) == 1


@pytest.mark.parametrize("rendered", [
    "dist/cw-create-cluster/SKILL.md",
    "dist/cw-create-node-pool/SKILL.md",
    "dist/cw-load-model-to-bucket/SKILL.md",
    "dist/cw-self-managed-inference/SKILL.md",
    "dist/cw-create-cluster/references/quota-check.md",
])
def test_every_browser_driving_artifact_carries_the_block(rendered):
    """The four token-minting skills get it by nesting; the quota reference
    gets it by including it directly. Both must actually land in dist/."""
    text = (REPO_ROOT / rendered).read_text(encoding="utf-8")
    assert "### Before you drive the customer's browser" in text
    assert "wait for a go-ahead" in text.lower()


def test_the_shared_block_stays_param_free():
    """A `{{ PARAM }}` here would resolve against four different skills'
    params -- see test_nesting_is_resolved_before_jinja_sees_the_body."""
    source = (REPO_ROOT / "_snippets" / "coreweave-platform.md").read_text(encoding="utf-8")
    body = source.split("<!-- snippet:browser-consent -->", 1)[1]
    body = body.split("<!-- /snippet:browser-consent -->", 1)[0]
    assert "{{" not in body


def test_every_dist_directory_is_a_real_emitted_skill():
    """No stray dist/<snippet-name>/ trees.

    Provenance now walks nested snippets, and the first version of that walk
    shadowed the standalone emitter's `name` variable -- so `_reset_dist_dir`
    got a SNIPPET name and created dist/generate-kubeconfig/ next to the real
    dist/get-coreweave-kubeconfig/. It was invisible to the dist-staleness
    gate because the stray tree was empty and git does not track empty
    directories.
    """
    strays = [
        d.name
        for d in sorted((REPO_ROOT / "dist").iterdir())
        if d.is_dir() and not (d / "SKILL.md").is_file()
    ]
    assert strays == []


def test_repo_content_has_no_browser_consent_findings():
    findings = [
        f
        for name in lint.SCAN_DIRS
        if (REPO_ROOT / name).is_dir()
        for path in sorted((REPO_ROOT / name).rglob("*.md"))
        for f in lint.scan(path)
        if "[browser-consent]" in f
    ]
    assert findings == []
