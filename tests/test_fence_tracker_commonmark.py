"""Differential test: build.py's fence tracker against a real CommonMark parser.

WHY THIS EXISTS
---------------
`build.py`'s Checkpoint control decides whether a line is inside a fenced
code block using `_classify_block_lines`, a line-at-a-time tracker rather
than a block parser. Every bypass this control has ever had was a
disagreement between that tracker and what CommonMark actually says, and
hand-written fixtures only cover the disagreements someone already thought
of. `tests/test_checkpoint_validator.py` pins the shapes we know about; this
file goes the other way and checks the PROPERTY the tracker is built to have,
over a shape matrix, a seeded fuzz, and every committed body:

    every line CommonMark treats as fenced-code content is a line the
    tracker treats as code (or as the fence delimiter itself)

That direction is the one with teeth. Where CommonMark says "code" and the
tracker says "prose", a destructive command is never scanned and a
`> **Checkpoint:**` shown as a fenced EXAMPLE starts counting as a real gate
— both silent. The opposite direction (tracker says code, CommonMark says
prose) is allowed on purpose: it over-scans, which fails the build loudly.
Asserting one direction and not the other is therefore deliberate, and the
`test_over_scan_*` cases below record where the slack actually is so that a
future change to the tracker has to look at it.

markdown-it-py is the adjudicator (`commonmark` preset, CommonMark 0.30, the
same spec dialect the Claude skill loader and GitHub both render). It is a
dev-only dependency; the suite skips if it is missing.
"""

from __future__ import annotations

import importlib.util
import itertools
import random
import sys
from pathlib import Path

import pytest

