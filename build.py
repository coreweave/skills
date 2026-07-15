#!/usr/bin/env python3
"""Build the CoreWeave Claude skills library.

Renders the hand-edited skill sources into the generated `dist/` artifacts
and their plugin mirrors. `dist/` is committed; this script is the only
thing that should ever write to it (see the CI invariant below).

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
                                    from _shared-scripts/, copy them into
                                    dist/<name>/scripts/. A skill's own
                                    references/ directory is also copied
                                    verbatim into dist/<name>/references/.
    4. Write dist/ + provenance   — for each skill, write
                                    dist/<name>/SKILL.md (frontmatter +
                                    rendered body) AND insert the
                                    provenance header in one shot, then
                                    mirror the finished tree into the
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

Two deferred decisions, now settled (documented for the next maintainer):

  - Plugin mirror + shared scripts are COPIED, not symlinked. Copies are
    portable for downstream consumers (a plugin tree can be vendored on
    its own), and git stores the small text twice without complaint. The
    rule stays "dist/ is the only source of truth; the plugin tree
    mirrors it" — nothing hand-edits the plugin copy.
  - The emitted frontmatter is the manifest's `frontmatter:` block in
    source order, MINUS the source-only keys in
    SOURCE_ONLY_FRONTMATTER_KEYS (currently just `allowed-tools`). The
    Skill loader would *enforce* `allowed-tools`, but skills like the
    browser-driven cw-add-users need environment-provided tools that
    can't be enumerated statically, so the shipped skills are
    intentionally unrestricted (matching the hand-authored dist that
    predated this build). `allowed-tools` stays in skill.yaml as a record
    of intent; it just isn't propagated. (Decision: scampbell, 2026-06-16.)

Plugin manifests are NOT rewritten by the build. Each plugin's skills
are auto-discovered by the Claude Code plugin loader from the
`plugins/<plugin>/skills/` subdirectory — there is no JSON list to keep
in sync. The repo-root `.claude-plugin/marketplace.json` and each
plugin's `.claude-plugin/plugin.json` are hand-authored metadata only.

CI invariant: after a fresh build, `git diff --exit-code dist/` must
be clean. If it isn't, the PR's source files and committed output have
drifted — the contributor forgot to rebuild. The build output is a pure
function of source content (the provenance header carries no SHA or
timestamp), so re-running on unchanged sources is a no-op.

Run locally with:

    python build.py

(or `coreweave-skills-build` once the entry point in pyproject.toml is
wired up.)
"""

from __future__ import annotations

import os
import re
import shutil
import sys
from pathlib import Path

import yaml
from jinja2 import Environment, StrictUndefined

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

# Frontmatter keys that are authoring/source metadata only and are NOT
# written into the generated SKILL.md. See the module docstring for why
# `allowed-tools` is here. Everything else in `frontmatter:` is emitted
# verbatim in source order.
SOURCE_ONLY_FRONTMATTER_KEYS = ("allowed-tools",)

# name -> repo-relative source file, populated by build_snippet_index().
# Kept module-level so build_snippet_index() can honor its documented
# `-> dict[str, str]` signature while phase 4/5 still cite the file a
# snippet came from in the provenance header.
SNIPPET_SOURCES: dict[str, str] = {}


class BuildError(Exception):
    """A build failure with a message already aimed at the contributor."""


def _rel(path: Path) -> str:
    """Repo-relative POSIX path for human-facing messages and provenance."""
    try:
        return path.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def _jinja_env() -> Environment:
    """Jinja2 environment for snippet bodies.

    StrictUndefined turns a missing required param into a build error
    instead of a silently-empty substitution. Trailing newlines are kept
    so a snippet's own spacing survives inlining.
    """
    return Environment(
        undefined=StrictUndefined,
        keep_trailing_newline=True,
        autoescape=False,
    )


def _write_atomic(target: Path, text: str) -> None:
    """Write `text` to `target` via a temp file + rename (no partial files)."""
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, target)


