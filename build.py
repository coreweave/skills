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
                                    name -> body index, splice any
                                    snippet-inside-snippet includes, then
                                    render each
                                    skill's body.md by substituting
                                    `{{include:NAME}}` with the snippet
                                    body (Jinja2 evaluates `{{ PARAM }}`
                                    placeholders inside it using the
                                    `params` dict from skill.yaml).
    3. Copy shared scripts        — for each skill that requested entries
                                    from _shared-scripts/, copy them into
                                    dist/<name>/scripts/. A skill's own
                                    references/ directory is also copied
                                    into dist/<name>/references/, with
                                    `{{include:NAME}}` markers in its .md
                                    files resolved against the same
                                    `includes:` list as body.md.
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
                                    in a fenced code block with no gate in
                                    scope (its own section or the one before
                                    it) fails the build, as does a near-miss
                                    marker or a stale ratchet-baseline
                                    entry. Read-
                                    only — it never writes or rewrites
                                    output, so a failure means the emitted
                                    files are on disk but the exit code is
                                    non-zero and CI will not merge them.
                                    See validate_rendered_bodies()
                                    (APPSEC-3963).

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
Create API token dialog offers Token name, Expiration, and Comment, and
the token inherits every permission its creating user holds, org-wide,
until it expires. Nothing in this repository can change that; scoped
token types would have to come from the platform (APPSEC-3961).

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
from collections.abc import Iterator
from pathlib import Path
from typing import NamedTuple

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
# Create API token dialog offers only Token name, Expiration, and Comment —
# there is no scope, role, or per-resource selector — so telling a customer to
# "pick read-write" would be advice they cannot act on. What IS rendered is
# TOKEN_ROLES, the minimal authorizations the workflow needs, because a token
# inherits its creating user's roles and that is the only real lever on its
# authority. TOKEN_SCOPE survives as the lint target: any workflow asking for
# write authority must record why (APPSEC-3961).
TOKEN_SCOPE_PARAM = "TOKEN_SCOPE"
TOKEN_SCOPE_VALUES = ("read-only", "read-write")
TOKEN_SCOPE_NARROW = "read-only"

# Snippet whose every include must carry TOKEN_SCOPE. Without this, the
# audit trail would be opt-in: deleting the TOKEN_SCOPE line from a manifest
# would take the justification requirement with it and the build would pass,
# which is the failure mode _validate_tool_restriction exists to prevent for
# the tool-scoping key. Same property, same asset class.
TOKEN_MINTING_SNIPPET = "create-api-token"

# Recommended expirations the Console's dialog actually offers. A value
# outside this set renders verbatim into customer-facing text telling them to
# pick something the dropdown does not have ("8 hrs"), and `Never` is the one
# option the policy says must never be recommended — a non-expiring credential
# carrying its creator's full account authority. Both are build errors.
TOKEN_EXPIRY_PARAM = "TOKEN_EXPIRY"
TOKEN_EXPIRY_VALUES = ("1 hour", "8 hours", "One month", "90 days", "One year")
TOKEN_EXPIRY_FORBIDDEN = ("Never",)

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

# name -> snippets spliced into it by `resolve_nested_includes` (direct edges
# only; `_nested_closure` walks them transitively). Provenance headers list
# every snippet that fed an artifact, and a nested snippet is as much a source
# as a declared one -- without this the header would credit `create-api-token`
# and stay silent about the `browser-consent` block inside it.
SNIPPET_NESTED: dict[str, set[str]] = {}


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

    return resolve_nested_includes(index)