markdown_it = pytest.importorskip(
    "markdown_it", reason="markdown-it-py is needed to adjudicate CommonMark"
)

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_build_module():
    """Import the repo-root build.py (not an installed package) by path."""
    spec = importlib.util.spec_from_file_location("_build_fence_tracker", REPO_ROOT / "build.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


build = _load_build_module()
MD = markdown_it.MarkdownIt("commonmark")


def commonmark_fence_content(text: str) -> set[int]:
    """1-based line numbers CommonMark places INSIDE a fenced code block.

    The opening and closing delimiter lines are excluded: they carry no
    command and no gate, so the tracker is free to classify them either way.
    A fence token's `map` starts at its opening line and its `content` is the
    block's body, which is enough to name the body's lines without assuming
    the block was ever closed.
    """
    lines: set[int] = set()
    for token in MD.parse(text):
        if token.map is None or token.type != "fence":
            continue
        opener = token.map[0] + 1  # 0-based -> 1-based
        body = len(token.content.split("\n")) - 1 if token.content else 0
        lines |= set(range(opener + 1, opener + 1 + body))
    return lines


def tracker_verdicts(text: str) -> dict[str, set[int]]:
    """Group the tracker's per-line verdicts by kind, as 1-based line numbers."""
    grouped: dict[str, set[int]] = {
        build._LINE_CODE: set(),
        build._LINE_PROSE: set(),
        build._LINE_DELIMITER: set(),
    }
    for lineno, _raw, _content, verdict in build._classify_block_lines(text.split("\n")):
        grouped[verdict].add(lineno)
    return grouped


def under_scanned(text: str) -> list[int]:
    """Lines CommonMark calls code that the tracker hands to the prose path."""
    verdicts = tracker_verdicts(text)
    scanned = verdicts[build._LINE_CODE] | verdicts[build._LINE_DELIMITER]
    return sorted(commonmark_fence_content(text) - scanned)


def over_scanned(text: str) -> list[int]:
    """Lines the tracker calls code that CommonMark puts in no code block."""
    indented: set[int] = set()
    for token in MD.parse(text):
        if token.map is not None and token.type == "code_block":
            indented |= set(range(token.map[0] + 1, token.map[1] + 1))
    return sorted(tracker_verdicts(text)[build._LINE_CODE] - commonmark_fence_content(text) - indented)


def annotate(text: str, lines: list[int]) -> str:
    """Render a document with the offending lines marked, for the assertion."""
    out = [f"{'>>' if n in lines else '  '} {n:>3} {line!r}" for n, line in enumerate(text.split("\n"), 1)]
    return "\n".join(out)


def assert_no_under_scan(text: str, label: str) -> None:
    missed = under_scanned(text)
    assert not missed, (
        f"{label}: CommonMark puts line(s) {missed} inside a fenced code block, "
        f"but the tracker hands them to the prose path — a command there would "
        f"never be scanned and a fenced example gate there would count as a "
        f"real gate.\n{annotate(text, missed)}"
    )


# ---------------------------------------------------------------------------
# A generated matrix of fence shapes. Each document opens a fence, puts a
# destructive command in it, closes it with an independently varied
# delimiter, and follows with more command-shaped prose — so a tracker that
# desyncs on either delimiter shows up as an under-scan somewhere.
# ---------------------------------------------------------------------------

_DELIMITERS = ["```", "~~~", "````"]
_INDENTS = ["", "  ", "   ", "    ", "\t", "      "]
_QUOTES = ["", "> ", "  > ", "> > "]
_INFO_STRINGS = ["", "bash", "markdown"]


def _shape_matrix():
    for delim, indent, quote, info in itertools.product(
        _DELIMITERS, _INDENTS, _QUOTES, _INFO_STRINGS
    ):
        for closer, close_indent, close_quote in itertools.product(_DELIMITERS, _INDENTS, _QUOTES):
            label = (
                f"open={quote + indent + delim + info!r} "
                f"close={close_quote + close_indent + closer!r}"
            )
            yield label, (
                "Intro paragraph.\n\n"
                f"{quote}{indent}{delim}{info}\n"
                f"{quote}{indent}terraform apply\n"
                f"{close_quote}{close_indent}{closer}\n"
                "terraform apply\n"
                "trailing paragraph\n"
            )


def test_generated_shape_matrix_is_never_under_scanned():
    """~15.5k opener/closer combinations, adjudicated one by one."""
    count = 0
    for label, text in _shape_matrix():
        assert_no_under_scan(text, label)
        count += 1
    assert count > 15000, f"the matrix collapsed to {count} cases"


# ---------------------------------------------------------------------------
# A seeded fuzz over a vocabulary of block-structure lines. The seeds are
# fixed so a failure is reproducible; the vocabulary is what matters, and it
# is deliberately stuffed with the shapes that broke the tracker in review:
# list-contained and quoted fences, mismatched indentation, tabs, and
# delimiters that dedent out of their container.
# ---------------------------------------------------------------------------

_FUZZ_VOCABULARY = [
    # Delimiters, at every indentation and quote depth that matters.
    "```", "~~~", "````", "~~~~", "```bash", "```markdown", "~~~bash", "```ba`sh",
    "  ```", "   ```", "    ```", "     ```", "      ```", "\t```", "\t\t```", "  \t```",
    "  ~~~", "    ~~~", "    ````", "   ~~~~", "```~~~", "~~~```",
    "> ```", "> ~~~", ">> ```", "> > ```", "  > ```", "    > ```", "> ```bash",
    ">> ~~~", "   > ~~~", "  >> ```", ">   ```", ">\t```", ">>> ~~~", "     > > ```",
    # Container openers and quote/blank lines.
    "- item", "- Run:", "  - inner", "    - deep", "     - deeper", "1. step",
    "* bullet", "10. wide marker", "  1) alt", "\t- tab list",
    "> - quoted list", "> 1. quoted ol", "> ", ">", "  > text",
    "", "  ", "\t", "---", "## Heading", "# comment",
    # Payloads: what the control is actually looking for.
    "terraform apply", "  terraform apply", "   terraform apply", "    terraform apply",
    "\tterraform apply", "     terraform apply", "helm install foo coreweave/foo",
    "aws s3api create-bucket --bucket x", "> **Checkpoint:** confirm",
    "**Checkpoint:** drifted", "___Checkpoint:___", "***Checkpoint***",
    "paragraph text", "    indented code",
]


@pytest.mark.parametrize("seed", [1, 7, 42, 999, 20260825])
def test_fuzzed_documents_are_never_under_scanned(seed):
    rng = random.Random(seed)
    for case in range(8000):
        text = "\n".join(rng.choice(_FUZZ_VOCABULARY) for _ in range(rng.randint(2, 14))) + "\n"
        assert_no_under_scan(text, f"seed {seed} case {case}")


# ---------------------------------------------------------------------------
# The committed bodies. These have to agree with CommonMark in BOTH
# directions: an under-scan is a live bypass, and an over-scan would mean the
# build is scanning prose and could fail on a sentence that merely reads like
# a command.
# ---------------------------------------------------------------------------


def _committed_bodies():
    paths = sorted(build.DIST_DIR.glob("*/SKILL.md"))
    assert paths, "no dist/<name>/SKILL.md found — run `python build.py` first"
    return paths


@pytest.mark.parametrize("path", _committed_bodies(), ids=lambda p: p.parent.name)
def test_committed_bodies_agree_with_commonmark_exactly(path):
    text = path.read_text(encoding="utf-8")
    assert_no_under_scan(text, str(path))
    extra = over_scanned(text)
    assert not extra, (
        f"{path}: the tracker scans line(s) {extra} as code that CommonMark "
        f"renders as prose. Not a bypass, but it means the build is matching "
        f"commands against prose in a shipped body.\n{annotate(text, extra)}"
    )


# ---------------------------------------------------------------------------
# Where the slack is. These record the shapes the tracker deliberately
# over-scans, so that "it errs toward scanning" is a measured claim and not a
# hope. If a change makes one of these exact, delete the case; if a change
# makes it UNDER-scan instead, the tests above fail first.
# ---------------------------------------------------------------------------

OVER_SCAN_CASES = {
    # A closer dedented relative to its opener: a legal closer if the opener
    # sat indented inside its list item, a container break-out otherwise.
    # The tracker cannot tell, so it keeps scanning to end of file.
    "dedented-closer-inside-a-list": "- Note:\n     ```bash\n     echo x\n  ```\nprose here\n",
    # Same reason, reached through a tab whose width depends on the container.
    "tab-indented-line-under-a-space-indented-fence": "- Note:\n  ```bash\n\techo x\n  ```\nprose\n",
    # An unterminated fence runs to end of file, and `str.split` leaves a
    # final empty element that CommonMark never sees as a line.
    "phantom-final-line-after-an-unclosed-fence": "```bash\nterraform apply\n",
}


@pytest.mark.parametrize("case", sorted(OVER_SCAN_CASES))
def test_over_scan_cases_stay_one_directional(case):
    """Over-scanning is allowed; under-scanning in the same document is not."""
    text = OVER_SCAN_CASES[case]
    assert_no_under_scan(text, case)
    assert over_scanned(text), f"{case} no longer over-scans — delete this case"
