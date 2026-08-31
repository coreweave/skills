"""Regression fixtures for the Checkpoint contract validator (APPSEC-3963).

WHY THIS EXISTS
---------------
`validate_rendered_bodies` in build.py is a security control: it fails the
build when a destructive command ships in a rendered skill body with no
`> **Checkpoint:**` human-confirmation gate in scope — the gate must sit in
the command's own markdown section or the one immediately before it. Its
whole value rests on the markdown block tracker agreeing with what a reader
(and an agent) actually sees as a runnable code block. Every case below is
either a bypass that shipped at some point in review — a fence shape the
tracker did not recognize, so the command inside it was never scanned — or
the mirror-image false positive that a naive fix would introduce.

Run with `pytest tests/`. The fixtures write synthetic SKILL.md files into a
tmp dir and point `build.DIST_DIR` at it; no real body is touched, and the
grandfather baseline is emptied so a fixture can't accidentally consume an
allowance.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_build_module():
    """Import the repo-root build.py (not an installed package) by path."""
    spec = importlib.util.spec_from_file_location("_build_under_test", REPO_ROOT / "build.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


build = _load_build_module()

GATE = "> **Checkpoint:** Show the customer the plan and get confirmation.\n"


def validate(tmp_path: Path, body: str) -> str | None:
    """Validate one synthetic body; return the error text, or None if clean."""
    skill = "fixture-skill"
    (tmp_path / skill).mkdir(parents=True, exist_ok=True)
    (tmp_path / skill / "SKILL.md").write_text(body, encoding="utf-8")

    saved_dist, saved_baseline = build.DIST_DIR, dict(build.CHECKPOINT_BASELINE)
    build.DIST_DIR = tmp_path
    build.CHECKPOINT_BASELINE.clear()
    try:
        build.validate_rendered_bodies([{"name": skill}], full_build=False)
        return None
    except build.BuildError as exc:
        return str(exc)
    finally:
        build.DIST_DIR = saved_dist
        build.CHECKPOINT_BASELINE.clear()
        build.CHECKPOINT_BASELINE.update(saved_baseline)


def doc(*blocks: str) -> str:
    """Wrap fixture blocks in the frontmatter every emitted SKILL.md carries."""
    return "---\nname: fixture-skill\ndescription: fixture\n---\n\n" + "\n".join(blocks)


# ---------------------------------------------------------------------------
# Fence shapes that must be SCANNED. Each one hid an ungated destructive
# command from the validator before the tracker learned about tilde
# delimiters and container-relative indentation.
# ---------------------------------------------------------------------------

UNGATED_FENCE_SHAPES = {
    "backtick-at-margin": "```bash\nterraform apply -auto-approve\n```\n",
    # Tilde fences are valid CommonMark and were invisible to a
    # backtick-only tracker.
    "tilde-at-margin": "~~~bash\nterraform apply -auto-approve\n~~~\n",
    "tilde-long-run": "~~~~~bash\nhelm install foo coreweave/foo\n~~~~~\n",
    # A fence inside a list item is indented past the three-space
    # document-level allowance. The repo already emits this shape (see
    # dist/verify-coreweave-workload-health/SKILL.md), so it was a live
    # bypass, not a hypothetical one.
    "list-contained-backtick-4sp": "- Run the deploy:\n    ```bash\n    terraform apply\n    ```\n",
    "list-contained-tilde-4sp": "- Run the deploy:\n    ~~~bash\n    terraform apply\n    ~~~\n",
    "nested-list-backtick-6sp": "- outer\n  - inner\n      ```bash\n      helm upgrade foo coreweave/foo\n      ```\n",
    # Same, inside a block-quoted aside indented into a list item.
    "list-contained-blockquote-fence": "- Note:\n    > ```bash\n    > aws s3api create-bucket --bucket x\n    > ```\n",
    "list-contained-blockquote-tilde": "- Note:\n     > ~~~\n     > terraform apply\n     > ~~~\n",
    # Tab indentation counts as indentation, not as "no leading spaces".
    "tab-indented": "- Run the deploy:\n\t```bash\n\tterraform apply\n\t```\n",
    "deep-indent-10sp": "          ```bash\n          helm install foo coreweave/foo\n          ```\n",
    # A closer longer than its opener still closes; the command before it
    # is still inside the block.
    "closed-by-longer-run": "```bash\nterraform apply\n````\n",
    # An unterminated fence runs to end of file — its contents are code.
    "unclosed-at-eof": "```bash\nterraform apply\n",
}


@pytest.mark.parametrize("shape", sorted(UNGATED_FENCE_SHAPES))
def test_ungated_command_in_any_fence_shape_fails(tmp_path, shape):
    err = validate(tmp_path, doc("Steps:\n\n", UNGATED_FENCE_SHAPES[shape]))
    assert err is not None, f"{shape}: ungated destructive command was not detected"
    assert "has no preceding" in err


@pytest.mark.parametrize("shape", sorted(UNGATED_FENCE_SHAPES))
def test_same_shape_passes_once_gated(tmp_path, shape):
    """The fix must not turn a correctly gated body into a build failure."""
    assert validate(tmp_path, doc(GATE, "\n", UNGATED_FENCE_SHAPES[shape])) is None


# ---------------------------------------------------------------------------
# Fence-tracker state: a delimiter line inside an open block is CONTENT, so
# it must not close the block and let later commands slip past the scan.
# ---------------------------------------------------------------------------

NESTED_DELIMITER_CASES = {
    # An info-stringed ``` inside an open backtick block is not a closer.
    "info-string-inside-backtick": "```markdown\n```bash\n```\n\n```bash\nterraform apply\n```\n",
    # A closer indented more than three spaces past its opener is content.
    "over-indented-closer": "```markdown\n    ```\n```\n\n```bash\nterraform apply\n```\n",
    # A backtick run cannot close a tilde fence, or vice versa.
    "backtick-inside-tilde": "~~~markdown\n```\n~~~\n\n```bash\nterraform apply\n```\n",
    "tilde-inside-backtick": "```markdown\n~~~\n```\n\n~~~bash\nterraform apply\n~~~\n",
    # A shorter run cannot close a longer opener.
    "short-run-inside-long": "````markdown\n```\n````\n\n```bash\nterraform apply\n```\n",
}


@pytest.mark.parametrize("case", sorted(NESTED_DELIMITER_CASES))
def test_nested_delimiter_does_not_hide_a_later_command(tmp_path, case):
    err = validate(tmp_path, doc(NESTED_DELIMITER_CASES[case]))
    assert err is not None, f"{case}: a nested delimiter desynced the fence tracker"
    assert "terraform apply" in err


# ---------------------------------------------------------------------------
# The OPPOSITE direction of the same state machine: a delimiter that MUST end
# its block. The cases above only prove the tracker does not close too eagerly
# and would all still pass if it never closed at all, which is its own bypass:
# a tracker stuck inside a block reads the NEXT opener as this block's closer,
# and the command in that next block then lands in prose, unscanned.
#
# Each fixture below is therefore built so only a correctly closed first block
# leaves `terraform apply` inside a fence — closer first, then a real fence
# holding the command.
# ---------------------------------------------------------------------------

MUST_CLOSE_DELIMITER_CASES = {
    # A bare run of the same length is the ordinary closer.
    "bare-run-at-margin": "```markdown\n```\n\n```\nterraform apply\n```\n",
    # A run LONGER than the opener still closes.
    "longer-run": "```markdown\n````\n\n```\nterraform apply\n```\n",
    # Trailing whitespace after the run is still "nothing but whitespace".
    "trailing-whitespace": "```markdown\n```   \n\n```\nterraform apply\n```\n",
    # Three spaces is the document-level indentation allowance, not content.
    "indented-3sp-at-margin": "```markdown\n   ```\n\n```\nterraform apply\n```\n",
    "tilde-delimiter": "~~~markdown\n~~~\n\n~~~\nterraform apply\n~~~\n",
    # Inside a list item, opener and closer at the same indentation.
    "list-contained": (
        "- Run:\n    ```markdown\n    ```\n\n    ```\n    terraform apply\n    ```\n"
    ),
    # Inside a blockquote, at the same quote depth.
    "quoted-same-depth": (
        "> ```markdown\n> ```\n>\n> ```\n> terraform apply\n> ```\n"
    ),
}


@pytest.mark.parametrize("case", sorted(MUST_CLOSE_DELIMITER_CASES))
def test_delimiter_that_must_close_does_not_swallow_a_later_command(tmp_path, case):
    err = validate(tmp_path, doc(MUST_CLOSE_DELIMITER_CASES[case]))
    assert err is not None, (
        f"{case}: the closer was read as content, so the next opener was "
        f"consumed as this block's closer and the command fell into prose"
    )
    assert "terraform apply" in err


def test_a_gate_after_a_closed_fence_is_still_seen(tmp_path):
    """A tracker stuck inside a block would miss the gate and fail the build."""
    body = doc(
        "```markdown\nan example block\n```\n",
        "\n",
        GATE,
        "\n```bash\nterraform apply\n```\n",
    )
    assert validate(tmp_path, body) is None


def test_a_near_miss_after_a_closed_fence_is_still_reported(tmp_path):
    """Same failure, hygiene side: a stuck tracker reports no marker at all."""
    body = doc(GATE, "\n```markdown\nan example block\n```\n", "\n**Checkpoint:** drifted\n")
    err = validate(tmp_path, body)
    assert err is not None and "near-miss Checkpoint marker" in err


# ---------------------------------------------------------------------------
# Container break-out. A delimiter that leaves its container does NOT simply
# close the fence: CommonMark ends the container (a fenced block takes no lazy
# continuation), which closes the fence, and then the same line opens a fresh
# block at the outer level — so the lines after it are still code.
#
# Reading it as a plain closer reverses the control in both directions at
# once, which is why both directions are pinned here: a command stops being
# scanned, AND a gate that is really inside a code block starts counting as a
# real gate for everything after it. Adjudicated against markdown-it-py in
# commonmark mode; see tests/test_fence_tracker_commonmark.py.
# ---------------------------------------------------------------------------

BREAKOUT_CASES = {
    "column0-bare-after-list-fence": (
        "- Run the deploy:\n    ```bash\n    echo staging\n```\n"
        "terraform apply -auto-approve\n```\n"
    ),
    "column0-info-stringed-after-list-fence": (
        "- Run the deploy:\n    ```bash\n    echo staging\n```bash\n"
        "terraform apply -auto-approve\n```\n"
    ),
    "column0-tilde-after-list-fence": (
        "- Run the deploy:\n    ```bash\n    echo staging\n~~~\n"
        "terraform apply -auto-approve\n~~~\n"
    ),
    "column0-after-nested-list-fence": (
        "- outer\n  - inner\n      ```bash\n      echo staging\n```\n"
        "terraform apply\n```\n"
    ),
    "unquoted-after-quoted-fence": (
        "> ```bash\n> echo staging\n```\nterraform apply\n```\n"
    ),
    "shallower-quote-after-nested-quoted-fence": (
        "> > ```bash\n> > echo staging\n> ```\n> terraform apply\n> ```\n"
    ),
}


@pytest.mark.parametrize("case", sorted(BREAKOUT_CASES))
def test_break_out_delimiter_does_not_hand_code_to_the_prose_path(tmp_path, case):
    err = validate(tmp_path, doc(BREAKOUT_CASES[case]))
    assert err is not None, (
        f"{case}: the break-out delimiter was read as a plain closer, so the "
        f"command CommonMark keeps inside a code block was never scanned"
    )
    assert "has no preceding" in err


def test_break_out_delimiter_does_not_manufacture_a_gate(tmp_path):
    """The mirror image: the `> **Checkpoint:**` here is inside a code block.

    CommonMark ends the list item at the column-0 fence and opens a new one,
    so the marker after it is code, not prose, and gates nothing. A tracker
    that reads the column-0 fence as a plain closer sees a real gate instead
    and waves through every command in the rest of the document.
    """
    body = doc(
        "- Show the example:\n    ```markdown\n    example only\n```\n",
        GATE,
        "```\n",
        "\n```bash\nterraform apply\n```\n",
    )
    err = validate(tmp_path, body)
    assert err is not None and "has no preceding" in err


# ---------------------------------------------------------------------------
# Block-quote prefix stripping. The prefix is stripped so quoted fences and
# quoted commands are tracked, but the `>` COUNT has to be respected: a `>`
# run DEEPER than the open fence's is literal code content, never a closer.
# Stripping it unconditionally let a `> ``` ` line close a plain ``` block.
# ---------------------------------------------------------------------------

DEEPER_QUOTE_INSIDE_FENCE_CASES = {
    "quoted-delimiter-in-unquoted-fence": "```markdown\n> ```\nterraform apply\n```\n",
    "indented-quoted-delimiter-in-unquoted-fence": (
        "```markdown\n    > ```\nterraform apply\n```\n"
    ),
    "doubly-quoted-delimiter-in-quoted-fence": (
        "> ```markdown\n> > ```\n> terraform apply\n> ```\n"
    ),
    "quoted-delimiter-in-list-contained-fence": (
        "- Note:\n    ```markdown\n    > ```\n    terraform apply\n    ```\n"
    ),
}


@pytest.mark.parametrize("case", sorted(DEEPER_QUOTE_INSIDE_FENCE_CASES))
def test_deeper_quote_marker_inside_a_fence_is_content(tmp_path, case):
    err = validate(tmp_path, doc(DEEPER_QUOTE_INSIDE_FENCE_CASES[case]))
    assert err is not None, (
        f"{case}: the quote marker was stripped and the remainder mistaken "
        f"for the closer, dropping the code after it out of the scan"
    )
    assert "has no preceding" in err


def test_an_ambiguous_dedented_closer_keeps_scanning(tmp_path):
    """A deliberate over-scan, pinned so it stays an over-scan.

    Whether `  ``` ` closes the fence above it depends on the list item's
    content indentation, which this tracker does not compute. It resolves the
    ambiguity by treating the rest of the document as code, so the bare
    `terraform apply` line below is scanned and the build fails loudly —
    rather than being waved through as prose.
    """
    body = doc("- Note:\n     ```bash\n     echo x\n  ```\n", "\nterraform apply\n")
    err = validate(tmp_path, body)
    assert err is not None and "has no preceding" in err


def test_command_inside_a_fence_is_not_read_as_a_near_miss(tmp_path):
    """A fenced example of a bad marker is documentation, not a drifted gate."""
    body = doc(GATE, "\nHow not to write it:\n\n", "```markdown\n__Checkpoint:__ nope\n```\n")
    assert validate(tmp_path, body) is None


def test_a_gate_shown_inside_a_fence_does_not_count_as_a_gate(tmp_path):
    """A marker quoted as an EXAMPLE gates nothing — it is code, not prose."""
    body = doc(
        "```markdown\n" + GATE + "```\n",
        "\n```bash\nterraform apply\n```\n",
    )
    err = validate(tmp_path, body)
    assert err is not None and "has no preceding" in err


def test_a_commented_out_command_is_not_an_invocation(tmp_path):
    assert validate(tmp_path, doc("```bash\n# terraform apply\n```\n")) is None


