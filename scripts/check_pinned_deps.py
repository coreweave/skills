#!/usr/bin/env python3
"""Keep every pinned dependency internally consistent, and keep the
automation that proposes pin bumps from rotting silently.

WHY THIS EXISTS
---------------
Three customer-facing skills fetch coreweave/reference-architecture and run
its Terraform and Helm charts under the customer's own credentials, and one
installs a `s5cmd` binary onto their PATH. Every one of those dependencies is
pinned, which buys nothing unless two things stay true:

  1. All copies of a pin agree. `dist/` and `plugins/` are generated, so a
     mismatch means someone hand-edited a rendered file or forgot to re-run
     `python build.py` — and the copy a customer installs is then not the copy
     anyone reviewed. (sibling: scripts/lint_skill_content.py, which rejects
     content that is *unpinned*; this script checks that what IS pinned is
     pinned consistently.)

  2. The pin bump proposals keep arriving. Renovate finds these pins with
     custom regex managers in .github/renovate.json5. Rename the snippet,
     re-wrap a `--version` line, or reformat the fetch block, and the regex
     quietly stops matching: no error anywhere, just a pin that never gets
     proposed for update again and drifts years behind upstream. So this
     script re-runs each manager's own matchStrings against its own
     managerFilePatterns and fails if a manager matches nothing.

CHECKS
------
    single-source     the ref-arch SHA appears exactly once outside the
                      generated trees (it lives in the snippet, so a bump is
                      a one-line change).
    pin-agreement     every occurrence of a given pin — ref-arch SHA, each
                      Helm chart's `--version`, the s5cmd release — is
                      identical repo-wide, source and rendered alike.
    rendered-matches  every pin value found under dist/ or plugins/ also
                      appears in a source file (nothing is generated-only).
    manager-live      every custom regex manager in .github/renovate.json5
                      still matches at least once in the files it claims.

Renovate evaluates matchStrings with JavaScript RegExp; this script uses
Python `re` after rewriting `(?<name>` to `(?P<name>`. The two agree for the
patterns in use — the point is to catch a regex that has stopped matching
entirely, not to reimplement Renovate.

Run locally with:

    python scripts/check_pinned_deps.py
"""

from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
RENOVATE_CONFIG = REPO_ROOT / ".github" / "renovate.json5"

SOURCE_DIRS = ("skills", "_snippets")
GENERATED_DIRS = ("dist", "plugins")

# name -> (compiled pattern, capture group holding the pin value). Each pattern
# is written to match the same text in a source file and in its rendered copy.
PINS = {
    "reference-architecture SHA": (
        re.compile(r"CW_REF_ARCH_SHA=([a-f0-9]{40})"), 1),
    "s5cmd release": (
        re.compile(r"S5CMD_VERSION=([0-9]+\.[0-9]+\.[0-9]+-[a-f0-9]+)"), 1),
}

# Helm pins are per-chart, so they get their own pass: the chart name is part
# of the identity, not part of the value.
HELM_RE = re.compile(
    r"coreweave/(cert-manager|traefik)[\s\S]{0,200}?--version (\d+\.\d+\.\d+)")


def markdown_files(dirs: tuple[str, ...]) -> list[Path]:
    out: list[Path] = []
    for name in dirs:
        root = REPO_ROOT / name
        if root.is_dir():
            out.extend(sorted(root.rglob("*.md")))
    return out


