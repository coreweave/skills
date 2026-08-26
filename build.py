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

Two deferred decisions, now settled (documented for the next maintainer):

  - Plugin mirror + shared scripts are COPIED, not symlinked. Copies are
    portable for downstream consumers (a plugin tree can be vendored on
    its own), and git stores the small text twice without complaint. The
    rule stays "dist/ is the only source of truth; the plugin tree
    mirrors it" — nothing hand-edits the plugin copy.
  - The emitted frontmatter is the manifest's `frontmatter:` block in
    source order, MINUS the source-only keys in
    SOURCE_ONLY_FRONTMATTER_KEYS (currently just `allowed-tools`).

    `allowed-tools` is NOT a restriction. In a SKILL.md the Skill loader
    reads it as a permission pre-approval: the listed tools may be used
    without prompting the customer, and every unlisted tool remains
    callable. Shipping `allowed-tools: [Bash, Read, Write]` on these
    skills would therefore auto-approve arbitrary shell execution for
    workflows that run `terraform apply`, mint API tokens, and drive
    `kubectl` against live clusters — removing the human confirmation the
    Checkpoint steps depend on. So it stays source-only, as a record of
    the tools a workflow legitimately needs. (This corrects APPSEC-3961's
    first attempt, which propagated it believing it narrowed the skill.)

    The key that actually narrows a skill is `disallowed-tools`: the
    loader removes those tools from the model's pool while the skill is
    active. It is declared under `frontmatter:` and emitted verbatim like
    any other key, for workflow skills and standalone entries alike.

    Every emitted skill must declare a non-empty `disallowed-tools:` list
    or waive it on record with the top-level manifest key
    `disallowed-tools-waived: "<reason>"`; declaring neither is a build
    error, so a dropped or typo'd key cannot silently ship an
    unrestricted skill. The reason string is mandatory (empty or
    non-string fails the build), the waiver is rejected inside
    `frontmatter:`, and it is never emitted.

    Note that a deny-list needs no escape hatch for tools that cannot be
    enumerated statically: a skill that drives the Console via
    environment-provided browser tools simply does not name them, so
    cw-create-cluster's quota check keeps working without a waiver.

API token scope (a different asset, same audit-trail shape)
-----------------------------------------------------------

Tool scoping above narrows what the agent may do. It says nothing about
the authority of the CoreWeave API access token the workflow asks the
customer for, and that token cannot be scoped at all: the Console's
Create API Token dialog offers Name, Expiration, and Note, and the token
inherits every permission its creating user holds, org-wide, until it
expires. APPSEC-3961's primary control (Console support for scoped token
types) is a Console-side dependency and is still MISSING.

So the repo-side controls are: name the minimal IAM roles the workflow
needs (TOKEN_ROLES, rendered — a token inherits its creating user's
roles, so this is what lets a customer mint it as a least-privilege user
instead of an admin), recommend a short expiry (TOKEN_EXPIRY, rendered),
and require a recorded reason for every workflow that asks for write
authority (TOKEN_SCOPE + TOKEN_SCOPE_JUSTIFICATION_KEY, neither
rendered). See _validate_token_scope().

_validate_include_params() backstops all of it. TOKEN_SCOPE was declared
by five manifests and referenced by no snippet, so Jinja2 dropped it and
no customer ever saw the recommendation it implied — an unreferenced
param now fails the build rather than rotting silently.

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
from jinja2 import Environment, StrictUndefined, meta

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
# written into the generated SKILL.md. `allowed-tools` is here because in
# a SKILL.md it is a permission PRE-APPROVAL, not a restriction: the Skill
# loader treats it as "use these without prompting the user", and every
# unlisted tool stays callable. Emitting it would silently auto-approve
# bare `Bash` for skills that run `terraform apply` and mint API tokens,
# removing the human confirmation these workflows depend on. It stays in
# skill.yaml as a record of the tools a workflow legitimately needs; the
# key that actually restricts is DISALLOWED_TOOLS_KEY below.
SOURCE_ONLY_FRONTMATTER_KEYS: tuple[str, ...] = ("allowed-tools",)