def test_a_backtick_info_string_holding_a_backtick_is_a_paragraph(tmp_path):
    """Not a fence per CommonMark, so its neighbours are prose, not code."""
    assert validate(tmp_path, doc("```bash`x\nterraform apply\n```\n")) is None


# ---------------------------------------------------------------------------
# Marker hygiene: every emphasis form markdown renders, not just `**...**`.
# ---------------------------------------------------------------------------

NEAR_MISS_MARKERS = [
    "**Checkpoint:** no blockquote",
    "__Checkpoint:__ no blockquote",
    "*Checkpoint:* no blockquote",
    "_Checkpoint:_ no blockquote",
    "**Checkpoint**: colon outside the bold",
    "__Checkpoint__: colon outside the bold",
    "**Checkpoint** with no colon at all",
    "__Checkpoint__ with no colon at all",
    "> **Checkpoint**: colon outside the bold",
    "> **checkpoint:** wrong case",
    "> __Checkpoint:__ wrong emphasis",
    "> *Checkpoint:* wrong emphasis",
    "> Checkpoint: no emphasis at all",
    # Bold-italic. `***...***` was already covered by the `**` branch, which
    # matches from the second asterisk; the underscore forms were not,
    # because `__` is followed by `_` rather than the word and the single-`_`
    # branch refuses an intra-word underscore.
    "***Checkpoint:*** bold-italic asterisks",
    "___Checkpoint:___ bold-italic underscores",
    "___Checkpoint___ bold-italic, no colon",
    "___Checkpoint___: colon outside the emphasis",
    "> ___Checkpoint:___ wrong emphasis",
    "> ***Checkpoint:*** wrong emphasis",
]


