# Releasing the CoreWeave skills library

How to get merged work into customers' hands. This guide covers **what to do**;
[CONTRIBUTING.md](../CONTRIBUTING.md) covers how to build and edit a skill.

| If this is your situation | Go to |
| --- | --- |
| You fixed or added a skill | [Ship an ordinary PR](#ship-an-ordinary-pr) |
| Merged work needs to reach installed customers | [Cut a release](#cut-a-release) |
| A released version is broken | [Roll back a release](#roll-back-a-release) |

---

## Ship an ordinary PR

1. Edit the source under `skills/`, `_snippets/`, or `_shared-scripts/`.
2. Run the build:

   ```bash
   python build.py
   ```

3. Commit the source **and** the regenerated `dist/` and `plugins/` trees. See
   [CONTRIBUTING.md, "Commit and open a PR"](../CONTRIBUTING.md#8-commit-and-open-a-pr).
4. **Do not bump any plugin version.** Cutting the release is a separate act —
   see [Why ordinary PRs never bump versions](#why-ordinary-prs-never-bump-versions).

CI posts an advisory annotation on `main` about unbumped changes. It never
blocks. Act on it when you cut a release, not before.

---

## Cut a release

Merging a fix does not deliver it — see
[Why a release step exists](#why-a-release-step-exists). A release makes merged
work installable: find what changed, bump those versions, write the changelog,
merge, tag.

Two things shape the first command:

- **Releases are per plugin.** Each plugin carries its own version in its
  `plugin.json` and its own tag line. There is no repo-wide version, so "the
  last release" always means the last release *of that plugin*.
- **The repo has no tags yet.** Nothing has been released, so the first release
  has no previous tag to diff against and uses the root commit instead.

1. **Find what changed.** For the first release:

   ```bash
   python scripts/bump_plugin_version.py --check --since $(git rev-list --max-parents=0 HEAD)
   ```

   For every release after that, diff against that plugin's previous tag:

   ```bash
   python scripts/bump_plugin_version.py --check --since coreweave-cks-skills-v0.1.0
   ```

2. **Bump only the plugins whose skills changed.** Leave the others alone; a new
   version number on untouched content tells customers something changed when
   nothing did.

   ```bash
   python scripts/bump_plugin_version.py --since <ref>
   ```

   | Increment | Use for |
   | --- | --- |
   | **patch** | wording, fixes, and pin bumps that don't change what the skill asks permission to do |
   | **minor** | new skills, or new steps in an existing skill |
   | **major** | a workflow a customer has to relearn |

3. **Add an entry to each bumped plugin's `CHANGELOG.md`**, at
   `plugins/<plugin>/CHANGELOG.md`. Move the `Unreleased` heading down to the
   new version number and write under it. Aim it at someone deciding whether to
   update today:

   - what changed, in one line;
   - any **behavior or permission change** — a new tool the skill uses, a new
     credential it asks for, a command it now runs unprompted;
   - any **pin movement**, with the upstream compare link (see
     [CONTRIBUTING.md, "Pinned dependencies"](../CONTRIBUTING.md#pinned-dependencies));
   - the ticket and PR link;
   - **what the customer has to do** — usually `claude plugin update <name>`,
     occasionally "re-run the skill against existing clusters", sometimes
     nothing.

   The file sits at the plugin root, so it ships with the plugin and an
   installed copy carries its own history. `build.py` only rewrites
   `plugins/<plugin>/skills/`, so a rebuild won't touch it.

   Two things to watch. The content lint scans every `.md` under `plugins/`, so
   **describe** a pin movement rather than pasting the command — a literal
   `helm install` or curl-pipe-shell line in a changelog entry fails CI the same
   way it would in a skill body. And commit the file: CI fails on untracked
   files under `plugins/`.

4. **Merge to `main`.** Never tag a commit that isn't on `main`.

5. **Tag each released plugin** as `{plugin-name}-v{version}`. Derive the
   version from `plugin.json` rather than typing it:

   ```bash
   p=coreweave-cks-skills
   v=$(python3 -c "import json;print(json.load(open('plugins/$p/.claude-plugin/plugin.json'))['version'])")
   git tag "$p-v$v" && git push origin "$p-v$v"
   ```

   Do **not** use `claude plugin tag`. It hardcodes a double-dash
   `{name}--v{version}` format with no option to change it, and this repo
   follows the single-dash convention.

   The name prefix is what lets each plugin hold an independent version line; a
   bare `v0.1.1` tag would say nothing about which plugin it released.

   **Only release plugins the marketplace lists.** `plugins/` holds five
   directories, but `.claude-plugin/marketplace.json` catalogs three
   (`coreweave-platform-skills`, `coreweave-cks-skills`,
   `coreweave-storage-skills`). The other two are uncatalogued, so nobody can
   install them and bumping one publishes nothing. Add the marketplace entry
   first, in its own PR.

### What the repo enforces

Tag rules are enforced by repository rulesets, not convention:

- only @coreweave/docs can create or delete a tag;
- nobody can force-move a tag to another commit;
- every tag must match `^[a-z][a-z0-9]*(-[a-z0-9]+)*-v[0-9]+\.[0-9]+\.[0-9]+$`,
  so a bare `v0.1.1` and a double-dash `name--v0.1.1` are both rejected at push.

If you push a conforming tag with the wrong version number, @coreweave/docs can
delete it and push the right one.

On `main`, `build-and-verify-dist` and `content-lint` must pass before a PR can
merge, and the pin and gate files listed in
[`.github/CODEOWNERS`](../.github/CODEOWNERS) need a code-owner review.

---

## Roll back a release

Installs read `main`, not tags (see
[Tags do not gate what customers install](#tags-do-not-gate-what-customers-install)),
so a rollback is a forward release of reverted content:

1. `git revert` the offending commit(s) on `main`.
2. Bump the patch version **forward**. Never reuse or move a version number: a
   customer who already pulled the bad version won't re-download the same
   string, and moving a tag leaves installs that fetched the old commit
   undetectably stale.
3. [Cut a release](#cut-a-release) as usual, and say in the CHANGELOG what was
   rolled back and why.

There is currently no way to tell an existing install to stop using a version.

---

## Background

### Why a release step exists

`claude plugin update` compares **version strings**. It does not compare
content, and it does not compare the commit SHA, even though
`installed_plugins.json` records one. When the installed version equals the
marketplace version, the command reports success without reading a file:

```
$ claude plugin update coreweave-cks-skills@coreweave-skills
✔ coreweave-cks-skills is already at the latest version (0.0.0).
```

This is documented behavior, not a bug — the plugin reference describes
`version` as a pin: *"Setting this pins the plugin to that version string, so
users only receive updates when you bump it."* It was also observed here, when
an install made that morning served skills from a ten-week-old commit while both
`marketplace update` and `plugin update` reported success. See
[`scripts/bump_plugin_version.py`](../scripts/bump_plugin_version.py).

So merging a fix to `main` does not deliver it. A pin bump, a prompt-injection
fix, a permission narrowing — none of it reaches an existing install until a
plugin version changes. Every security fix has two halves: the merge, and the
release.

### Why ordinary PRs never bump versions

- A version only matters at the moment content is *published*. Mid-iteration
  branches are not published.
- A per-PR bump gate would tax every skill edit and buy nothing.
- `build.py` must stay a pure function of source content, or
  `git diff --exit-code dist/` in CI could never come back clean.

### Tags do not gate what customers install

Claude Code resolves version constraints against git tags **when a plugin
declares a dependency with a semver range**. This repo's
`.claude-plugin/marketplace.json` references every plugin by a **relative path**
(`./plugins/coreweave-cks-skills`), and no plugin declares `dependencies`. For a
relative-path plugin with no matching tag, Claude Code *"installs the
marketplace's current copy instead."*

So a customer running `claude plugin install coreweave-cks-skills@coreweave-skills`
gets **the current contents of the default branch**, not a tag. Tags are
release bookkeeping — useful for `--since` diffs and for humans, but not a
delivery mechanism. Branch protection on `main` is what stands between a merge
and a customer's next install.

## Open items

- Reconcile `bump_plugin_version.py --since` and the CI advisory's
  `git describe --tags --abbrev=0` with the `{plugin-name}-v{version}`
  convention. `git describe` returns the most recent tag of *any* plugin, which
  is the wrong baseline for a per-plugin diff.
- Name a release owner. Tag creation is restricted to @coreweave/docs, which
  makes them the de facto releasers, but that was a side effect of needing a
  bypass actor rather than a decision.
- Give `coreweave-networking-skills` and `coreweave-sunk-skills` marketplace
  entries, or delete them. Until then they are unreleasable and have no
  `CHANGELOG.md`.
