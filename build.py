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
                                    phase 4's writer. An entry with no
                                    `plugin:` is "include-only": it still
                                    gets a dist/<name>/ tree, but ships in
                                    no plugin (see below).
    6. Validate rendered bodies   — read back every dist/<name>/SKILL.md
                                    emitted above and enforce the
                                    `> **Checkpoint:**` human-confirmation
                                    contract on it: a destructive command
                                    in a fenced code block with no gate
                                    earlier in the document fails the
                                    build, as does a near-miss marker or a
                                    stale ratchet-baseline entry. Read-
                                    only — it never writes or rewrites
                                    output, so a failure means the emitted
                                    files are on disk but the exit code is
                                    non-zero and CI will not merge them.
                                    See validate_rendered_bodies() and
                                    SECURITY.md (APPSEC-3963).

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

Include-only skills (`plugin:` omitted in standalone-skills.yaml)
----------------------------------------------------------------

A snippet can be worth rendering as a whole skill without being worth
shipping to customers on its own — a browser-first procedure, say, that
only makes sense as a step inside a larger workflow. Omit `plugin:` on a
standalone-skills.yaml entry and the build writes dist/<name>/ as usual
but mirrors it into NO plugin, so the skill cannot be installed
standalone from the marketplace. It still reaches the eval harness,
which mounts dist/ directly. `plugin:` stays REQUIRED for source skills
under skills/ — every hand-authored skill ships somewhere.

Flipping an existing entry to include-only removes its plugin copy: the
build sweeps `plugins/*/skills/<name>/` for a mirror left by an earlier
build. Commit that deletion, and bump the affected plugin's version in
`plugins/<plugin>/.claude-plugin/plugin.json` — `claude plugin update`
silently no-ops without a version change, so installed copies would keep
serving the withdrawn skill.

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


def _unship_from_all_plugins(name: str) -> None:
    """Delete `plugins/*/skills/<name>/` from every plugin tree.

    The teardown half of an include-only skill. A skill with no owning
    plugin must not be installable, and the build only knows where a copy
    *would* go when `plugin:` is set — so when it isn't, sweep every
    plugin. Without this, flipping an entry to include-only would leave the
    previous build's mirror in place and customers would keep installing a
    skill the repo no longer ships.
    """
    if not PLUGINS_DIR.is_dir():
        return
    for plugin_dir in sorted(PLUGINS_DIR.iterdir()):
        stale = plugin_dir / "skills" / name
        if stale.is_dir():
            shutil.rmtree(stale)


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

    `skill_record["plugin"]` may be None for an include-only standalone
    (see the module docstring). Then only dist/ is written, and any plugin
    copy from an earlier build is swept away.
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

    if not plugin:
        # Include-only: dist/ only, shipped in no plugin.
        _unship_from_all_plugins(name)
        return dist_path

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

    An entry may omit `plugin:` to become include-only — dist/ output, no
    plugin mirror. See the module docstring.

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

        # In a filtered build, skip entries that weren't requested BEFORE any
        # validation/rendering: an unrelated standalone (missing snippet, bad
        # template, unnamed) shouldn't fail or slow a single-skill build. Full
        # builds (only is None) still validate every entry below.
        if only is not None:
            fm = entry.get("frontmatter")
            candidate = fm.get("name") if isinstance(fm, dict) else None
            if not isinstance(candidate, str) or candidate not in only:
                continue

        snippet = entry.get("snippet")
        plugin = entry.get("plugin")
        frontmatter = entry.get("frontmatter")
        params = entry.get("params") or {}
        if not snippet:
            raise BuildError(f"standalone '{key}': `snippet:` is required")
        # `plugin:` is OPTIONAL here. Omitting it makes the entry
        # include-only: still emitted to dist/ (the eval harness mounts that
        # directly), but mirrored into no plugin, so customers cannot install
        # it standalone.
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