def resolve_nested_includes(index: dict[str, str]) -> dict[str, str]:
    """Phase 2a (second half): splice `{{include:NAME}}` markers that appear
    INSIDE snippet bodies, so one snippet can be composed from another.

    This exists so a rule that several snippets must all state can live in
    exactly one place. `browser-consent` is the motivating case: the
    consent-and-injection contract for driving a customer's authenticated
    Console session has to appear in `create-api-token` (which drives the
    Tokens page) and in `quota-check.md` (which drives the Quotas page).
    Before this, the only way to have it in both was to write it twice --
    and the two copies promptly drifted apart (see the `browser-consent`
    header comment in _snippets/coreweave-platform.md).

    Nesting is resolved BEFORE Jinja2 ever runs, so a nested snippet is
    spliced in as raw text and any `{{ PARAM }}` placeholder it contains is
    evaluated later, against the params of whatever call site pulled the
    OUTER snippet in. That is a sharp edge: a nested snippet with its own
    params silently inherits four different skills' values. Keep nested
    snippets param-free -- `browser-consent` is, deliberately.

    A cycle (`a` includes `b` includes `a`) and a marker naming a snippet
    that does not exist are both build errors.
    """
    SNIPPET_NESTED.clear()
    marker_re = re.compile(INCLUDE_MARKER_RE)

    def expand(name: str, chain: tuple[str, ...]) -> str:
        if name in chain:
            cycle = " -> ".join(chain[chain.index(name):] + (name,))
            raise BuildError(f"snippet include cycle: {cycle}")
        body = index[name]
        nested = SNIPPET_NESTED.setdefault(name, set())

        def splice(match: re.Match[str]) -> str:
            child = match.group(1)
            if child not in index:
                raise BuildError(
                    f"snippet '{name}' includes '{child}', which has no "
                    f"matching snippet in {_rel(SNIPPETS_DIR)}"
                )
            if child == TOKEN_MINTING_SNIPPET:
                # Nesting the token-minting snippet would hide it from
                # _validate_token_scope(), which sees only the names a
                # manifest or standalone entry declares directly. Rather
                # than teach both call sites to chase nesting, keep the
                # minting step where the validation can always see it.
                raise BuildError(
                    f"snippet '{name}' nests '{TOKEN_MINTING_SNIPPET}'. The "
                    f"token-minting snippet must be declared directly (in a "
                    f"manifest's `includes:` or as its own standalone entry) "
                    f"so token-scope validation sees it (APPSEC-3961)."
                )
            nested.add(child)
            return expand(child, chain + (name,))

        return marker_re.sub(splice, body)

    return {name: expand(name, ()) for name in index}


def _nested_closure(name: str) -> list[str]:
    """Every snippet reachable from `name` through nesting, depth-first.

    Used to build provenance: an artifact that inlined `name` also inlined
    everything `name` pulled in. `resolve_nested_includes` has already
    rejected cycles, so the walk terminates.
    """
    seen: list[str] = []

    def walk(current: str) -> None:
        for child in sorted(SNIPPET_NESTED.get(current, ())):
            if child not in seen:
                seen.append(child)
                walk(child)

    walk(name)
    return seen


def render_skill_body(body_md: str, snippet_index: dict[str, str],
                      includes: list[dict], where: str = "body.md") -> str:
    """Phase 2b: substitute `{{include:NAME}}` markers in a skill body.

    `where` names the file being rendered, for error messages only --
    reference files under `references/` go through this same function (see
    `copy_skill_references`), and "body.md references undeclared include"
    pointing at a reference file would send a contributor to the wrong file.

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
            f"{where} references undeclared include(s): "
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


def copy_skill_references(skill_record: dict, snippet_index: dict[str, str]) -> None:
    """Copy a skill's own `references/` directory into dist/<name>/,
    resolving `{{include:NAME}}` markers in the `.md` files on the way.

    Reference material (`skills/<name>/references/*.md`) ships next to the
    rendered SKILL.md so the skill can `Read references/<file>` at runtime.
    Not a numbered phase, but part of assembling dist/.

    References used to be copied verbatim, which quietly put them outside
    the snippet system: a rule shared between a body and a reference had to
    be written twice, and `quota-check.md` and `create-api-token` drifted
    apart for exactly that reason. Markers here resolve against the SAME
    `includes:` list as body.md -- one declaration in skill.yaml covers
    both files, and an undeclared marker is still a build error.

    Non-markdown files (images, scripts, fixtures) are copied untouched.
    """
    src = skill_record["source_dir"] / "references"
    if not src.is_dir():
        return
    dst = DIST_DIR / skill_record["name"] / "references"
    shutil.copytree(src, dst)

    includes = skill_record["manifest"]["includes"]
    for path in sorted(dst.rglob("*.md")):
        text = path.read_text(encoding="utf-8")
        if "{{include:" not in text:
            continue
        rel = f"references/{path.relative_to(dst).as_posix()}"
        rendered = render_skill_body(text, snippet_index, includes, where=rel)
        _write_atomic(path, rendered)


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


def _token_includes(manifest: dict) -> list[tuple[str | None, dict]]:
    """Normalize a workflow manifest's `includes:` to (snippet, params)."""
    out: list[tuple[str | None, dict]] = []
    for inc in manifest.get("includes") or []:
        if isinstance(inc, dict):
            out.append((inc.get("name"), inc.get("params") or {}))
    return out