def rel(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


# Directories with nothing a Renovate manager could ever point at, skipped
# when enumerating candidate files for the manager-live check.
_UNSEARCHED_DIRS = {
    ".git", ".venv", "venv", "env", "__pycache__", ".pytest_cache",
    ".mypy_cache", ".ruff_cache", ".idea", ".vscode", ".claude",
    "node_modules",
}


def repo_files() -> list[str]:
    """Every repo-relative file path a custom manager might target.

    The pin CHECKS above only concern rendered markdown, but a manager can
    legitimately point anywhere — the gitleaks image pin lives in a workflow
    YAML. Scoping the manager-live search to markdown made any such manager
    report "matches no file" and fail the build, which would push the next
    person to delete the manager rather than the assumption.
    """
    out: list[str] = []
    for path in sorted(REPO_ROOT.rglob("*")):
        if not path.is_file():
            continue
        parts = path.relative_to(REPO_ROOT).parts
        if any(p in _UNSEARCHED_DIRS for p in parts[:-1]):
            continue
        out.append(rel(path))
    return out


def collect() -> dict[str, dict[str, list[str]]]:
    """pin label -> value -> list of repo-relative files holding that value."""
    found: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    for path in markdown_files(SOURCE_DIRS + GENERATED_DIRS):
        text = path.read_text(encoding="utf-8")
        for label, (pattern, group) in PINS.items():
            for match in pattern.finditer(text):
                found[label][match.group(group)].append(rel(path))
        for chart, version in HELM_RE.findall(text):
            found[f"Helm chart {chart}"][version].append(rel(path))
    return found


def check_agreement(found: dict) -> list[str]:
    """Every occurrence of a pin must carry the same value."""
    findings = []
    for label, by_value in sorted(found.items()):
        if len(by_value) <= 1:
            continue
        detail = "; ".join(
            f"{value} in {', '.join(sorted(set(files)))}"
            for value, files in sorted(by_value.items())
        )
        findings.append(
            f"[pin-agreement] {label} disagrees across the repo: {detail} — "
            f"the rendered trees are generated, so re-run `python build.py` "
            f"after changing the source pin"
        )
    return findings


def check_single_source(found: dict) -> list[str]:
    """The ref-arch SHA lives once in source, so a bump is a one-line change."""
    label = "reference-architecture SHA"
    sources = [
        f for files in found.get(label, {}).values() for f in files
        if f.startswith(SOURCE_DIRS)
    ]
    if not sources:
        return [
            f"[single-source] no {label} found in {'/, '.join(SOURCE_DIRS)}/ — "
            f"the pin should live in _snippets/coreweave-cks.md"
        ]
    if len(sources) > 1:
        return [
            f"[single-source] {label} appears in {len(sources)} source files "
            f"({', '.join(sorted(sources))}) — keep it in the "
            f"fetch-pinned-ref-arch snippet only, so one edit updates every "
            f"skill that fetches the repo"
        ]
    return []


def check_rendered_matches(found: dict) -> list[str]:
    """No pin value may exist only in a generated tree."""
    findings = []
    for label, by_value in sorted(found.items()):
        for value, files in sorted(by_value.items()):
            if any(f.startswith(SOURCE_DIRS) for f in files):
                continue
            findings.append(
                f"[rendered-matches] {label} = {value} appears only in "
                f"generated files ({', '.join(sorted(set(files)))}) with no "
                f"source counterpart — a rendered file was hand-edited; edit "
                f"the source and re-run `python build.py`"
            )
    return findings


def load_renovate_config() -> dict:
    """Parse renovate.json5. Handles the // comments and trailing commas the
    repo's config actually uses; a full JSON5 parser is not worth a dependency
    for one file."""
    text = RENOVATE_CONFIG.read_text(encoding="utf-8")
    # Strip // comments that start a line or follow whitespace, never inside a
    # string (the config has no URLs in comment position mid-string).
    text = re.sub(r"(?m)^\s*//.*$", "", text)
    text = re.sub(r",(\s*[}\]])", r"\1", text)
    return json.loads(text)


def manager_file_globs(patterns: list[str]) -> list[str]:
    """Renovate managerFilePatterns are `/regex/` or plain globs."""
    globs = []
    for pattern in patterns:
        if pattern.startswith("/") and pattern.endswith("/"):
            globs.append(pattern[1:-1])
        else:
            globs.append(re.escape(pattern))
    return globs


def check_managers_live() -> list[str]:
    """Each custom regex manager must still match something."""
    if not RENOVATE_CONFIG.is_file():
        return [f"[manager-live] {rel(RENOVATE_CONFIG)} not found"]
    try:
        config = load_renovate_config()
    except json.JSONDecodeError as exc:
        return [f"[manager-live] {rel(RENOVATE_CONFIG)} does not parse: {exc}"]

    findings = []
    managers = config.get("customManagers") or []
    if not managers:
        return ["[manager-live] no customManagers in renovate.json5 — the skill "
                "pins would never be proposed for update"]

    all_files = repo_files()
    for manager in managers:
        label = manager.get("description") or manager.get("depNameTemplate", "?")
        globs = manager_file_globs(manager.get("managerFilePatterns") or [])
        targets = [f for f in all_files if any(re.search(g, f) for g in globs)]
        if not targets:
            findings.append(
                f"[manager-live] manager '{label}' matches no file: its "
                f"managerFilePatterns {manager.get('managerFilePatterns')} "
                f"point at nothing that exists — Renovate would silently stop "
                f"proposing this pin"
            )
            continue
        total = 0
        for match_string in manager.get("matchStrings") or []:
            pattern = re.compile(match_string.replace("(?<", "(?P<"))
            for target in targets:
                try:
                    text = (REPO_ROOT / target).read_text(encoding="utf-8")
                except (UnicodeDecodeError, OSError):
                    # The candidate set is the whole repo now, so a loose
                    # managerFilePattern can sweep in a binary. A pin is never
                    # in one; skip rather than crash the gate.
                    continue
                total += len(pattern.findall(text))
        if not total:
            findings.append(
                f"[manager-live] manager '{label}' matched 0 times in "
                f"{', '.join(targets)} — the pin was reformatted out from "
                f"under its regex; Renovate would silently stop proposing it"
            )
    return findings


def main() -> int:
    found = collect()
    findings = (
        check_single_source(found)
        + check_agreement(found)
        + check_rendered_matches(found)
        + check_managers_live()
    )

    if findings:
        for finding in findings:
            print(finding, file=sys.stderr)
        print(
            f"\n{len(findings)} pinned-dependency finding(s). Every executable "
            "dependency a skill fetches is pinned, every copy of a pin agrees, "
            "and the Renovate managers that propose bumps still match — see "
            "the module docstring of scripts/check_pinned_deps.py and the "
            "\"Pinned dependencies\" section of CONTRIBUTING.md.",
            file=sys.stderr,
        )
        return 1

    summary = ", ".join(
        f"{label} @ {next(iter(by_value))}"
        for label, by_value in sorted(found.items())
    )
    print(f"✓ pins consistent and proposable: {summary}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