# ---------------------------------------------------------------------------
# Structural Checkpoint enforcement (APPSEC-3963) — see SECURITY.md.
#
# Skills in this repo instruct an agent operating on live customer
# infrastructure. The `> **Checkpoint:**` blockquote is the contract marker
# for a human-confirmation gate: the customer approves before the agent runs
# the destructive command that follows (several bodies deliberately pair a
# Checkpoint with `-auto-approve` — the gate replaces the tool's own
# interactive prompt). This phase makes the gate's EXISTENCE structural: the
# build fails when a destructive command appears in a rendered body with no
# Checkpoint anywhere earlier in the document, and when a marker is close to
# — but not exactly — the canonical form, so the contract can't silently
# drift. Runtime enforcement (making the agent actually stop) is out of
# scope here; see SECURITY.md for the threat model and limits.
#
# Design decisions (justified at length in SECURITY.md):
#
#   - Validation runs on the emitted dist/<name>/SKILL.md artifacts, at the
#     end of the emit phases. Rationale: the reported file:line points at a
#     real committed file; workflow skills and standalones share one code
#     path; and the validator never touches emission internals. Plugin
#     mirrors are byte-for-byte copies of dist/ (phase 4), so validating
#     dist/ covers them. A validation failure exits non-zero, so CI never
#     merges output that failed, even though files were already written.
#
#   - Precedence scope is "anywhere earlier in the same document", not
#     "same markdown section". The cw-self-managed-inference deploy gate
#     legitimately spans a section boundary (its Checkpoint closes the
#     values-file step; the `helm install` opens the next step), so a
#     same-section rule would reject a correctly-gated body. Anywhere-
#     earlier is the strongest scope every currently-gated occurrence
#     satisfies without body edits.
#
#   - Only fenced code blocks are scanned for commands. Inline `code` in
#     prose is narrative, not a runnable block. Fence lines whose first
#     non-space character is `#` are comments, not invocations. Which
#     lines count as "fenced" is where this control lives or dies, so the
#     tracker handles BOTH delimiter characters (``` and ~~~), fences at
#     any indentation (list-contained fences sit past the document-level
#     three-space allowance — the repo already emits some), and fences
#     inside blockquotes at any depth. See _FENCE_LINE_RE, and
#     tests/test_checkpoint_validator.py for a fixture per bypass shape.
#
#   - `kubectl apply` and `terraform destroy` are NOT enforced yet: current
#     bodies contain occurrences of each with no earlier Checkpoint, and
#     adding gates is a body change outside this build-time control's
#     scope. Both are documented follow-ups in SECURITY.md.
#
#   - CHECKPOINT_BASELINE grandfathers the ungated `helm install` /
#     `helm upgrade` occurrences that predate this control (the cluster-
#     dependency installs in cw-self-managed-inference, which sit before
#     that document's only Checkpoint). The baseline is a ratchet: a NEW
#     ungated occurrence fails the build, and once a baselined occurrence
#     is gated or removed, the build fails until its entry is deleted —
#     the list can only shrink. This keeps helm commands enforced
#     everywhere else (notably the deploy-step install) instead of
#     dropping the whole command class.
# ---------------------------------------------------------------------------

# The contract marker, verbatim. Gate detection is EXACT (up to 3 leading
# spaces, per CommonMark's block-quote indentation allowance): anything
# checkpoint-shaped that doesn't match is a hygiene error, never a gate.
CHECKPOINT_MARKER = "> **Checkpoint:**"
CHECKPOINT_GATE_RE = re.compile(r"^ {0,3}" + re.escape(CHECKPOINT_MARKER))

