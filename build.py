#!/usr/bin/env python3
"""Build the CoreWeave + W&B Claude skills library.

This is a SKELETON. None of the phases are implemented — each is a
no-op shell with TODOs. Implementing the build is a separate work item.

Pipeline overview
-----------------

The build is a deterministic function of the repo contents:

    skills/<name>/{skill.yaml,body.md}  ─┐
    _snippets/*.md                       ├─► dist/<name>/SKILL.md
    standalone-skills.yaml               │   plugins/<plugin>/skills/<name>/SKILL.md
    _shared-scripts/                     ┘

Phases (run in order):

    1. Load skill manifests       — parse every skills/*/skill.yaml.
    2. Resolve includes           — scan _snippets/*.md, build a
                                    name -> body index, render each
                                    skill's body.md by substituting
                                    `{{include:NAME}}` with the snippet
                                    body (Jinja2 evaluates `{{ PARAM }}`
                                    placeholders inside it using the
                                    `params` dict from skill.yaml).
    3. Copy shared scripts        — for each skill that requested entries
                                    from _shared-scripts/, copy or symlink
                                    them into dist/<name>/scripts/.
    4. Emit rendered skills       — write dist/<name>/SKILL.md (frontmatter
                                    + rendered body) and copy into the
                                    plugin directory declared by the
                                    manifest.
    5. Emit standalones           — load standalone-skills.yaml and, for
                                    each entry, render the snippet body
                                    with its default params and wrap it
                                    in the standalone frontmatter.
    6. Write provenance           — every emitted SKILL.md gets a header
                                    comment naming the source manifest +
                                    snippets + git SHA so reviewers can
                                    trace any line back to its origin.

CI invariant: after a fresh build, `git diff --exit-code dist/` must
be clean. If it isn't, the PR's source files and committed output have
drifted — the contributor forgot to rebuild.

Run locally with:

    python build.py

(or `coreweave-skills-build` once the entry point in pyproject.toml is
wired up.)
"""

from __future__ import annotations

import sys
from pathlib import Path

# Planned third-party deps (declared in pyproject.toml). Imported lazily
# inside the phase functions so the skeleton runs even if they aren't
# installed yet.
#
#   import frontmatter
#   import yaml
#   from jinja2 import Environment, StrictUndefined

REPO_ROOT = Path(__file__).resolve().parent
SKILLS_DIR = REPO_ROOT / "skills"
SNIPPETS_DIR = REPO_ROOT / "_snippets"
SHARED_SCRIPTS_DIR = REPO_ROOT / "_shared-scripts"
PLUGINS_DIR = REPO_ROOT / "plugins"
DIST_DIR = REPO_ROOT / "dist"
STANDALONE_MANIFEST = REPO_ROOT / "standalone-skills.yaml"

# Convention picked for the scaffold — a maintainer can change either.
# The include marker is single-brace so it doesn't collide with Jinja2's
# double-brace param syntax used INSIDE snippet bodies.
INCLUDE_MARKER_RE = r"\{\{include:([a-z0-9][a-z0-9-]*)\}\}"
SNIPPET_OPEN_RE = r"<!--\s*snippet:([a-z0-9][a-z0-9-]*)\s*-->"
SNIPPET_CLOSE_RE = r"<!--\s*/snippet:([a-z0-9][a-z0-9-]*)\s*-->"


def load_skill_manifests() -> list[dict]:
    """Phase 1: parse every `skills/*/skill.yaml` into structured records.

    Returns
    -------
    A list of dicts, one per skill, each containing at least:
        - source_dir: Path to skills/<name>/
        - manifest:   parsed skill.yaml (frontmatter, plugin, includes, ...)
        - body_path:  Path to skills/<name>/body.md

    TODO:
        - Use PyYAML safe_load to parse skill.yaml.
        - Skip directories whose name starts with `_` (template dirs).
        - Validate required keys (frontmatter.name, frontmatter.description,
          plugin); raise a clean error pointing at the offending file.
    """
    raise NotImplementedError("phase 1: load_skill_manifests")


def build_snippet_index() -> dict[str, str]:
    """Phase 2a: scan `_snippets/*.md` and index every tagged region.

    Returns
    -------
    A dict mapping snippet name -> raw body string (the contents between
    `<!-- snippet:NAME -->` and `<!-- /snippet:NAME -->`, exclusive).

    TODO:
        - Walk _snippets/*.md, regex-scan for open/close markers.
        - Reject duplicates: a snippet name is unique repo-wide. The
          filename is for human organization; the build doesn't care.
        - Reject mismatched open/close pairs with a clear error.
    """
    raise NotImplementedError("phase 2a: build_snippet_index")


