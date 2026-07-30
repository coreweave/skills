#!/usr/bin/env python3
"""Verify that what the evals test is what `/plugin install` installs.

WHY THIS EXISTS
---------------
Two consumers read this repo's build output, and they read DIFFERENT TREES:

    skills-evals   mounts  dist/<skill>/
    a customer     installs plugins/<plugin>/skills/<skill>/   (via marketplace.json)

`build.py` keeps those in step by mirroring `dist/<skill>/` into the plugin tree
with a literal copy, and `build.yml` already gates that both trees match a fresh
build. So an ordinary content change is safe: edit a skill, rebuild, and the
installable copy moves with the eval'd copy.

What nothing checks is the ROUTING. `build.py` requires a `plugin:` field in each
`skill.yaml` but never validates it against `.claude-plugin/marketplace.json`. So
transposing a plugin name — `coreweave-cks-skills` -> `coreweave-kcs-skills` —
silently creates a plugin directory no marketplace entry points at, AND leaves the
previous copy behind in the real plugin. From then on every build updates the
orphan while the installable copy stays frozen at whatever it was. The evals keep
testing fresh bytes; customers keep installing stale ones; CI stays green, because
both trees still agree with sources.

This script asserts the chain end to end: every built skill is reachable through
exactly one plugin, that plugin is advertised in the marketplace, and the bytes
are identical to what the evals mount.

PARKED PLUGINS
--------------
A product line can have a reserved name and a `plugins/<name>/` manifest before it
has any skills. Such a skeleton is kept on disk but left OUT of marketplace.json,
so a customer can't install it and receive nothing. This script reports that state
as informational.

The rule is `empty + unadvertised = parked`, `ships skills + unadvertised = broken`.
That makes re-enabling self-enforcing: the moment a skill lands in a parked plugin
the same check flips to a hard error, so whoever adds the first skill has to add the
marketplace entry back in the same PR.

WHAT IT DELIBERATELY DOES NOT DO
--------------------------------
It never looks at a skill's CONTENT, only at packaging. Editing prose, adding a
skill, or changing a shared snippet cannot fail this check. It fires only when a
skill would be uninstallable, unreachable, or served stale — i.e. when the thing
customers get stops being the thing we tested.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DIST_DIR = REPO_ROOT / "dist"
PLUGINS_DIR = REPO_ROOT / "plugins"
MARKETPLACE = REPO_ROOT / ".claude-plugin" / "marketplace.json"


def tree_hash(root: Path) -> str:
    """Content hash of a built skill tree, including relative paths.

    Same construction skills-evals uses for SKILL_VERSION, so a value printed here
    is directly comparable to what an eval run recorded.
    """
    entries = []
    for path in root.rglob("*"):
        if path.is_file():
            entries.append((("./" + path.relative_to(root).as_posix()).encode(), path))
    entries.sort(key=lambda item: item[0])
    outer = hashlib.sha256()
    for rel, path in entries:
        outer.update(hashlib.sha256(path.read_bytes()).hexdigest().encode() + b"  " + rel + b"\n")
    return outer.hexdigest()[:12]


def main() -> int:
    errors: list[str] = []
    warnings: list[str] = []
    parked: list[str] = []

    if not MARKETPLACE.is_file():
        print(f"::error::missing {MARKETPLACE.relative_to(REPO_ROOT)}", file=sys.stderr)
        return 1
    catalog = json.loads(MARKETPLACE.read_text(encoding="utf-8"))

    # name -> directory the marketplace points at. `source` is repo-relative
    # ("./plugins/x"), which is what Claude Code resolves on install.
    advertised: dict[str, Path] = {}
    for entry in catalog.get("plugins", []):
        name, source = entry.get("name"), entry.get("source")
        if not name or not source:
            errors.append(f"marketplace.json: entry missing name or source: {entry!r}")
            continue
        target = (REPO_ROOT / source).resolve()
        advertised[name] = target
        if not target.is_dir():
            errors.append(f"marketplace.json: '{name}' source {source} does not exist")

    on_disk = {p.name for p in PLUGINS_DIR.iterdir() if p.is_dir()} if PLUGINS_DIR.is_dir() else set()

    # A plugin directory nobody advertises is either a PARKED SKELETON or the typo
    # signature, and which one it is depends entirely on whether it ships anything:
    #
    #   empty + unadvertised  -> parked on purpose. The product line has a reserved
    #                            name and a manifest but no skills yet, so it is
    #                            deliberately absent from the catalog: a customer
    #                            cannot install an empty plugin and get nothing.
    #   ships skills + unadvertised -> broken. build.py wrote skills from a `plugin:`
    #                            value that reaches no customer.
    #
    # That distinction also makes re-enabling self-enforcing: the moment a skill
    # lands in a parked plugin, this flips from informational to a hard error, so
    # whoever adds the first skill is forced to add the marketplace entry back.
    for orphan in sorted(on_disk - set(advertised)):
        skills_dir = PLUGINS_DIR / orphan / "skills"
        ships = [p for p in skills_dir.iterdir() if p.is_dir()] if skills_dir.is_dir() else []
        if not ships:
            parked.append(orphan)
            continue
        errors.append(
            f"plugins/{orphan}/ ships {len(ships)} skill(s) but no marketplace.json entry "
            f"points at it — unreachable by /plugin install "
            f"(check the `plugin:` field in the skill sources that built it; if this "
            f"product line is ready, add it back to .claude-plugin/marketplace.json)"
        )
    for missing in sorted(set(advertised) - on_disk):
        errors.append(f"marketplace.json advertises '{missing}' but plugins/{missing}/ is absent")

    # Each plugin's own manifest must be valid and self-consistent.
    for name in sorted(on_disk & set(advertised)):
        manifest = PLUGINS_DIR / name / ".claude-plugin" / "plugin.json"
        if not manifest.is_file():
            errors.append(f"plugins/{name}/: missing .claude-plugin/plugin.json")
            continue
        try:
            declared = json.loads(manifest.read_text(encoding="utf-8")).get("name")
        except json.JSONDecodeError as exc:
            errors.append(f"plugins/{name}/.claude-plugin/plugin.json: invalid JSON ({exc})")
            continue
        if declared != name:
            errors.append(
                f"plugins/{name}/.claude-plugin/plugin.json declares name '{declared}', "
                f"which does not match its directory"
            )

    # The core assertion: dist/<skill> (what evals mount) is reachable through
    # exactly one advertised plugin, byte-identical.
    built = sorted(p.name for p in DIST_DIR.iterdir() if p.is_dir()) if DIST_DIR.is_dir() else []
    if not built:
        print("::error::dist/ is empty — run `python build.py` first.", file=sys.stderr)
        return 1

    for skill in built:
        dist_hash = tree_hash(DIST_DIR / skill)
        homes = [n for n in sorted(on_disk) if (PLUGINS_DIR / n / "skills" / skill).is_dir()]
        installable = [n for n in homes if n in advertised]

        if not homes:
            errors.append(f"{skill}: built into dist/ but shipped by no plugin — uninstallable")
            continue
        if len(homes) > 1:
            errors.append(
                f"{skill}: present in {len(homes)} plugins ({', '.join(homes)}) — "
                f"ambiguous, and all but one will go stale"
            )
        if not installable:
            errors.append(f"{skill}: only in unadvertised plugin(s) {', '.join(homes)}")

        for home in homes:
            plugin_hash = tree_hash(PLUGINS_DIR / home / "skills" / skill)
            if plugin_hash != dist_hash:
                errors.append(
                    f"{skill}: dist/ is {dist_hash} but plugins/{home}/ serves {plugin_hash} "
                    f"— evals would test bytes customers never receive"
                )
            elif home in advertised:
                print(f"  ✓ {skill}: {dist_hash} via {home}")

    # Reverse direction: a plugin shipping something dist/ doesn't know about.
    for name in sorted(on_disk):
        skills_dir = PLUGINS_DIR / name / "skills"
        # A plugin can ship nothing two ways: an empty skills/, or no skills/ at
        # all (the scaffold state). Both warn — treat them the same.
        shipped = (
            sorted(p.name for p in skills_dir.iterdir() if p.is_dir())
            if skills_dir.is_dir()
            else []
        )
        for skill in shipped:
            if skill not in built:
                errors.append(
                    f"plugins/{name}/skills/{skill}/ has no dist/ counterpart "
                    f"— stale output the build no longer regenerates"
                )
        if not shipped and name in advertised:
            warnings.append(
                f"{name} is advertised but ships no skills — a customer can install it and get "
                f"nothing. Either ship a skill into it or remove its marketplace.json entry "
                f"to park it."
            )

    for name in parked:
        print(f"  · {name}: parked — manifest kept, not offered for install until it ships a skill")
    for warning in warnings:
        print(f"  ! {warning}")
    if not errors:
        print(f"\n✓ {len(built)} built skill(s) match what /plugin install serves.")
        return 0

    print(file=sys.stderr)
    for err in errors:
        print(f"::error::{err}", file=sys.stderr)
    print(
        "\nPackaging is broken: what the evals mount is not what a customer would\n"
        "install. This check ignores skill content entirely, so it is not failing\n"
        "because prose changed — look at `plugin:` fields in the skill sources and\n"
        "at .claude-plugin/marketplace.json.\n",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