# The frontmatter key that genuinely narrows a skill: the Skill loader
# removes these tools from the model's pool while the skill is active.
# Declared under `frontmatter:` and emitted verbatim, so propagation needs
# no special-casing here — this constant exists for the presence check in
# _validate_tool_restriction().
DISALLOWED_TOOLS_KEY = "disallowed-tools"

# Top-level manifest key (skill.yaml, or a standalone-skills.yaml entry)
# that waives the DISALLOWED_TOOLS_KEY requirement for one skill. Its
# value MUST be a non-empty reason string explaining why the skill ships
# with no tool restriction (build error otherwise) — that string is the
# audit trail. The key lives at the manifest top level, never inside
# `frontmatter:`, and is never emitted into the generated SKILL.md.
DISALLOWED_TOOLS_WAIVER_KEY = "disallowed-tools-waived"

# The include param that records the coarse API-token authority a workflow
# needs. It is deliberately NOT rendered into the token step: the Console's
# Create API Token dialog offers only Name, Expiration, and Note — there is
# no scope, role, or per-resource selector — so telling a customer to "pick
# read-write" would be advice they cannot act on. What IS rendered is
# TOKEN_ROLES, the minimal IAM roles the workflow needs, because a token
# inherits its creating user's roles and that is the only real lever on its
# authority. TOKEN_SCOPE survives as the lint target: any workflow asking for
# write authority must record why (APPSEC-3961).
TOKEN_SCOPE_PARAM = "TOKEN_SCOPE"
TOKEN_SCOPE_VALUES = ("read-only", "read-write")
TOKEN_SCOPE_NARROW = "read-only"

# Top-level manifest key carrying the mandatory non-empty reason a workflow's
# token needs broader-than-read-only authority. Mirrors
# DISALLOWED_TOOLS_WAIVER_KEY: manifest top level (never inside
# `frontmatter:`), never emitted, empty/non-string fails the build.
TOKEN_SCOPE_JUSTIFICATION_KEY = "token-scope-justification"