def render_skill_body(body_md: str, snippet_index: dict[str, str],
                      includes: list[dict]) -> str:
    """Phase 2b: substitute `{{include:NAME}}` markers in a skill body.

    For each include declared in skill.yaml:
        1. Look up snippet_index[name] to get the raw snippet body.
        2. Render it through Jinja2 with `params` as the context (so any
           `{{ PARAM }}` placeholder inside the snippet gets resolved).
        3. Replace the corresponding `{{include:NAME}}` marker in body_md
           with the rendered snippet.

    TODO:
        - Use jinja2.Environment(undefined=StrictUndefined) so a missing
          required param fails the build instead of silently emitting
          an empty string.
        - Reject `{{include:NAME}}` markers in body.md that aren't also
          listed in skill.yaml `includes` — keeps the manifest authoritative.
    """
    raise NotImplementedError("phase 2b: render_skill_body")


def copy_shared_scripts(skill_record: dict) -> None:
    """Phase 3: copy/symlink `_shared-scripts/` entries into the skill.

    Whether the build copies or symlinks is an open question (symlinks
    are smaller in git and update atomically; copies are more portable
    for downstream consumers). Pick one and document it in the README.

    TODO:
        - Define how a skill declares its shared-script dependencies
          (most likely an optional `shared_scripts:` list in skill.yaml).
        - Resolve paths relative to _shared-scripts/ only — never let a
          manifest reach out of the repo.
    """
    raise NotImplementedError("phase 3: copy_shared_scripts")


def emit_rendered_skill(skill_record: dict, rendered_body: str) -> Path:
    """Phase 4: write `dist/<name>/SKILL.md` and copy into the plugin.

    Output layout:

        dist/<name>/SKILL.md                          (canonical artifact)
        plugins/<plugin>/skills/<name>/SKILL.md       (consumed by Claude)

    The plugin copy may also be a symlink to the dist copy — decision
    intentionally deferred. Whatever the build picks, the rule is
    "dist/ is the only source of truth; the plugin tree mirrors it."

    TODO:
        - Use python-frontmatter to round-trip the frontmatter block.
        - Write atomically (write-to-tempfile + rename) so a partial
          failure doesn't leave dist/ in a corrupt state.
    """
    raise NotImplementedError("phase 4: emit_rendered_skill")


def emit_standalone_skills(snippet_index: dict[str, str]) -> None:
    """Phase 5: render standalone skills declared in standalone-skills.yaml.

    For each entry in the manifest:
        - Look up `snippet` in `snippet_index`.
        - Render with Jinja2 using `params` (defaults from the manifest).
        - Wrap with the entry's `frontmatter`.
        - Emit to `dist/<frontmatter.name>/SKILL.md` and copy into the
          plugin tree, exactly like Phase 4 does for workflow skills.

    TODO:
        - Reuse `emit_rendered_skill` instead of duplicating its writer.
        - Fail loudly if `snippet` doesn't exist in snippet_index.
    """
    raise NotImplementedError("phase 5: emit_standalone_skills")


def write_provenance_header(target: Path, sources: list[str]) -> None:
    """Phase 6: prepend a generated-by header to a rendered SKILL.md.

    The header is an HTML comment block listing:
        - "DO NOT EDIT — generated by build.py"
        - source manifest path (skills/<name>/skill.yaml)
        - every snippet name + source file that was inlined
        - the current git SHA (so a reviewer can `git checkout <sha>`
          and reproduce the artifact exactly)

    Run AFTER frontmatter is written, so the header lives below the
    frontmatter block (otherwise the Skill loader gets confused).

    TODO:
        - Capture git SHA from subprocess.run(["git", "rev-parse", "HEAD"]).
        - Decide what to do in a dirty working tree (probably append
          "-dirty" to the SHA, like `git describe --dirty`).
    """
    raise NotImplementedError("phase 6: write_provenance_header")


def main() -> int:
    """End-to-end build entry point.

    Wires the six phases together. Returns 0 on success, non-zero on
    any failure so CI can rely on the exit code. Each phase currently
    raises NotImplementedError; once they're implemented, replace the
    short-circuit below with the real call sequence.
    """
    # TODO: replace this skeleton driver with the full pipeline.
    #
    # manifests = load_skill_manifests()
    # snippets = build_snippet_index()
    # for skill in manifests:
    #     body = (skill["body_path"]).read_text()
    #     rendered = render_skill_body(body, snippets, skill["manifest"]["includes"])
    #     copy_shared_scripts(skill)
    #     out = emit_rendered_skill(skill, rendered)
    #     write_provenance_header(out, sources=[...])
    # emit_standalone_skills(snippets)

    print("build.py: skeleton — no phases implemented yet", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
