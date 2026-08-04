#!/usr/bin/env python3
"""Bump a plugin's version so `claude plugin update` can actually see a change.

WHY THIS EXISTS
---------------
`claude plugin update` compares VERSION STRINGS. It does not compare content,
and it does not compare the commit SHA — even though `installed_plugins.json`
records one. When the installed version equals the marketplace version, the
command short-circuits and reports success without looking at a single file:

    $ claude plugin update coreweave-cks-skills@coreweave-skills
    ✔ coreweave-cks-skills is already at the latest version (0.0.0).

Every plugin in this repo sat at `0.0.0` from its first commit. The result,
observed in the field on 2026-08-03: an install made that morning served skills
from a ten-week-old commit, and BOTH `marketplace update` and `plugin update`
reported success. The only way to get current content was uninstall + reinstall,
which nobody thinks to do because nothing indicates staleness.

Bumping the version is the entire fix. Verified against the same install:

    ✔ Plugin "coreweave-cks-skills" updated from 0.0.0 to 0.1.0. Restart to apply.

WHY THIS ISN'T IN build.py
--------------------------
`build.py` deliberately treats `plugin.json` as hand-authored metadata, and
`build.yml` depends on the build being a pure function of source content — the
provenance header carries no SHA or timestamp precisely so that re-running on
unchanged sources is a no-op.

A build that bumped a version on every invocation would break that invariant:
`git diff --exit-code dist/ plugins/` could never come back clean, because each
CI build would produce a version the committed tree doesn't have. Version bumps
have to be an explicit act, separate from the build.

WHY THIS IS NOT A PR GATE
-------------------------
A check that failed a PR until its author bumped a version would tax every
single skill edit, which is exactly the wrong trade during pre-release
iteration. It also wouldn't buy anything: a version only matters at the moment
content is PUBLISHED, and mid-iteration branches aren't published.

So `--check` is advisory. It exits 0 no matter what it finds, and `build.yml`
runs it only on pushes to main, where it annotates rather than blocks. Cutting a
release is when you run this for real.

PARKED PLUGINS
--------------
A plugin that ships no skills is kept out of `marketplace.json` and cannot be
installed (see `check_plugin_parity.py` for the `empty + unadvertised = parked`
rule). Its version is unreachable, so this script ignores parked plugins
entirely rather than bumping numbers nobody can observe.

USAGE
-----
    # What would a release need to bump? (advisory, exits 0)
    python scripts/bump_plugin_version.py --check --since <ref>

    # Bump the patch version of every plugin whose skills changed since a ref
    python scripts/bump_plugin_version.py --since v0.1.0

    # Bump one plugin explicitly
    python scripts/bump_plugin_version.py coreweave-cks-skills --minor
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
MARKETPLACE = REPO / ".claude-plugin" / "marketplace.json"


def published_plugins() -> list[str]:
    """Plugin names advertised in marketplace.json, i.e. the installable ones."""
    data = json.loads(MARKETPLACE.read_text())
    return [p["name"] for p in data.get("plugins", [])]


def manifest_path(plugin: str) -> Path:
    return REPO / "plugins" / plugin / ".claude-plugin" / "plugin.json"


def read_version(plugin: str) -> str:
    return json.loads(manifest_path(plugin).read_text()).get("version", "0.0.0")


def write_version(plugin: str, version: str) -> None:
    """Rewrite just the version, preserving key order and 2-space indent."""
    path = manifest_path(plugin)
    data = json.loads(path.read_text())
    data["version"] = version
    path.write_text(json.dumps(data, indent=2) + "\n")


def bump(version: str, level: str) -> str:
    try:
        major, minor, patch = (int(part) for part in version.split("."))
    except ValueError:
        sys.exit(f"version {version!r} is not major.minor.patch; fix it by hand")
    if level == "major":
        return f"{major + 1}.0.0"
    if level == "minor":
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"


def changed_since(ref: str, plugin: str) -> list[str]:
    """Skill files under this plugin's installable tree that changed since ref.

    We diff `plugins/<p>/skills/` rather than `skills/` because that is the tree
    a customer actually installs, and build.yml already guarantees it matches
    sources. That keeps this script honest even if a skill is re-homed between
    plugins.
    """
    result = subprocess.run(
        ["git", "diff", "--name-only", f"{ref}...HEAD", "--", f"plugins/{plugin}/skills/"],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        sys.exit(f"git diff against {ref!r} failed: {result.stderr.strip()}")
    return [line for line in result.stdout.splitlines() if line]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("plugin", nargs="?", help="plugin to bump; omit to use --since")
    ap.add_argument("--since", metavar="REF", help="bump every plugin whose skills changed since REF")
    ap.add_argument("--minor", action="store_true", help="bump minor instead of patch")
    ap.add_argument("--major", action="store_true", help="bump major instead of patch")
    ap.add_argument(
        "--check",
        action="store_true",
        help="report what a release would bump and exit 0; never fails",
    )
    args = ap.parse_args()

    if args.major and args.minor:
        sys.exit("pick one of --major or --minor")
    level = "major" if args.major else "minor" if args.minor else "patch"

    known = published_plugins()

    if args.plugin:
        if args.plugin not in known:
            sys.exit(
                f"{args.plugin!r} is not advertised in marketplace.json.\n"
                f"Published plugins: {', '.join(known)}\n"
                "A parked plugin has no installable version to bump."
            )
        targets = [args.plugin]
    elif args.since:
        targets = [p for p in known if changed_since(args.since, p)]
        if not targets:
            print(f"no published plugin's skills changed since {args.since}")
            return 0
    else:
        ap.error("give a plugin name or --since REF")

    for plugin in targets:
        current = read_version(plugin)
        nxt = bump(current, level)
        if args.check:
            n = len(changed_since(args.since, plugin)) if args.since else 0
            detail = f" ({n} skill file(s) changed)" if args.since else ""
            # ::warning:: is a GitHub Actions annotation; harmless locally.
            print(f"::warning::{plugin} is at {current} and has unreleased skill changes{detail}. "
                  f"Bump to {nxt} before publishing, or `claude plugin update` will be a no-op for customers.")
        else:
            write_version(plugin, nxt)
            print(f"{plugin}: {current} -> {nxt}")

    if args.check:
        print(f"\nadvisory only: {len(targets)} plugin(s) would need a bump at release time")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