@pytest.mark.parametrize("marker", NEAR_MISS_MARKERS)
def test_near_miss_marker_fails_even_behind_a_real_gate(tmp_path, marker):
    """An inert look-alike must not hide behind an earlier valid gate."""
    err = validate(tmp_path, doc(GATE, "\n", marker + "\n"))
    assert err is not None, f"near-miss {marker!r} was not detected"
    assert "near-miss Checkpoint marker" in err


NOT_NEAR_MISSES = [
    # The canonical marker itself.
    "> **Checkpoint:** Show the customer the plan.",
    # Unemphasized prose mentioning the word mid-sentence.
    "We reached a checkpoint in the plan and moved on.",
    # A list bullet is not single-asterisk emphasis (CommonMark flanking:
    # the delimiter is followed by whitespace), even when it mentions the
    # word and the line closes with real emphasis elsewhere.
    "* Checkpoint discipline matters *a lot* here.",
    # Underscores inside an identifier are not emphasis either.
    "Set `model_checkpoint_dir` before the run.",
    # Still true with the wider underscore runs: CommonMark does not open
    # emphasis on an intra-word `_`.
    "Set `run___checkpoint___dir` before the run.",
]


@pytest.mark.parametrize("line", NOT_NEAR_MISSES)
def test_non_marker_lines_are_not_reported(tmp_path, line):
    assert validate(tmp_path, doc(GATE, "\n", line + "\n")) is None