def _validate_token_scope(manifest: dict, where: str,
                          includes: list[tuple[str | None, dict]] | None = None,
                          ) -> None:
    """No workflow asks for a broad API token without a recorded reason.

    A CoreWeave API access token inherits every permission its creating user
    holds, and the Console offers no scoped token type — so a skill that asks
    for write authority is
    asking the customer to put a full-authority credential in the agent's
    environment. That can be the right call, but it should be a decision on
    record rather than a default nobody revisited.

    Any `create-api-token` include whose TOKEN_SCOPE is not
    TOKEN_SCOPE_NARROW requires a top-level
    `token-scope-justification: "<reason>"`. Mirrors
    _validate_tool_restriction: mandatory non-empty reason, rejected inside
    `frontmatter:`, never emitted — including the part that matters most,
    that the declaration itself is not optional. A `create-api-token`
    include with no TOKEN_SCOPE at all is a build error, so a manifest
    cannot shed the justification requirement by shedding one line.

    `includes` lets the standalone path pass its own (snippet, params) pairs,
    since standalone-skills.yaml entries carry one flat `params:` dict rather
    than an `includes:` list. Without that this validation covered only
    skills/ manifests, and promoting `create-api-token` to a standalone —
    which CONTRIBUTING documents as a worked example — emitted a
    customer-facing skill with `read-write`, no justification, and an
    expiry of `Never`, without complaint.
    """
    frontmatter = manifest.get("frontmatter")
    if isinstance(frontmatter, dict) and TOKEN_SCOPE_JUSTIFICATION_KEY in frontmatter:
        raise BuildError(
            f"{where}: `{TOKEN_SCOPE_JUSTIFICATION_KEY}` belongs at the "
            f"manifest top level, not inside `frontmatter:` (it must never be "
            f"emitted)"
        )

    scopes: list[str] = []
    for name, params in (_token_includes(manifest) if includes is None
                         else includes):
        if TOKEN_SCOPE_PARAM not in params:
            if name == TOKEN_MINTING_SNIPPET:
                raise BuildError(
                    f"{where}: include '{TOKEN_MINTING_SNIPPET}' declares no "
                    f"{TOKEN_SCOPE_PARAM}. Every workflow that has the customer "
                    f"mint a full-authority API token must record the authority "
                    f"it asks for, so that dropping the line cannot silently "
                    f"drop the `{TOKEN_SCOPE_JUSTIFICATION_KEY}` requirement "
                    f"with it: add {TOKEN_SCOPE_PARAM}: "
                    f"{'|'.join(TOKEN_SCOPE_VALUES)} (APPSEC-3961)."
                )
            continue
        scope = params[TOKEN_SCOPE_PARAM]
        if scope not in TOKEN_SCOPE_VALUES:
            raise BuildError(
                f"{where}: include '{name}' sets "
                f"{TOKEN_SCOPE_PARAM}: {scope!r} — must be one of "
                f"{', '.join(TOKEN_SCOPE_VALUES)}"
            )
        scopes.append(scope)

        # The recommendation the customer is told to pick must be a real
        # option, and must not be the one the policy forbids.
        expiry = params.get(TOKEN_EXPIRY_PARAM)
        if expiry in TOKEN_EXPIRY_FORBIDDEN:
            raise BuildError(
                f"{where}: include '{name}' recommends "
                f"{TOKEN_EXPIRY_PARAM}: {expiry!r}. A non-expiring token keeps "
                f"its creator's full account authority forever; skills must "
                f"never recommend it. Use one of "
                f"{', '.join(TOKEN_EXPIRY_VALUES)} (APPSEC-3961)."
            )
        if expiry is not None and expiry not in TOKEN_EXPIRY_VALUES:
            raise BuildError(
                f"{where}: include '{name}' sets "
                f"{TOKEN_EXPIRY_PARAM}: {expiry!r}, which the Console's "
                f"dialog does not offer — it would render verbatim into "
                f"customer-facing text as an option they cannot pick. Use one "
                f"of {', '.join(TOKEN_EXPIRY_VALUES)}."
            )

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
        # Same token-scope guarantees as a skills/ manifest. The entry itself
        # plays the manifest role (it carries `frontmatter:` and may carry a
        # top-level `token-scope-justification:`), and its one flat `params:`
        # dict is passed as the include list. Direct declaration is enough:
        # resolve_nested_includes() rejects any snippet that nests
        # create-api-token, so the minting step is always visible here.
        _validate_token_scope(
            entry,
            f"standalone '{key}'",
            includes=[(snippet, params)],
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
        sources = [_rel(STANDALONE_MANIFEST)]
        for origin_snippet in [snippet, *_nested_closure(snippet)]:
            origin = SNIPPET_SOURCES.get(origin_snippet, "_snippets")
            sources.append(f"{origin}:{origin_snippet}")
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
# Structural Checkpoint enforcement (APPSEC-3963). This section is the
# authority on the control; SECURITY.md summarizes it and CONTRIBUTING.md
# states the rules an author has to follow.
#
# Skills in this repo instruct an agent operating on live customer
# infrastructure. The `> **Checkpoint:**` blockquote is the contract marker
# for a human-confirmation gate: the customer approves before the agent runs
# the destructive command that follows (several bodies deliberately pair a
# Checkpoint with `-auto-approve` — the gate replaces the tool's own
# interactive prompt). This phase makes the gate's EXISTENCE structural: the
# build fails when a destructive command appears in a rendered body with no
# Checkpoint in scope, and when a marker is close to — but not exactly —
# the canonical form, so the contract can't silently drift. Runtime enforcement (making the agent actually stop) is out of
# scope here; the limits are listed below and summarized in SECURITY.md.
#
# Design decisions (the reasoning lives here, where it has to be
# maintained; SECURITY.md carries a summary for a reader who only wants to
# know the control exists):
#
#   - Validation runs on the emitted dist/<name>/SKILL.md artifacts, at the
#     end of the emit phases. Rationale: the reported file:line points at a
#     real committed file; workflow skills and standalones share one code
#     path; and the validator never touches emission internals. Plugin
#     mirrors are byte-for-byte copies of dist/ (phase 4), so validating
#     dist/ covers them. A validation failure exits non-zero, so CI never
#     merges output that failed, even though files were already written.
#
#   - A gate's scope is its own markdown section plus the next one —
#     CHECKPOINT_HEADING_ALLOWANCE, where the trade-off between
#     same-section (rejects a correctly-gated body) and anywhere-earlier
#     (one gate near the top covers the whole file) is derived.
#
#   - Only FENCED code blocks are scanned for commands. Inline `code` in
#     prose is narrative, not a runnable block, and an indentation-only
#     code block (CommonMark's four-space form, no delimiter) is not
#     followed at all — a known limit, and no committed body uses that
#     form today. Fence lines whose first non-space
#     character is `#` are comments, not invocations.
#
#     Which lines count as "fenced" is where this control lives or dies,
#     so the tracker handles BOTH delimiter characters (``` and ~~~),
#     fences at any indentation (list-contained fences sit past the
#     document-level three-space allowance — the repo already emits some),
#     and fences inside blockquotes at any depth. It is a line-at-a-time
#     tracker, not a block parser, so it cannot be EQUIVALENT to
#     CommonMark; what it guarantees is one direction — a line CommonMark
#     calls fenced-code content is never handed to the prose path. See
#     _classify_block_lines for the derivation,
#     tests/test_fence_tracker_commonmark.py for that property checked
#     against markdown-it-py, and tests/test_checkpoint_validator.py for a
#     fixture per bypass shape.
#
#   - A command class is enforced only once every current occurrence
#     already passes, so enabling one is never bundled with body edits.
#     `kubectl apply` therefore stays out for now, and enabling it needs
#     TWO changes, not one: the bodies' ungated occurrences have to gain
#     gates (a content decision for the skills' owners), and the matcher
#     has to reach them. Both are written
#     `kubectl --kubeconfig X --context Y apply`, which is four tokens
#     between binary and subcommand and so past the allowance below —
#     adding the class without widening it would enforce nothing while
#     reading as coverage. Tracked on APPSEC-3963.
#
#   - CHECKPOINT_BASELINE grandfathers the ungated `helm install` /
#     `helm upgrade` occurrences that predate this control (cluster-
#     dependency installs in cw-self-managed-inference that sit before
#     that document's first Checkpoint). The baseline is a ratchet: a NEW
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

# An ATX heading, which is what bounds a gate's scope (see
# CHECKPOINT_HEADING_ALLOWANCE). Matched on the RAW line, so a heading inside
# a block quote does not count: `> ## Foo` opens a section of that aside, not
# of the document. CommonMark's setext form (`Foo` underlined with `===`) is
# not matched; no body uses it, and missing one can only leave a gate in
# scope longer, never shorten it — the same direction the fence tracker errs
# in.
CHECKPOINT_HEADING_RE = re.compile(r"^ {0,3}#{1,6}(?: |$)")

# How many headings may sit between a gate and the destructive command it
# gates. One, meaning the gate must be in the command's own section or the
# one immediately before it.
#
# Zero would be the obvious choice and is wrong: the cw-self-managed-inference
# deploy gate legitimately spans a section boundary — the Checkpoint closes
# the values-file step and the `helm install` it gates opens the next one —
# so a same-section rule rejects a correctly-gated body. Unbounded (the
# original rule) is the other failure: one Checkpoint near the top of a
# document satisfied every command below it, so an ungated `terraform apply`
# in a troubleshooting section or an appendix passed the build. One heading
# of slack is the scope every currently-gated occurrence satisfies with no
# body edits, and it keeps a gate covering a whole multi-block step (several
# `helm install` lines under one heading need one Checkpoint, not one each —
# demanding a confirmation per command trains the click-through habit this
# control exists to prevent).
CHECKPOINT_HEADING_ALLOWANCE = 1

# "Looks like an attempted Checkpoint marker": an emphasis-wrapped
# "checkpoint" run in ANY of markdown's emphasis forms (`*`/`_` for italic,
# `**`/`__` for bold, `***`/`___` for bold-italic), any case, colon inside
# or outside the emphasis; or a blockquote that opens with the word
# "checkpoint", emphasized or bare. Covering only `**...**` here would let
# `__Checkpoint:__`, `*Checkpoint:*` and `___Checkpoint:___` ship as inert
# prose that reads like a gate. Prose that merely mentions the word
# checkpoint mid-sentence, unemphasized, does not match.
#
# One branch per emphasis WIDTH rather than one alternation, because the
# rules that keep each from firing on ordinary prose differ:
#
#   - The doubled and tripled forms tolerate stray inner whitespace
#     (`** Checkpoint:**`), since a repeated delimiter can't be mistaken
#     for a list bullet.
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
    # (a) `**Checkpoint:**` / `**Checkpoint**:` / `***Checkpoint:***`
    r"\*{2,3}\s*" + _CHECKPOINT_WORD + r"[^*_\n]{0,40}\*{2,3}"
    # (b) `__Checkpoint:__` / `__Checkpoint__:` / `___Checkpoint:___`
    r"|(?<!\w)_{2,3}\s*" + _CHECKPOINT_WORD + r"[^*_\n]{0,40}_{2,3}(?!\w)"
    # (c) `*Checkpoint:*`
    r"|(?<![\w*])\*" + _CHECKPOINT_WORD + r"(?:[^*_\n]{0,40}\S)?\*"
    # (d) `_Checkpoint:_`
    r"|(?<!\w)_" + _CHECKPOINT_WORD + r"(?:[^*_\n]{0,40}\S)?_(?!\w)"
    # (e) a blockquote opening with the word, emphasized any way or bare
    r"|^ {0,3}>\s*(?:\*{1,3}|_{1,3})?\s*" + _CHECKPOINT_WORD
)