def _reset_dist_dir(name: str) -> Path:
    """Remove and recreate dist/<name>/ so stale outputs can't linger."""
    dist_dir = DIST_DIR / name
    if dist_dir.exists():
        shutil.rmtree(dist_dir)
    dist_dir.mkdir(parents=True)
    return dist_dir


def load_skill_manifests() -> list[dict]:
    """Phase 1: parse every `skills/*/skill.yaml` into structured records.

    Returns
    -------
    A list of dicts, one per skill (sorted by name for determinism), each
    containing:
        - name:       frontmatter.name
        - plugin:     the owning marketplace plugin
        - source_dir: Path to skills/<name>/
        - manifest:   parsed skill.yaml (frontmatter, plugin, includes, ...)
        - body_path:  Path to skills/<name>/body.md
    """
    if not SKILLS_DIR.is_dir():
        raise BuildError(f"no skills directory at {_rel(SKILLS_DIR)}")

    records: list[dict] = []
    for source_dir in sorted(SKILLS_DIR.iterdir()):
        # Skip files and template/scaffold dirs (those start with "_").
        if not source_dir.is_dir() or source_dir.name.startswith("_"):
            continue

        manifest_path = source_dir / "skill.yaml"
        body_path = source_dir / "body.md"
        if not manifest_path.is_file():
            raise BuildError(f"{_rel(source_dir)}: missing skill.yaml")
        if not body_path.is_file():
            raise BuildError(f"{_rel(source_dir)}: missing body.md")

        manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(manifest, dict):
            raise BuildError(f"{_rel(manifest_path)}: not a YAML mapping")

        frontmatter = manifest.get("frontmatter")
        if not isinstance(frontmatter, dict):
            raise BuildError(f"{_rel(manifest_path)}: missing `frontmatter:` mapping")
        for key in ("name", "description"):
            if not frontmatter.get(key):
                raise BuildError(f"{_rel(manifest_path)}: frontmatter.{key} is required")
        plugin = manifest.get("plugin")
        if not plugin:
            raise BuildError(f"{_rel(manifest_path)}: `plugin:` is required")

        name = frontmatter["name"]
        if name != source_dir.name:
            raise BuildError(
                f"{_rel(manifest_path)}: frontmatter.name '{name}' must match "
                f"the directory name '{source_dir.name}'"
            )

        manifest.setdefault("includes", [])
        records.append(
            {
                "name": name,
                "plugin": plugin,
                "source_dir": source_dir,
                "manifest": manifest,
                "body_path": body_path,
            }
        )

    if not records:
        raise BuildError(f"no buildable skills found under {_rel(SKILLS_DIR)}")
    return records


