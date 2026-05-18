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
    4. Write dist/ + provenance   — for each skill, write
                                    dist/<name>/SKILL.md (frontmatter +
                                    rendered body) AND prepend the
                                    provenance header in one shot, then
                                    mirror the finished file into the
                                    plugin directory declared by the
                                    manifest. Provenance must be in
                                    place BEFORE the plugin mirror is
                                    written, otherwise the two outputs
                                    diverge.
    5. Emit standalones           — load standalone-skills.yaml and, for
                                    each entry, render the snippet body
                                    with its default params, wrap it in
                                    the standalone frontmatter, and run
                                    phase 4's writer.

Plugin manifests are NOT rewritten by the build. Each plugin's skills
are auto-discovered by the Claude Code plugin loader from the
`plugins/<plugin>/skills/` subdirectory — there is no JSON list to keep
in sync. The repo-root `.claude-plugin/marketplace.json` and each
plugin's `.claude-plugin/plugin.json` are hand-authored metadata only.

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
# The include marker uses double braces: `{{include:NAME}}`. That form is
# reserved for scaffold-level include expansion, while ordinary Jinja2
# `{{ PARAM }}` placeholders are evaluated only inside snippet bodies.
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


def emit_rendered_skill(skill_record: dict, rendered_body: str,
                        sources: list[str]) -> Path:
    """Phase 4: write `dist/<name>/SKILL.md` (with provenance), then mirror.

    Output layout:

        dist/<name>/SKILL.md                          (canonical artifact)
        plugins/<plugin>/skills/<name>/SKILL.md       (consumed by Claude)

    Critical ordering: the provenance header MUST be prepended to the
    dist/ file BEFORE the plugin copy is written, otherwise the two
    outputs diverge (the plugin copy would be missing the header) and
    the CI staleness check would never converge. This function takes
    `sources` and calls `write_provenance_header` itself; callers must
    not try to bolt provenance on after the fact.

    The plugin copy may also be a symlink to the dist copy — decision
    intentionally deferred. Whatever the build picks, the rule is
    "dist/ is the only source of truth; the plugin tree mirrors it."

    TODO:
        - Use python-frontmatter to round-trip the frontmatter block.
        - Write atomically (write-to-tempfile + rename) so a partial
          failure doesn't leave dist/ in a corrupt state.
        - Implementation outline:
              dist_path = write dist/<name>/SKILL.md with frontmatter
                          + rendered_body
              write_provenance_header(dist_path, sources)
              copy or symlink dist_path -> plugins/<plugin>/skills/<name>/SKILL.md
    """
    raise NotImplementedError("phase 4: emit_rendered_skill")


def emit_standalone_skills(snippet_index: dict[str, str]) -> list[dict]:
    """Phase 5: render standalone skills declared in standalone-skills.yaml.

    For each entry in the manifest:
        - Look up `snippet` in `snippet_index`.
        - Render with Jinja2 using `params` (defaults from the manifest).
        - Wrap with the entry's `frontmatter` (the emitted skill name
          comes from `frontmatter.name`, not from the top-level manifest
          key — the key is just a human-friendly identifier).
        - Emit through `emit_rendered_skill` so dist/ + plugin mirror +
          provenance ordering all stay consistent with phase 4.

    Skip entries that are commented out in the YAML (the scaffold's
    example entry is commented out for exactly this reason — once
    phase 5 is implemented, uncommenting it ships a real standalone).

    Returns
    -------
    The same shape as `load_skill_manifests` for any standalones that
    were actually emitted, so phase 6 can include them when rewriting
    plugin manifests.

    TODO:
        - Fail loudly if `snippet` doesn't exist in snippet_index.
    """
    raise NotImplementedError("phase 5: emit_standalone_skills")


def write_provenance_header(target: Path, sources: list[str]) -> None:
    """Prepend a generated-by header to a rendered SKILL.md.

    Called from inside phase 4 (`emit_rendered_skill`); not a top-level
    phase of its own. Kept as a separate function so phase 5
    (standalones) can reuse it.

    The header is an HTML comment block listing:
        - "DO NOT EDIT — generated by build.py"
        - source manifest path (skills/<name>/skill.yaml or
          standalone-skills.yaml)
        - every snippet name + source file that was inlined

    Inserted AFTER the frontmatter block so the Skill loader still
    parses frontmatter correctly.

    Provenance metadata constraint — IMPORTANT:
        The header must contain ONLY values derived from source files
        (paths, snippet names, content hashes). It must NOT contain the
        current git SHA, build timestamp, builder identity, or any
        other field that changes between builds of identical sources.

        Why: dist/ is committed. If the header included the current
        SHA, every commit would produce a new SHA, then the next
        build would re-stamp the header with that new SHA, then
        committing those changes would produce yet another SHA — the
        staleness check in CI would never converge. Build output must
        be a pure function of source content.

    TODO:
        - Decide on the source-list representation (just paths is
          probably enough; per-snippet content hashes are nice if a
          reviewer wants to diff inlined output against a snippet
          revision).
    """
    raise NotImplementedError("write_provenance_header")


def main() -> int:
    """End-to-end build entry point.

    Wires the five phases together. Returns 0 on success, non-zero on
    any failure so CI can rely on the exit code. Each phase currently
    raises NotImplementedError; once they're implemented, replace the
    short-circuit below with the real call sequence.
    """
    # TODO: replace this skeleton driver with the full pipeline.
    #
    # NOTE on ordering: emit_rendered_skill takes `sources` and writes
    # the provenance header BEFORE mirroring to the plugin tree. Do not
    # try to add provenance after the fact — the plugin copy would miss
    # it and CI's staleness check would fail.
    #
    # manifests = load_skill_manifests()
    # snippets = build_snippet_index()
    # emitted: list[dict] = []
    # for skill in manifests:
    #     body = (skill["body_path"]).read_text()
    #     rendered = render_skill_body(body, snippets, skill["manifest"]["includes"])
    #     copy_shared_scripts(skill)
    #     sources = [str(skill["source_dir"] / "skill.yaml")] + [
    #         f"_snippets/<file>:{inc['name']}" for inc in skill["manifest"]["includes"]
    #     ]
    #     emit_rendered_skill(skill, rendered, sources=sources)
    #     emitted.append(skill)
    # emitted += emit_standalone_skills(snippets)
    #
    # Note: plugin manifests are not touched by the build. Skills are
    # auto-discovered by Claude Code from each plugin's `skills/` subdir.

    print("build.py: skeleton — no phases implemented yet", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