# Destructive-command classes enforced today. Matched anywhere in a fence
# line so wrapper prefixes (`cwrun aws s3api create-bucket`) still match,
# and up to three intervening tokens are allowed between the binary and
# its subcommand so idiomatic global flags (`terraform -chdir=x apply`,
# `helm -n kube-system install`, `aws --profile x s3api create-bucket`)
# can't sidestep the scan. Deliberately fail-closed: a prose-ish fence
# line that happens to match fails the build loudly rather than letting a
# destructive invocation ship ungated. `kubectl apply` is intentionally
# absent — see the section comment above.
DESTRUCTIVE_COMMAND_RE = re.compile(
    r"\bterraform(?:\s+\S+){0,3}?\s+(?:apply|destroy)(?![\w-])"
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
}

# Leading block-quote prefix (`> `, possibly nested) — stripped so fences
# and commands inside block-quoted asides are still tracked correctly. The
# leading indentation is unbounded on purpose: a blockquote nested in a list
# item sits further in than CommonMark's three-space document-level
# allowance, and refusing to strip it would hide the whole aside from the
# scan. Only `content` (fence tracking + command matching) is derived from
# this; gate and near-miss detection still read the raw line, so relaxing
# it cannot make a deeply-indented marker count as a gate.
#
# The stripped prefix's `>` COUNT is load-bearing, not incidental: inside an
# open fence a `>` run deeper than the opener's is literal code content, not
# a delimiter. Stripping it unconditionally and then matching the remainder
# against _FENCE_LINE_RE let `> ``` ` on a line inside a plain ``` block be
# read as that block's closer, which desynced the tracker and dropped the
# real code that followed out of the scan. See _classify_block_lines.
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
#     `dist/cw-verify-workload-health/SKILL.md`). Openers are
#     therefore accepted at any indentation; the closer rule that pairs
#     with that is derived in _classify_block_lines.
#   - An opening BACKTICK fence's info string may not contain a backtick;
#     such a line is a paragraph, so its neighbours are prose rather than
#     code and there is nothing to scan. (Tilde info strings may contain
#     tildes, hence the char-specific check.)
_FENCE_LINE_RE = re.compile(r"^([ \t]*)(`{3,}|~{3,})(.*)$")