# ---------------------------------------------------------------------------
# Gate SCOPE (CHECKPOINT_HEADING_ALLOWANCE).
#
# The scope started as "anywhere earlier in the document", which meant one
# Checkpoint near the top of a body satisfied every command below it — an
# ungated `terraform apply` in a troubleshooting section or an appendix
# hundreds of lines later passed the build. The allowance is now one heading:
# the gate reaches its own section and the next one.
#
# Both directions are pinned. Tightening to zero headings would reject the
# cw-self-managed-inference deploy gate, which legitimately spans a boundary,
# and demanding a gate per command would force a confirmation per line on a
# multi-block step — which trains the click-through habit the control exists
# to prevent.
# ---------------------------------------------------------------------------

COMMAND_BLOCK = "```bash\nterraform apply -auto-approve\n```\n"


def _doc_with_gate_n_headings_back(n: int) -> str:
    """A body whose only gate sits `n` headings before the command."""
    parts = ["# Title\n\n", GATE, "\n"]
    for i in range(n):
        parts.append(f"## Section {i}\n\nprose\n\n")
    parts.append(COMMAND_BLOCK)
    return "".join(parts)


@pytest.mark.parametrize("headings_back", [0, 1])
def test_a_gate_within_the_allowance_is_in_scope(tmp_path, headings_back):
    assert validate(tmp_path, _doc_with_gate_n_headings_back(headings_back)) is None