# Include params that are authoring/lint metadata and are deliberately not
# referenced by the snippet they are passed to. Every OTHER declared param
# must appear in that snippet body: Jinja2 silently drops an unreferenced
# param, which is exactly how TOKEN_SCOPE sat in five manifests without ever
# reaching a customer (APPSEC-3961). _validate_include_params() closes that.
SOURCE_ONLY_INCLUDE_PARAMS: tuple[str, ...] = (TOKEN_SCOPE_PARAM,)

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
        _validate_token_scope(manifest, _rel(manifest_path))
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
        _validate_include_params(
            name, snippet_index[name], params, f"include '{name}'"
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


def _validate_include_params(snippet: str, snippet_body: str,
                             params: dict, where: str) -> None:
    """Every declared include param is actually referenced by its snippet.

    Jinja2 ignores a param the template never mentions, so a misspelled or
    orphaned param is silently dropped and the guidance it was supposed to
    render never reaches the customer. That is not hypothetical: TOKEN_SCOPE
    was declared by every skill that mints a token and referenced by no
    snippet, so no customer ever saw a scope recommendation (APPSEC-3961).

    Params in SOURCE_ONLY_INCLUDE_PARAMS are exempt — they exist for the
    build's own checks and are not meant to render.
    """
    env = _jinja_env()
    try:
        referenced = meta.find_undeclared_variables(env.parse(snippet_body))
    except Exception as exc:  # noqa: BLE001 — re-raise as a clean build error
        raise BuildError(f"{where}: parsing snippet '{snippet}': {exc}") from exc

    orphans = sorted(
        set(params) - referenced - set(SOURCE_ONLY_INCLUDE_PARAMS)
    )
    if orphans:
        raise BuildError(
            f"{where}: param(s) {', '.join(orphans)} passed to snippet "
            f"'{snippet}' are never referenced in its body — Jinja2 drops "
            f"them silently, so whatever they were meant to render would not "
            f"reach the customer. Reference them in the snippet, remove them "
            f"from the manifest, or (for build-only metadata) add them to "
            f"SOURCE_ONLY_INCLUDE_PARAMS."
        )


def _validate_token_scope(manifest: dict, where: str) -> None:
    """No workflow asks for a broad API token without a recorded reason.

    A CoreWeave API access token inherits every permission its creating user
    holds — the Console offers no scoped token type (APPSEC-3961's primary
    control, still MISSING) — so a skill that asks for write authority is
    asking the customer to put a full-authority credential in the agent's
    environment. That can be the right call, but it should be a decision on
    record rather than a default nobody revisited.

    Any `create-api-token` include whose TOKEN_SCOPE is not
    TOKEN_SCOPE_NARROW requires a top-level
    `token-scope-justification: "<reason>"`. Mirrors
    _validate_tool_restriction: mandatory non-empty reason, rejected inside
    `frontmatter:`, never emitted.
    """
    frontmatter = manifest.get("frontmatter")
    if isinstance(frontmatter, dict) and TOKEN_SCOPE_JUSTIFICATION_KEY in frontmatter:
        raise BuildError(
            f"{where}: `{TOKEN_SCOPE_JUSTIFICATION_KEY}` belongs at the "
            f"manifest top level, not inside `frontmatter:` (it must never be "
            f"emitted)"
        )

    scopes: list[str] = []
    for inc in manifest.get("includes") or []:
        if not isinstance(inc, dict):
            continue
        params = inc.get("params") or {}
        if TOKEN_SCOPE_PARAM not in params:
            continue
        scope = params[TOKEN_SCOPE_PARAM]
        if scope not in TOKEN_SCOPE_VALUES:
            raise BuildError(
                f"{where}: include '{inc.get('name')}' sets "
                f"{TOKEN_SCOPE_PARAM}: {scope!r} — must be one of "
                f"{', '.join(TOKEN_SCOPE_VALUES)}"
            )
        scopes.append(scope)

    justification = manifest.get(TOKEN_SCOPE_JUSTIFICATION_KEY)
    broad = [s for s in scopes if s != TOKEN_SCOPE_NARROW]

    if TOKEN_SCOPE_JUSTIFICATION_KEY in manifest:
        if not isinstance(justification, str) or not justification.strip():
            raise BuildError(
                f"{where}: `{TOKEN_SCOPE_JUSTIFICATION_KEY}` requires a "
                f"non-empty reason string explaining why this workflow's API "
                f"token needs more than {TOKEN_SCOPE_NARROW} authority"
            )
        if not broad:
            raise BuildError(
                f"{where}: records a `{TOKEN_SCOPE_JUSTIFICATION_KEY}` but no "
                f"include asks for more than {TOKEN_SCOPE_NARROW} — drop the "
                f"justification, or the stale reason will outlive what it "
                f"justified"
            )
        return

    if broad:
        raise BuildError(
            f"{where}: asks for {TOKEN_SCOPE_PARAM}: {broad[0]} but records no "
            f"`{TOKEN_SCOPE_JUSTIFICATION_KEY}`. A CoreWeave token inherits "
            f"all of its creating user's permissions, so a broad scope needs a "
            f"reason on record: add a top-level "
            f"`{TOKEN_SCOPE_JUSTIFICATION_KEY}: \"<reason>\"` (APPSEC-3961)."
        )


def _validate_tool_restriction(manifest: dict, where: str) -> None:
    """Every emitted skill declares a tool restriction, or waives it on record.

    A skill must either carry a non-empty `disallowed-tools:` list under
    `frontmatter:` (the key the Skill loader actually enforces) or set the
    top-level `disallowed-tools-waived: "<reason>"`. Silence is a build
    error: without this check, dropping or typo'ing the key ships an
    unrestricted skill with no audit trail (APPSEC-3961).

    Raises BuildError when the waiver is misplaced (inside `frontmatter:`,
    where it would leak into the shipped SKILL.md), when it carries an
    empty/non-string reason, when both it and a restriction are set, or
    when neither is present.
    """
    frontmatter = manifest.get("frontmatter")
    if not isinstance(frontmatter, dict):
        frontmatter = {}

    if DISALLOWED_TOOLS_WAIVER_KEY in frontmatter:
        raise BuildError(
            f"{where}: `{DISALLOWED_TOOLS_WAIVER_KEY}` belongs at the manifest "
            f"top level, not inside `frontmatter:` (it must never be emitted)"
        )

    declared = frontmatter.get(DISALLOWED_TOOLS_KEY)
    # A present-but-empty list is treated as absent: it restricts nothing,
    # so it must not satisfy the requirement by accident.
    has_restriction = bool(declared)
    waived = DISALLOWED_TOOLS_WAIVER_KEY in manifest

    if waived:
        reason = manifest[DISALLOWED_TOOLS_WAIVER_KEY]
        if not isinstance(reason, str) or not reason.strip():
            raise BuildError(
                f"{where}: `{DISALLOWED_TOOLS_WAIVER_KEY}` requires a non-empty "
                f"reason string explaining why this skill ships with no "
                f"`{DISALLOWED_TOOLS_KEY}` restriction"
            )
        if has_restriction:
            raise BuildError(
                f"{where}: sets both `{DISALLOWED_TOOLS_KEY}` and "
                f"`{DISALLOWED_TOOLS_WAIVER_KEY}` — the waiver is for skills "
                f"that declare no restriction at all; drop one"
            )
        return

    if not has_restriction:
        raise BuildError(
            f"{where}: must declare a non-empty `{DISALLOWED_TOOLS_KEY}:` list "
            f"under `frontmatter:` (the key the Skill loader enforces), or "
            f"waive it on record with a top-level "
            f"`{DISALLOWED_TOOLS_WAIVER_KEY}: \"<reason>\"`. Note that "
            f"`allowed-tools` does NOT restrict anything — it pre-approves "
            f"tools without prompting — so it does not satisfy this."
        )


def emit_rendered_skill(skill_record: dict, rendered_body: str,
                        sources: list[str]) -> Path:
    """Phase 4: write `dist/<name>/SKILL.md` (with provenance), then mirror.

    Output layout:

        dist/<name>/SKILL.md                          (canonical artifact)
        plugins/<plugin>/skills/<name>/SKILL.md       (consumed by Claude)

    The frontmatter block is the manifest's `frontmatter:` mapping (source
    key order preserved, unicode kept) minus SOURCE_ONLY_FRONTMATTER_KEYS.
    `disallowed-tools` rides along in that mapping like any other emitted
    key; _validate_tool_restriction() enforces that it (or a recorded
    waiver) is present.
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
    manifest = skill_record["manifest"]

    source_dir = skill_record.get("source_dir")
    # Error label: standalone records carry the manifest's top-level entry
    # key (what a contributor greps standalone-skills.yaml for), matching
    # phase 5's other error messages; workflow skills cite their skill.yaml.
    where = skill_record.get("error_label") or (
        _rel(source_dir / "skill.yaml")
        if source_dir is not None
        else f"{_rel(STANDALONE_MANIFEST)} entry '{name}'"
    )
    _validate_tool_restriction(manifest, where)

    frontmatter = {
        k: v
        for k, v in manifest["frontmatter"].items()
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

        _validate_include_params(
            snippet, snippet_index[snippet], params, f"standalone '{key}'"
        )
        try:
            body = env.from_string(snippet_index[snippet]).render(**params)
        except Exception as exc:  # noqa: BLE001
            raise BuildError(f"standalone '{key}': rendering '{snippet}': {exc}") from exc
        if not body.endswith("\n"):
            body += "\n"

        name = frontmatter["name"]
        manifest: dict = {"frontmatter": frontmatter, "plugin": plugin}
        # Carry the per-entry restriction waiver (if any) through to the
        # phase-4 writer, which validates it. See the module docstring /
        # APPSEC-3961.
        if DISALLOWED_TOOLS_WAIVER_KEY in entry:
            manifest[DISALLOWED_TOOLS_WAIVER_KEY] = entry[DISALLOWED_TOOLS_WAIVER_KEY]
        record = {
            "name": name,
            "plugin": plugin,
            "source_dir": None,
            "manifest": manifest,
            "body_path": None,
            "is_standalone": True,
            "error_label": f"standalone '{key}'",
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