# Per-line verdicts from _classify_block_lines.
_LINE_CODE = "code"  # content of a fenced code block
_LINE_PROSE = "prose"  # everything else
_LINE_DELIMITER = "delimiter"  # the fence line itself: neither scanned nor gated


def _command_signature(line: str) -> str:
    """Normalize a fence line into a CHECKPOINT_BASELINE key component."""
    sig = line.strip()
    if sig.endswith("\\"):
        sig = sig[:-1].rstrip()
    return sig


def _leading_whitespace(line: str) -> str:
    """The line's leading spaces and tabs, verbatim."""
    return line[: len(line) - len(line.lstrip(" \t"))]


class _OpenFence(NamedTuple):
    """A fenced code block's delimiter and the position it was opened at.

    Also used for the line under examination, whose position is compared
    against the open fence's. `char` is empty when the line could not open a
    fence at all. See `_classify_block_lines` for what each field decides.
    """

    char: str  # "`" or "~"; empty if this line cannot open a fence
    run: int  # delimiter run length
    quote_ws: str  # the whole block-quote prefix, verbatim ("" if unquoted)
    inner_ws: str  # leading whitespace after the block-quote prefix

    @property
    def depth(self) -> int:
        """Number of `>` markers in the block-quote prefix."""
        return self.quote_ws.count(">")

    @property
    def outer(self) -> int:
        """Indentation columns before the block-quote prefix."""
        return len(_leading_whitespace(self.quote_ws or self.inner_ws).expandtabs(4))

    @property
    def inner(self) -> int:
        """Indentation columns after the block-quote prefix."""
        return len(self.inner_ws.expandtabs(4))

    def tabbed(self, other: _OpenFence) -> bool:
        """True when a tab makes this line's columns incomparable to `other`'s.

        A tab advances to the next four-column stop, so how wide it is
        depends on the column the enclosing container starts at — which is
        exactly what this tracker does not know, and a tab inside a
        block-quote prefix is partly consumed by the marker on top of that.
        Byte-identical prefixes land in the same column whatever the
        container is, so only DIFFERING prefixes containing a tab are a
        problem.
        """
        mine = self.quote_ws + self.inner_ws
        theirs = other.quote_ws + other.inner_ws
        return "\t" in mine + theirs and mine != theirs