@pytest.mark.parametrize("headings_back", [2, 3, 7])
def test_a_gate_past_the_allowance_is_out_of_scope(tmp_path, headings_back):
    err = validate(tmp_path, _doc_with_gate_n_headings_back(headings_back))
    assert err is not None, f"a gate {headings_back} headings back must not gate the command"
    assert "out of scope" in err
    # The message has to name the gate it rejected, or the author cannot tell
    # "no gate anywhere" from "gate too far away" — different fixes.
    assert f"{headings_back} heading(s) back" in err


def test_a_second_gate_refreshes_the_scope(tmp_path):
    """Each gate resets the count; an early gate going stale is not fatal."""
    body = (
        "# Title\n\n" + GATE + "\n"
        "## Step one\n\nprose\n\n"
        "## Step two\n\nprose\n\n"  # the first gate is now out of scope
        + GATE
        + "\n## Step three\n\n"  # ...but this one is one heading back
        + COMMAND_BLOCK
    )
    assert validate(tmp_path, body) is None


def test_every_heading_level_bounds_the_scope(tmp_path):
    """H1-H6 all count: a deeper heading is still a section boundary."""
    for hashes in ("#", "##", "###", "####", "#####", "######"):
        body = (
            "# Title\n\n" + GATE + "\n"
            f"{hashes} One\n\nprose\n\n"
            f"{hashes} Two\n\nprose\n\n" + COMMAND_BLOCK
        )
        assert validate(tmp_path, body) is not None, f"{hashes} did not bound the scope"