# "Looks like an attempted Checkpoint marker": an emphasis-wrapped
# "checkpoint" run in ANY of markdown's four emphasis forms (`**`, `__`,
# `*`, `_`), any case, colon inside or outside the emphasis; or a blockquote
# that opens with the word "checkpoint", emphasized or bare. Covering only
# `**...**` here would let `__Checkpoint:__` and `*Checkpoint:*` ship as
# inert prose that reads like a gate. Prose that merely mentions the word
# checkpoint mid-sentence, unemphasized, does not match.
#
# One branch per emphasis form rather than one alternation, because the
# rules that keep each form from firing on ordinary prose differ:
#
#   - The doubled forms tolerate stray inner whitespace (`** Checkpoint:**`),
#     since a doubled delimiter can't be mistaken for a list bullet.
#   - The single forms enforce CommonMark's flanking rule (no whitespace
#     just inside the delimiters), which is what stops a `* Checkpoint: do
#     X *and* Y` LIST ITEM from being reported as a marker.
#   - The underscore forms additionally enforce CommonMark's ban on
#     intra-word `_` emphasis, so `model_checkpoint_dir` in prose is not a
#     hit while `__Checkpoint__` still is.
#
# Deliberately fail-loud: an emphasized `*checkpoint*` written as ordinary
# prose is indistinguishable from a marker that drifted, so write that word
# unemphasized (no current body does otherwise).
#
# `_CHECKPOINT_WORD` ends the word with an explicit character-class
# lookahead instead of `\b`, because `\b` does not fire between `t` and the
# `_` of a `__Checkpoint__` wrapper.
_CHECKPOINT_WORD = r"checkpoint(?![a-z0-9])"

CHECKPOINT_NEARMISS_RE = re.compile(
    r"(?i)"
    # (a) `**Checkpoint:**` / `**Checkpoint**:`
    r"\*\*\s*" + _CHECKPOINT_WORD + r"[^*_\n]{0,40}\*\*"
    # (b) `__Checkpoint:__` / `__Checkpoint__:`
    r"|(?<!\w)__\s*" + _CHECKPOINT_WORD + r"[^*_\n]{0,40}__(?!\w)"
    # (c) `*Checkpoint:*`
    r"|(?<![\w*])\*" + _CHECKPOINT_WORD + r"(?:[^*_\n]{0,40}\S)?\*"
    # (d) `_Checkpoint:_`
    r"|(?<!\w)_" + _CHECKPOINT_WORD + r"(?:[^*_\n]{0,40}\S)?_(?!\w)"
    # (e) a blockquote opening with the word, emphasized any way or bare
    r"|^ {0,3}>\s*(?:\*\*|__|\*|_)?\s*" + _CHECKPOINT_WORD
)

# Destructive-command classes enforced today. Matched anywhere in a fence
# line so wrapper prefixes (`cwrun aws s3api create-bucket`) still match,
# and up to three intervening tokens are allowed between the binary and
# its subcommand so idiomatic global flags (`terraform -chdir=x apply`,
# `helm -n kube-system install`, `aws --profile x s3api create-bucket`)
# can't sidestep the scan. Deliberately fail-closed: a prose-ish fence
# line that happens to match fails the build loudly rather than letting a
# destructive invocation ship ungated. `kubectl apply` and
# `terraform destroy` are intentionally absent — see the section comment
# above and SECURITY.md ("Documented follow-ups").
DESTRUCTIVE_COMMAND_RE = re.compile(
    r"\bterraform(?:\s+\S+){0,3}?\s+apply(?![\w-])"
    r"|\bhelm(?:\s+\S+){0,3}?\s+(?:install|upgrade)(?![\w-])"
    r"|\baws(?:\s+\S+){0,3}?\s+s3api(?:\s+\S+){0,3}?\s+create-bucket(?![\w-])"
)

# Ratchet baseline of pre-existing UNGATED occurrences, keyed by
# (skill name, normalized command line) -> allowed count. Normalization is
# `_command_signature` (strip whitespace and a trailing `\` continuation),
# so the entries survive line-number churn and Checkpoint rewording but not
# command changes. Do not add entries for new content — fix the body
# instead. When one of these gains a Checkpoint or is removed, delete or
# decrement its entry (the stale-entry check below forces this).
CHECKPOINT_BASELINE: dict[tuple[str, str], int] = {
    ("cw-self-managed-inference", "helm install cert-manager coreweave/cert-manager"): 2,
    ("cw-self-managed-inference", "helm upgrade cert-manager coreweave/cert-manager"): 1,
    ("cw-self-managed-inference", "helm install traefik coreweave/traefik"): 1,
}

# Leading block-quote prefix (`> `, possibly nested) — stripped so fences
# and commands inside block-quoted asides are still tracked correctly. The
# leading indentation is unbounded on purpose: a blockquote nested in a list
# item sits further in than CommonMark's three-space document-level
# allowance, and refusing to strip it would hide the whole aside from the
# scan. Only `content` (fence tracking + command matching) is derived from
# this; gate and near-miss detection still read the raw line, so relaxing
# it cannot make a deeply-indented marker count as a gate.
_BLOCKQUOTE_PREFIX_RE = re.compile(r"^[ \t]*(?:>[ \t]?)+")