def _classify_block_lines(lines: list[str]) -> Iterator[tuple[int, str, str, str]]:
    """Yield `(lineno, raw, content, verdict)` for each line of a document.

    `content` is `raw` with any block-quote prefix stripped; `verdict` is one
    of `_LINE_CODE`, `_LINE_PROSE`, `_LINE_DELIMITER`.

    This is the whole foundation of the Checkpoint control, so the rules are
    derived rather than guessed, and the derivation is what makes the result
    ONE-DIRECTIONAL: a line CommonMark calls fenced-code content is never
    reported as prose. (The converse is allowed and does happen: prose can
    be reported as code. That direction fails the build loudly instead of
    letting a command through, so it is the safe one.)

    Write `c` for the enclosing container's content indentation — 0 at the
    document margin, 2 inside a `- ` list item, and so on — and measure
    indentation after block-quote stripping. CommonMark gives three facts:

      - an opening fence sits at `c <= open <= c + 3`;
      - a closing fence sits at `c <= close <= c + 3`;
      - a line indented less than `c` has left the container, and a fenced
        block takes no lazy continuation, so leaving the container closes
        the fence (and the line then starts a block at the outer level).

    `c` is unknowable without a full block parser, but the first fact bounds
    it: `max(0, open - 3) <= c <= open`. Writing `lo` for that lower bound
    `max(0, open - 3)`, every non-blank interior line at indentation `i`
    lands in one of four cases, and only two of them are decidable:

      - `open <= i <= max(3, open)` — a delimiter here closes the fence
        under EVERY possible `c`, so it is the closer. Anything else at
        this indentation is content.
      - `i > open + 3` — inside the container under every possible `c` and
        too far in to be a closer under any of them: content.
      - `i < lo` — outside the container under every possible `c`, so the
        fence closes and the line starts a block at the outer level.
      - otherwise (`lo <= i < open`, or a delimiter in
        `(max(3, open), open + 3]`) — AMBIGUOUS. Depending on `c` the line
        is content, a dedented closer, an over-indented closer, or a
        break-out, and those readings put different things inside code
        afterwards. No single choice of state is safe for the rest of the
        document: picking "closes" is what let a column-0 ``` ``` ``` end
        an unclosed list-contained fence and hand the code after it to the
        scan as prose, and picking "content" only moves the same desync a
        few lines later, when the next delimiter gets read as this fence's
        closer. The tracker therefore stops pretending it knows and reports
        every remaining line as code. That can only over-scan, and a body
        that reaches this state fails the build as soon as it contains
        anything command-shaped, which is the signal to write the fence
        unambiguously.

    Blank lines are exempt: they never close a container.

    Three things stop that arithmetic from being trustworthy on its own, and
    each is resolved into the ambiguous case rather than guessed at:

      - A block-quoted fence has TWO positions, the quote's indentation in
        whatever encloses it (`outer`, before the first `>`) and the fence's
        indentation inside the quote (`inner`, after the prefix is
        stripped). Both are carried, and `outer` is only ever compared
        between two lines that are both unquoted, because a `>` marker
        occupies columns this measurement cannot see.
      - A TAB's width depends on which column the enclosing container
        starts at, so a line whose leading whitespace contains a tab is
        only compared to the opener when the two are byte-identical (which
        is the same column in any container).
      - The `>` run itself needs no hedge — a quote marker is exact. An
        EQUAL depth can close, a SHALLOWER one has left the quote (so the
        fence closes and this line starts a block outside it), and a DEEPER
        one is literal content inside the open block, never a closer.

    `tests/test_fence_tracker_commonmark.py` checks the one-directional
    property against markdown-it-py over a generated shape matrix, a random
    fuzz and every committed body.
    """
    # The open fence, or None when outside one.
    fence: _OpenFence | None = None
    # Set once an indentation ambiguity makes the block structure
    # unknowable; from there every line is reported as code.
    unresolved = False

    for lineno, raw in enumerate(lines, start=1):
        quote = _BLOCKQUOTE_PREFIX_RE.match(raw)
        content = raw[quote.end() :] if quote else raw
        if unresolved:
            yield lineno, raw, content, _LINE_CODE
            continue

        this = _OpenFence(
            char="",
            run=0,
            quote_ws=quote.group(0) if quote else "",
            inner_ws=_leading_whitespace(content),
        )
        delim = _FENCE_LINE_RE.match(content)
        if delim:
            run = delim.group(2)
            # A backtick fence's info string may not hold a backtick; such a
            # line is a paragraph, so it can never open a fence.
            if not (run[0] == "`" and "`" in delim.group(3)):
                this = this._replace(char=run[0], run=len(run))

        if fence is None:
            if this.char:
                fence = this
                yield lineno, raw, content, _LINE_DELIMITER
                continue
        elif not raw.strip() and fence.depth == 0:
            pass  # a blank line inside an unquoted block is content
        elif this.depth < fence.depth:
            # Left the block-quote, so the fence ends here and this line
            # starts a new block outside it — a fence when it can open one,
            # otherwise ordinary content of whatever encloses it, which the
            # fall-through below reports as prose. A blank line lands here
            # too when the fence is quoted, which is correct: a blank line
            # ends a block-quote.
            fence = this if this.char else None
            if fence is not None:
                yield lineno, raw, content, _LINE_DELIMITER
                continue
        elif this.tabbed(fence):
            unresolved = True  # tab width depends on the container column
            yield lineno, raw, content, _LINE_CODE
            continue
        elif this.depth == fence.depth == 0 and this.outer < max(0, fence.outer - 3):
            fence = this if this.char else None  # left the container outright
            if fence is not None:
                yield lineno, raw, content, _LINE_DELIMITER
                continue
        elif this.outer < fence.outer:
            unresolved = True  # may or may not have left the container
            yield lineno, raw, content, _LINE_CODE
            continue
        elif this.depth > fence.depth:
            pass  # a deeper quote marker is literal content
        elif (
            delim
            and this.char == fence.char
            and this.run >= fence.run
            and not delim.group(3).strip()
            and fence.inner <= this.inner <= max(3, fence.inner)
        ):
            fence = None  # closer: same char and quote depth, >= length, bare
            yield lineno, raw, content, _LINE_DELIMITER
            continue
        elif this.inner < fence.inner or (
            # Past the band that closes under every possible container
            # indentation, but not past `inner + 3`, so it still closes
            # under some of them. Treating it as content would leave the
            # tracker inside a block CommonMark may have ended, and the
            # next delimiter would then be read as this fence's closer —
            # the desync arrives late but it still arrives.
            delim
            and this.char == fence.char
            and this.run >= fence.run
            and not delim.group(3).strip()
            and this.inner <= fence.inner + 3
        ):
            unresolved = True  # may or may not still be inside the block
            yield lineno, raw, content, _LINE_CODE
            continue
        # else: an interior line indented at least as far as its opener —
        # content, including a fence-looking line with another delimiter, a
        # shorter run, an info string, or an indentation past its opener's
        # closing allowance.

        yield lineno, raw, content, _LINE_CODE if fence else _LINE_PROSE