def build_snippet_index() -> dict[str, str]:
    """Phase 2a: scan `_snippets/*.md` and index every tagged region.

    Returns a dict mapping snippet name -> raw body string (the contents
    between `<!-- snippet:NAME -->` and `<!-- /snippet:NAME -->`,
    exclusive). Also populates SNIPPET_SOURCES (name -> source file) for
    provenance.

    Snippet names are unique repo-wide; the filename is only for human
    organization. Mismatched / overlapping open and close markers are a
    build error.
    """
    SNIPPET_SOURCES.clear()
    index: dict[str, str] = {}
    if not SNIPPETS_DIR.is_dir():
        return index

    open_re = re.compile(SNIPPET_OPEN_RE)
    close_re = re.compile(SNIPPET_CLOSE_RE)

    for path in sorted(SNIPPETS_DIR.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        # Walk markers in order so we can reject nesting / mismatches.
        open_iter = list(open_re.finditer(text))
        for m in open_iter:
            name = m.group(1)
            close = close_re.search(text, m.end())
            if not close:
                raise BuildError(
                    f"{_rel(path)}: snippet '{name}' has no closing "
                    f"<!-- /snippet:{name} --> marker"
                )
            if close.group(1) != name:
                raise BuildError(
                    f"{_rel(path)}: snippet '{name}' is closed by "
                    f"'/snippet:{close.group(1)}' (mismatched markers)"
                )
            if name in index:
                raise BuildError(
                    f"duplicate snippet '{name}' "
                    f"(in {SNIPPET_SOURCES[name]} and {_rel(path)})"
                )
            body = text[m.end():close.start()]
            # Trim a single leading/trailing newline so the marker lines
            # don't bleed blank lines into the inlined output.
            index[name] = body.strip("\n")
            SNIPPET_SOURCES[name] = _rel(path)

    return index


def render_skill_body(body_md: str, snippet_index: dict[str, str],
                      includes: list[dict]) -> str:
    """Phase 2b: substitute `{{include:NAME}}` markers in a skill body.

    For each include declared in skill.yaml: look up the snippet, render
    it through Jinja2 with `params` as the context, and replace the
    matching `{{include:NAME}}` marker in body_md.

    The skill body itself is never run through Jinja2 — only snippet
    bodies are — so literal `{{ }}` / `${ }` in prose or Terraform
    examples pass through untouched. A `{{include:NAME}}` marker present
    in body.md but not declared in `includes` is a build error (the
    manifest stays authoritative).
    """
    declared = [inc["name"] for inc in includes]
    present = set(re.findall(INCLUDE_MARKER_RE, body_md))
    undeclared = present - set(declared)
    if undeclared:
        raise BuildError(
            "body.md references undeclared include(s): "
            + ", ".join(sorted(undeclared))
            + " — add them to the manifest's `includes:` list"
        )

    env = _jinja_env()
    rendered = body_md
    for inc in includes:
        name = inc["name"]
        params = inc.get("params") or {}
        if name not in snippet_index:
            raise BuildError(
                f"include '{name}' has no matching snippet in {_rel(SNIPPETS_DIR)}"
            )
        try:
            snippet_text = env.from_string(snippet_index[name]).render(**params)
        except Exception as exc:  # noqa: BLE001 — re-raise as a clean build error
            raise BuildError(f"rendering snippet '{name}': {exc}") from exc
        rendered = rendered.replace("{{include:" + name + "}}", snippet_text)

    return rendered


def copy_shared_scripts(skill_record: dict) -> None:
    """Phase 3: copy `_shared-scripts/` entries into dist/<name>/scripts/.

    A skill declares its dependencies with an optional `shared_scripts:`
    list in skill.yaml (each entry a path relative to _shared-scripts/).
    Entries are COPIED (see the module docstring for the copy-vs-symlink
    decision). Paths are resolved under _shared-scripts/ only — a manifest
    can't reach out of the repo.
    """
    shared = skill_record["manifest"].get("shared_scripts") or []
    if not shared:
        return

    base = SHARED_SCRIPTS_DIR.resolve()
    dst_dir = DIST_DIR / skill_record["name"] / "scripts"
    for entry in shared:
        src = (SHARED_SCRIPTS_DIR / entry).resolve()
        if base != src and base not in src.parents:
            raise BuildError(
                f"{skill_record['name']}: shared_script '{entry}' escapes "
                f"{_rel(SHARED_SCRIPTS_DIR)}"
            )
        if not src.exists():
            raise BuildError(
                f"{skill_record['name']}: shared_script '{entry}' not found "
                f"under {_rel(SHARED_SCRIPTS_DIR)}"
            )
        dst = dst_dir / Path(entry).name
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.is_dir():
            shutil.copytree(src, dst)
        else:
            shutil.copy2(src, dst)


def copy_skill_references(skill_record: dict) -> None:
    """Copy a skill's own `references/` directory into dist/<name>/.

    Reference material (`skills/<name>/references/*.md`) ships next to the
    rendered SKILL.md so the skill can `Read references/<file>` at runtime.
    Copied verbatim; not a numbered phase, but part of assembling dist/.
    """
    src = skill_record["source_dir"] / "references"
    if not src.is_dir():
        return
    dst = DIST_DIR / skill_record["name"] / "references"
    shutil.copytree(src, dst)


def emit_rendered_skill(skill_record: dict, rendered_body: str,
                        sources: list[str]) -> Path:
    """Phase 4: write `dist/<name>/SKILL.md` (with provenance), then mirror.

    Output layout:

        dist/<name>/SKILL.md                          (canonical artifact)
        plugins/<plugin>/skills/<name>/SKILL.md       (consumed by Claude)

    The frontmatter block is the manifest's `frontmatter:` mapping (source
    key order preserved, unicode kept) minus SOURCE_ONLY_FRONTMATTER_KEYS.
    The provenance header is inserted BEFORE the plugin mirror is written,
    so the two trees never diverge. dist/<name>/ is assumed to already
    hold any scripts/ and references/ (copied by the phase-3 helpers) —
    the whole directory is mirrored.
    """
    name = skill_record["name"]
    plugin = skill_record["plugin"]
    frontmatter = {
        k: v
        for k, v in skill_record["manifest"]["frontmatter"].items()
        if k not in SOURCE_ONLY_FRONTMATTER_KEYS
    }

    dist_dir = DIST_DIR / name
    dist_dir.mkdir(parents=True, exist_ok=True)

    fm_yaml = yaml.dump(
        frontmatter,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
        width=80,
    )
    dist_path = dist_dir / "SKILL.md"
    _write_atomic(dist_path, f"---\n{fm_yaml}---\n{rendered_body}")

    # Provenance goes in before the mirror so dist/ and the plugin copy
    # are byte-identical.
    write_provenance_header(dist_path, sources)

    # Mirror the finished dist/<name>/ tree into the plugin (a copy, not a
    # symlink). Rebuild the target so a removed reference can't linger.
    plugin_dir = PLUGINS_DIR / plugin / "skills" / name
    if plugin_dir.exists():
        shutil.rmtree(plugin_dir)
    plugin_dir.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(dist_dir, plugin_dir)

    return dist_path


def emit_standalone_skills(
    snippet_index: dict[str, str], only: set[str] | None = None
) -> list[dict]:
    """Phase 5: render standalone skills declared in standalone-skills.yaml.

    `only` (a set of skill names) restricts the build to those standalones; an
    entry whose `frontmatter.name` isn't in `only` is skipped entirely (not
    re-emitted). `None` builds every standalone (the full-build default).

    For each (uncommented) entry: render its `snippet` with the entry's
    `params`, wrap it in the entry's `frontmatter`, and emit through
    phase 4 so dist/ + plugin mirror + provenance stay consistent. The
    emitted skill name comes from `frontmatter.name`, not the top-level
    YAML key (which is a human-friendly identifier only).

    Returns the same record shape as `load_skill_manifests` for any
    standalones actually emitted.
    """
    if not STANDALONE_MANIFEST.is_file():
        return []
    data = yaml.safe_load(STANDALONE_MANIFEST.read_text(encoding="utf-8"))
    if not data:  # entirely comments / empty -> nothing to emit
        return []
    if not isinstance(data, dict):
        raise BuildError(f"{_rel(STANDALONE_MANIFEST)}: not a YAML mapping")

    env = _jinja_env()
    emitted: list[dict] = []
    for key, entry in data.items():
        if entry is None:
            continue  # a key with no body (e.g. left as a placeholder)
        if not isinstance(entry, dict):
            raise BuildError(f"standalone '{key}': entry must be a mapping")

        snippet = entry.get("snippet")
        plugin = entry.get("plugin")
        frontmatter = entry.get("frontmatter")
        params = entry.get("params") or {}
        if not snippet:
            raise BuildError(f"standalone '{key}': `snippet:` is required")
        if not plugin:
            raise BuildError(f"standalone '{key}': `plugin:` is required")
        if not isinstance(frontmatter, dict) or not frontmatter.get("name"):
            raise BuildError(f"standalone '{key}': frontmatter.name is required")
        if snippet not in snippet_index:
            raise BuildError(
                f"standalone '{key}': snippet '{snippet}' not found in "
                f"{_rel(SNIPPETS_DIR)}"
            )

        try:
            body = env.from_string(snippet_index[snippet]).render(**params)
        except Exception as exc:  # noqa: BLE001
            raise BuildError(f"standalone '{key}': rendering '{snippet}': {exc}") from exc
        if not body.endswith("\n"):
            body += "\n"

        name = frontmatter["name"]
        if only is not None and name not in only:
            continue  # single-skill build that didn't ask for this standalone
        record = {
            "name": name,
            "plugin": plugin,
            "source_dir": None,
            "manifest": {"frontmatter": frontmatter, "plugin": plugin},
            "body_path": None,
            "is_standalone": True,
        }
        sources = [
            _rel(STANDALONE_MANIFEST),
            f"{SNIPPET_SOURCES.get(snippet, '_snippets')}:{snippet}",
        ]
        _reset_dist_dir(name)
        emit_rendered_skill(record, body, sources)
        emitted.append(record)

    return emitted


def write_provenance_header(target: Path, sources: list[str]) -> None:
    """Insert a generated-by header after the frontmatter of a SKILL.md.

    Called from inside phase 4 (and reused by phase 5). The header is an
    HTML comment placed AFTER the closing `---` so the Skill loader still
    parses frontmatter, listing the source files that produced the file.

    The header contains ONLY values derived from source files (paths and
    snippet names). It must never carry the git SHA, a build timestamp, or
    builder identity: dist/ is committed, so a per-build value would make
    CI's staleness check ping-pong forever. Build output is a pure
    function of source content.
    """
    text = target.read_text(encoding="utf-8")
    lines = text.split("\n")
    if not lines or lines[0] != "---":
        raise BuildError(f"{_rel(target)}: expected YAML frontmatter fence")
    try:
        close = lines.index("---", 1)
    except ValueError as exc:
        raise BuildError(f"{_rel(target)}: unterminated frontmatter") from exc

    source_lines = "\n".join(f"     - {s}" for s in sources)
    provenance = (
        "<!-- DO NOT EDIT. Generated by build.py from the sources below;\n"
        "     edit the source(s) and re-run `python build.py` to regenerate.\n"
        "     sources:\n"
        f"{source_lines}\n"
        "-->\n"
    )

    head = "\n".join(lines[: close + 1])
    tail = "\n".join(lines[close + 1:])
    _write_atomic(target, f"{head}\n{provenance}{tail}")


def main() -> int:
    """End-to-end build entry point.

    Wires the five phases together. Returns 0 on success, non-zero on any
    failure so CI can rely on the exit code.
    """
    # Optional positional args = skill names to build (default: all). Lets the
    # eval harness rebuild just the skill under test — `python3 build.py cw-create-cluster`
    # — instead of the whole library. Flag-like args are ignored (no options today).
    only = {a for a in sys.argv[1:] if not a.startswith("-")} or None
    try:
        manifests = load_skill_manifests()
        snippets = build_snippet_index()

        emitted: list[dict] = []
        for skill in manifests:
            if only is not None and skill["name"] not in only:
                continue
            _reset_dist_dir(skill["name"])
            copy_shared_scripts(skill)
            copy_skill_references(skill)

            body = skill["body_path"].read_text(encoding="utf-8")
            rendered = render_skill_body(body, snippets, skill["manifest"]["includes"])

            sources = [_rel(skill["source_dir"] / "skill.yaml")]
            for inc in skill["manifest"]["includes"]:
                origin = SNIPPET_SOURCES.get(inc["name"], "_snippets")
                sources.append(f"{origin}:{inc['name']}")

            emit_rendered_skill(skill, rendered, sources)
            emitted.append(skill)

        emitted += emit_standalone_skills(snippets, only=only)

        if only is not None:
            missing = only - {s["name"] for s in emitted}
            if missing:
                print(f"build.py: no skill matches {sorted(missing)}", file=sys.stderr)
                return 1

        names = ", ".join(sorted(s["name"] for s in emitted))
        scope = f" of {sorted(only)}" if only is not None else ""
        print(f"build.py: emitted {len(emitted)} skill(s){scope}: {names}")
        return 0
    except BuildError as exc:
        print(f"build.py: error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