def test_a_heading_inside_a_block_quote_does_not_bound_the_scope(tmp_path):
    """`> ## Foo` opens a section of the aside, not of the document."""
    body = (
        "# Title\n\n" + GATE + "\n"
        "> ## Quoted heading\n>\n> an aside\n\n"
        "> ### Another\n>\n> more aside\n\n" + COMMAND_BLOCK
    )
    assert validate(tmp_path, body) is None


def test_a_heading_inside_a_fence_does_not_bound_the_scope(tmp_path):
    """A `#` line inside a code block is a comment, not a section boundary."""
    body = (
        "# Title\n\n" + GATE + "\n"
        "```bash\n# Step 1\n## not a heading\n### nor this\n```\n\n"
        + COMMAND_BLOCK
    )
    assert validate(tmp_path, body) is None


def test_no_gate_at_all_still_reports_the_original_message(tmp_path):
    """The two failure modes stay distinguishable."""
    err = validate(tmp_path, "# Title\n\n## Step\n\n" + COMMAND_BLOCK)
    assert err is not None
    assert "has no preceding" in err
    assert "out of scope" not in err


# ---------------------------------------------------------------------------
# Enforced command classes.
#
# A class is only enabled once every current occurrence already passes, so
# these pin BOTH directions: the class fires when ungated, and the wrapper /
# global-flag shapes the bodies actually use still reach the matcher. The
# token allowance is the part that has bitten: `kubectl --kubeconfig X
# --context Y apply` is four tokens between binary and subcommand, which is
# why enabling kubectl apply needs a matcher change and not just body gates.
# ---------------------------------------------------------------------------

ENFORCED_COMMANDS = [
    "terraform apply -auto-approve",
    "terraform destroy -auto-approve -target='module.nodepool[\"pool\"]'",
    "terraform -chdir=infra apply",
    "terraform -chdir=infra destroy",
    "helm install cert-manager coreweave/cert-manager \\",
    "helm upgrade cert-manager coreweave/cert-manager \\",
    "helm -n kube-system install traefik coreweave/traefik",
    "cwrun aws s3api create-bucket --bucket b",
    "aws --profile cw s3api create-bucket --bucket b",
]


@pytest.mark.parametrize("command", ENFORCED_COMMANDS)
def test_enforced_command_fails_when_ungated(tmp_path, command):
    err = validate(tmp_path, doc("```bash\n", command + "\n", "```\n"))
    assert err is not None, f"{command!r} was not matched"


@pytest.mark.parametrize("command", ENFORCED_COMMANDS)
def test_enforced_command_passes_when_gated(tmp_path, command):
    body = doc(GATE, "\n", "```bash\n", command + "\n", "```\n")
    assert validate(tmp_path, body) is None


NOT_ENFORCED_COMMANDS = [
    # Deliberately out of scope today. Each is a documented follow-up, not an
    # oversight; a test here would otherwise silently start failing the day
    # someone enables the class, with no hint of which direction is wrong.
    "kubectl --kubeconfig \"$KCFG\" --context \"$CTX\" apply -f -",
    "kubectl delete nodepool pool",
    "rm -rf .terraform",
    # Shapes past the matcher's three-token allowance, documented in the
    # comment above DESTRUCTIVE_COMMAND_RE in build.py.
    "terraform -chdir=a -no-color -lock=false -input=false apply",
]


@pytest.mark.parametrize("command", NOT_ENFORCED_COMMANDS)
def test_unenforced_command_is_not_matched(tmp_path, command):
    """Pins the boundary, so widening the matcher is a deliberate edit here."""
    err = validate(tmp_path, doc("```bash\n", command + "\n", "```\n"))
    assert err is None, f"{command!r} is now matched — update this list"


# ---------------------------------------------------------------------------
# The real bodies: the control must hold with zero body edits.
# ---------------------------------------------------------------------------


def test_committed_bodies_pass_validation():
    """Every emitted dist/<name>/SKILL.md satisfies the contract as committed."""
    names = [{"name": p.parent.name} for p in sorted(build.DIST_DIR.glob("*/SKILL.md"))]
    assert names, "no dist/<name>/SKILL.md found — run `python build.py` first"
    build.validate_rendered_bodies(names, full_build=True)