def validate_rendered_bodies(emitted: list[dict], full_build: bool) -> None:
    """Phase 6: enforce the Checkpoint contract on emitted SKILL.md files.

    `full_build` is True when the whole library was built (no `only`
    filter); only then can a CHECKPOINT_BASELINE entry naming a skill that
    no longer exists be distinguished from one that was merely filtered
    out of this build.

    For every skill emitted this build (workflow skills and standalones
    alike), scan dist/<name>/SKILL.md and raise BuildError listing ALL of:

      - destructive commands (DESTRUCTIVE_COMMAND_RE) in fenced code blocks
        with no `> **Checkpoint:**` line in scope — the gate must sit in the
        command's own markdown section or the one immediately before it, per
        CHECKPOINT_HEADING_ALLOWANCE — and no CHECKPOINT_BASELINE allowance
        left;
      - near-miss Checkpoint markers (CHECKPOINT_NEARMISS_RE) outside
        fenced code blocks — e.g. `> **Checkpoint**:`, a marker that lost
        its blockquote, or one wearing any of markdown's other emphasis
        forms (`__Checkpoint:__`, `*Checkpoint:*`) — which would otherwise
        ship as inert prose while looking like a gate;
      - stale CHECKPOINT_BASELINE entries for skills in this build's
        scope, so the grandfather list only ever shrinks.

    See the section comment above for the contract, the scope decision, and
    the command classes still to be enforced.
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

        # The most recent gate, and how many headings have passed since it —
        # the gate is out of scope once that exceeds
        # CHECKPOINT_HEADING_ALLOWANCE. Counting headings rather than
        # comparing line numbers against section starts is the same rule
        # stated so the state is two integers: a gate is in scope in its own
        # section (0) and the next one (1).
        gate_line: int | None = None
        headings_since_gate = 0
        for lineno, raw, content, verdict in _classify_block_lines(lines):
            if verdict == _LINE_DELIMITER:
                continue

            if verdict == _LINE_PROSE:
                if CHECKPOINT_GATE_RE.match(raw):
                    gate_line = lineno
                    headings_since_gate = 0
                elif CHECKPOINT_HEADING_RE.match(raw):
                    headings_since_gate += 1
                elif CHECKPOINT_NEARMISS_RE.search(raw):
                    problems.append(
                        f"{rel}:{lineno}: near-miss Checkpoint marker "
                        f"({raw.strip()!r}) — the contract marker is the "
                        f"literal '{CHECKPOINT_MARKER}' opening a blockquote "
                        f"line; see CONTRIBUTING.md"
                    )
                continue

            # Inside a fenced code block (verdict == _LINE_CODE).
            if content.lstrip().startswith("#"):
                continue  # comment line, not an invocation
            match = DESTRUCTIVE_COMMAND_RE.search(content)
            in_scope = (
                gate_line is not None
                and headings_since_gate <= CHECKPOINT_HEADING_ALLOWANCE
            )
            if not match or in_scope:
                continue
            key = (name, _command_signature(content))
            allowed = CHECKPOINT_BASELINE.get(key, 0)
            if baseline_used.get(key, 0) < allowed:
                baseline_used[key] += 1
                continue
            if gate_line is None:
                why = f"has no preceding '{CHECKPOINT_MARKER}' line in the document"
            else:
                why = (
                    f"is out of scope of the nearest '{CHECKPOINT_MARKER}' "
                    f"line ({rel}:{gate_line}), which is {headings_since_gate} "
                    f"heading(s) back — a gate reaches its own section and the "
                    f"next one only, so add a Checkpoint in this section or the "
                    f"one before it"
                )
            problems.append(
                f"{rel}:{lineno}: destructive command '{match.group(0)}' "
                f"({content.strip()}) {why}; see CONTRIBUTING.md"
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
            "(contract: CONTRIBUTING.md):\n"
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
            copy_skill_references(skill, snippets)

            body = skill["body_path"].read_text(encoding="utf-8")
            rendered = render_skill_body(body, snippets, skill["manifest"]["includes"])

            sources = [_rel(skill["source_dir"] / "skill.yaml")]
            for inc in skill["manifest"]["includes"]:
                for name in [inc["name"], *_nested_closure(inc["name"])]:
                    origin = SNIPPET_SOURCES.get(name, "_snippets")
                    entry = f"{origin}:{name}"
                    if entry not in sources:
                        sources.append(entry)

            emit_rendered_skill(skill, rendered, sources)
            emitted.append(skill)

        emitted += emit_standalone_skills(snippets, only=only)

        # Phase 6 (APPSEC-3963): destructive commands in the emitted bodies
        # must be gated by a `> **Checkpoint:**` line. See the
        # validate_rendered_bodies section comment.
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