# A fenced-code-block delimiter line (after block-quote stripping): leading
# indentation, a run of at least three BACKTICKS or TILDES, and whatever
# follows (the info string, on an opening fence).
#
# The CommonMark rules this control depends on, and why each one matters:
#
#   - Both ``` ` ``` and `~` open a fence. Tracking backticks only left the
#     tracker "outside a fence" for the whole of a tilde-fenced block, so a
#     destructive command inside one was never scanned at all.
#   - A closing fence must use the SAME character as its opener, run at
#     least as long, and carry nothing but whitespace after the run. An
#     info-stringed ``` line inside an open block is content, not a closer;
#     getting that wrong would flip the tracker and hide later commands.
#   - The three-space indentation allowance is measured relative to the
#     enclosing block container (a list item), NOT the document margin, so
#     an opening fence's ABSOLUTE indentation is unbounded — this repo
#     already emits four-space list-contained fences (for example
#     `dist/verify-coreweave-workload-health/SKILL.md`). We therefore accept
#     any opening indentation and require a closer to sit within three
#     spaces of its own opener: the container-relative rule, without a full
#     block parser. Erring here OVER-scans (a line that is not really a
#     fence gets its contents checked, failing the build loudly) rather
#     than under-scans, which is the correct direction for this control.
#   - An opening BACKTICK fence's info string may not contain a backtick;
#     such a line is a paragraph, so its neighbours are prose rather than
#     code and there is nothing to scan. (Tilde info strings may contain
#     tildes, hence the char-specific check.)
_FENCE_LINE_RE = re.compile(r"^([ \t]*)(`{3,}|~{3,})(.*)$")


def _command_signature(line: str) -> str:
    """Normalize a fence line into a CHECKPOINT_BASELINE key component."""
    sig = line.strip()
    if sig.endswith("\\"):
        sig = sig[:-1].rstrip()
    return sig


def validate_rendered_bodies(emitted: list[dict], full_build: bool) -> None:
    """Phase 6: enforce the Checkpoint contract on emitted SKILL.md files.

    `full_build` is True when the whole library was built (no `only`
    filter); only then can a CHECKPOINT_BASELINE entry naming a skill that
    no longer exists be distinguished from one that was merely filtered
    out of this build.

    For every skill emitted this build (workflow skills and standalones
    alike), scan dist/<name>/SKILL.md and raise BuildError listing ALL of:

      - destructive commands (DESTRUCTIVE_COMMAND_RE) in fenced code blocks
        with no `> **Checkpoint:**` line earlier in the document and no
        CHECKPOINT_BASELINE allowance left;
      - near-miss Checkpoint markers (CHECKPOINT_NEARMISS_RE) outside
        fenced code blocks — e.g. `> **Checkpoint**:`, a marker that lost
        its blockquote, or one wearing any of markdown's other emphasis
        forms (`__Checkpoint:__`, `*Checkpoint:*`) — which would otherwise
        ship as inert prose while looking like a gate;
      - stale CHECKPOINT_BASELINE entries for skills in this build's
        scope, so the grandfather list only ever shrinks.

    See the section comment above and SECURITY.md for the contract, the
    scope decision, and the documented follow-ups.
    """
    problems: list[str] = []
    baseline_used = dict.fromkeys(CHECKPOINT_BASELINE, 0)
    validated: set[str] = set()

    for record in emitted:
        name = record["name"]
        if name in validated:
            # A workflow-skill / standalone name collision already stomps
            # dist/<name>/; don't compound it by scanning the same file
            # twice and double-consuming baseline allowances.
            continue
        validated.add(name)
        path = DIST_DIR / name / "SKILL.md"
        rel = _rel(path)
        lines = path.read_text(encoding="utf-8").split("\n")

        # (delimiter char, run length, indent) of the open fence, or None
        # when outside one. See _FENCE_LINE_RE for the CommonMark rules.
        fence: tuple[str, int, int] | None = None
        gate_seen = False
        for lineno, raw in enumerate(lines, start=1):
            content = _BLOCKQUOTE_PREFIX_RE.sub("", raw)
            delim = _FENCE_LINE_RE.match(content)
            if delim:
                indent = len(delim.group(1).expandtabs(4))
                run, rest = delim.group(2), delim.group(3)
                char = run[0]
                if fence is None:
                    if not (char == "`" and "`" in rest):
                        # Opening fence; any info string, any indentation.
                        fence = (char, len(run), indent)
                        continue
                elif (
                    char == fence[0]
                    and len(run) >= fence[1]
                    and not rest.strip()
                    and indent <= fence[2] + 3
                ):
                    fence = None  # closing fence: same char, >= length, bare
                    continue
                # else: a fence-looking line INSIDE the block (other
                # delimiter, too short, info-stringed, or indented past its
                # opener's closing allowance) is content, and a backtick
                # "fence" whose info string holds a backtick is a paragraph
                # — fall through and handle the line normally.

            if fence is None:
                if CHECKPOINT_GATE_RE.match(raw):
                    gate_seen = True
                elif CHECKPOINT_NEARMISS_RE.search(raw):
                    problems.append(
                        f"{rel}:{lineno}: near-miss Checkpoint marker "
                        f"({raw.strip()!r}) — the contract marker is the "
                        f"literal '{CHECKPOINT_MARKER}' opening a blockquote "
                        f"line; see SECURITY.md"
                    )
                continue

            # Inside a fenced code block (fence is not None).
            if content.lstrip().startswith("#"):
                continue  # comment line, not an invocation
            match = DESTRUCTIVE_COMMAND_RE.search(content)
            if not match or gate_seen:
                continue
            key = (name, _command_signature(content))
            allowed = CHECKPOINT_BASELINE.get(key, 0)
            if baseline_used.get(key, 0) < allowed:
                baseline_used[key] += 1
                continue
            problems.append(
                f"{rel}:{lineno}: destructive command '{match.group(0)}' "
                f"({content.strip()}) has no preceding "
                f"'{CHECKPOINT_MARKER}' line in the document; see SECURITY.md"
            )

    # Ratchet integrity: a baseline entry that no longer matches its full
    # count means an occurrence was gated, changed, or removed — the entry
    # must be deleted/decremented so the debt list can't quietly regrow.
    # Skills outside a filtered build's scope are skipped (not scanned),
    # but a FULL build that never saw the skill means it was deleted or
    # renamed, and the entry is dead weight.
    for (skill, sig), allowed in CHECKPOINT_BASELINE.items():
        if skill not in validated:
            if full_build:
                problems.append(
                    f"CHECKPOINT_BASELINE names skill '{skill}' which this "
                    f"full build did not emit — delete the stale entry for "
                    f"'{sig}' in build.py"
                )
            continue
        used = baseline_used[(skill, sig)]
        if used < allowed:
            problems.append(
                f"dist/{skill}/SKILL.md: stale CHECKPOINT_BASELINE entry — "
                f"expected {allowed} ungated occurrence(s) of '{sig}', found "
                f"{used}; delete or decrement the entry in build.py "
                f"(the baseline only ratchets down)"
            )

    if problems:
        raise BuildError(
            "Checkpoint contract violation(s) — a destructive command in a "
            "fenced code block requires a preceding '> **Checkpoint:**' gate, "
            "and markers must match the canonical form exactly "
            "(contract: SECURITY.md):\n"
            + "\n".join(f"  {p}" for p in problems)
        )


def main() -> int:
    """End-to-end build entry point.

    Wires the six phases together — the five emit phases, then phase 6's
    read-only Checkpoint validation of what they wrote. Returns 0 on
    success, non-zero on any failure so CI can rely on the exit code.
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

        # Phase 6 (APPSEC-3963): destructive commands in the emitted bodies
        # must be gated by a `> **Checkpoint:**` line. See the
        # validate_rendered_bodies section comment and SECURITY.md.
        validate_rendered_bodies(emitted, full_build=only is None)

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
