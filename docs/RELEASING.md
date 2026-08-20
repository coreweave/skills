# Releasing the CoreWeave skills library

Status: **draft.** This is the starting artifact for APPSEC-3965 (release and
branch-management controls). The rules in [Ordinary PRs](#ordinary-prs) and
[Release PRs](#release-prs) describe what the repo already does. The
[canary](#proposal-canary-then-promote) and [rollback](#proposal-rollback)
sections are **proposals with open questions**, and the
[Repo-admin actions](#repo-admin-actions) list is work no pull request can do.

---

## Why a release step exists at all

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
users only receive updates when you bump it."* It was also observed in the
field on 2026-08-03, when an install made that morning served skills from a
ten-week-old commit while both `marketplace update` and `plugin update`
reported success. See
[`scripts/bump_plugin_version.py`](../scripts/bump_plugin_version.py).

**The consequence that matters for security work: merging a fix to `main` does
not deliver it.** A pin bump, a prompt-injection fix, a permission narrowing —
none of it reaches an existing install until a plugin version changes. Every
security fix therefore has two halves: the merge, and the release.

---

## Ordinary PRs

**Ordinary PRs never bump plugin versions.** This is deliberate and already
enforced by convention rather than by a gate:

- A version only matters at the moment content is *published*. Mid-iteration
  branches are not published.
- A per-PR bump gate would tax every skill edit and buy nothing.
- `build.py` must stay a pure function of source content, or
  `git diff --exit-code dist/` in CI could never come back clean.

CI annotates rather than blocks: `bump_plugin_version.py --check` runs only on
pushes to `main`, with `continue-on-error: true`.

So: fix the skill, rebuild, merge. Cutting the release is a separate act.

---

## Release PRs

A release PR does one thing: it makes merged work installable.

1. **Find what changed** since the last release:

   ```bash
   python scripts/bump_plugin_version.py --check --since <last-tag>
   ```

2. **Bump only the plugins whose skills actually changed.** Leave the others
   alone — an untouched plugin with a new version number tells customers a lie
   and burns their attention.

   ```bash
   python scripts/bump_plugin_version.py --since <last-tag>
   ```

   Semver for skills: **patch** for wording, fixes, and pin bumps that do not
   change what the skill asks permission to do; **minor** for new skills or new
   steps; **major** for a workflow a customer must relearn.

3. **Write a CHANGELOG entry per plugin.** Short, and aimed at someone deciding
   whether to update today:

   - what changed, in one line;
   - which plugins are affected;
   - any **behavior or permission change** — a new tool the skill uses, a new
     credential it asks for, a command it now runs unprompted;
   - any **pin movement**, with the upstream compare link (see
     [CONTRIBUTING.md, "Pinned dependencies"](../CONTRIBUTING.md#pinned-dependencies));
   - the JIRA ticket and PR link;
   - **what the customer has to do** — usually `claude plugin update <name>`,
     occasionally "re-run the skill against existing clusters", sometimes
     nothing.

4. **Merge, then tag.** Never tag a commit that is not on `main`.

---

## Tagging

The supported convention for a repository hosting several plugins is
`{plugin-name}--v{version}`, where the version matches that commit's
`plugin.json`. From the plugin directory:

```bash
claude plugin tag --push
```

The command derives the tag from the manifest and the marketplace entry,
validates the plugin, checks that `plugin.json` and the marketplace entry agree
on the version, requires a clean working tree, and refuses if the tag already
exists. `git tag coreweave-cks-skills--v0.1.1` by hand is equivalent if you keep
the two files in sync yourself.

The name prefix is what lets this repo's five plugins hold independent version
lines. Do **not** use bare `v0.1.1` tags: with several plugins in one
repository, a bare tag says nothing about which plugin it released.

> **Known inconsistency.** [`scripts/bump_plugin_version.py`](../scripts/bump_plugin_version.py)
> documents `--since v0.1.0`, and the `build.yml` advisory resolves its baseline
> with `git describe --tags --abbrev=0`, which returns the most recent tag of
> *any* plugin. Both predate the per-plugin convention and should be reconciled
> with it before the first release. Tracked as an open item below.

---

## Do tags actually gate what customers install?

**Today: no.** This is worth stating plainly, because it decides whether the
canary and rollback proposals below are viable at all.

Claude Code resolves version constraints against git tags **when a plugin
declares a dependency with a semver range**. This repo's
`.claude-plugin/marketplace.json` references every plugin by a **relative path**
(`./plugins/coreweave-cks-skills`), and no plugin declares `dependencies`. For a
relative-path plugin with no matching tag, Claude Code *"installs the
marketplace's current copy instead."*

So a customer running `claude plugin install coreweave-cks-skills@coreweave-skills`
gets **the current contents of the default branch**, not a tag. Per-plugin tags
are, right now, historical markers and release bookkeeping — useful for
`--since` diffs and for humans, but not a security control.

A tag becomes load-bearing only if one of these changes:

- a plugin declares a dependency with a version range, so tag resolution kicks
  in for *that dependency*; or
- the marketplace entry stops using a relative path and points at a git source
  that can be pinned to a ref; or
- customers add the marketplace from a local folder that is a git repo
  (Claude Code v2.1.196+ reads tags there).

**Until one of those is true, the only thing standing between `main` and a
customer's next install is branch protection on `main`.** That is the control
to invest in first.

---

## Proposal: canary then promote

*Not implemented. Viability depends on the section above.*

The intent: expose a new version to a small audience before everyone gets it.

The problem: with relative-path sources, there is no "channel" to promote
between — an install reads `main`. Options worth evaluating, roughly in order
of how much they cost:

1. **A second marketplace repo** (`coreweave-skills-canary`) that volunteers add
   explicitly, tracking a `canary` branch of this repo. Promotion is a
   fast-forward of `main`. Cheap, no Claude Code feature required, and the
   canary audience is self-selecting — which is also its weakness.
2. **Git-source marketplace entries** pinned to a tag, so `main` and the
   released tag can differ. Makes tags load-bearing and unlocks real rollback,
   but changes how every customer's install resolves. Needs a compatibility
   check against installs already in the field.
3. **A bundle plugin with a constrained dependency**, using tag resolution as
   documented. Fits the feature's intended shape, but restructures the
   marketplace around a mechanism built for dependencies rather than channels.

Open questions:

- Who is the canary audience — CoreWeave staff, design partners, opt-in
  customers? Without a named group this stays theoretical.
- How long does a canary soak, and what signal ends it? "No complaints" is not
  a signal; there is currently no telemetry from installed skills.
- Does option 2 break existing installs, or silently re-resolve them?

## Proposal: rollback

*Not implemented. Depends on the same question.*

"Roll back by promoting the previous tag" only works if installs consume tags.
Today they consume `main`, so the honest rollback procedure is:

1. `git revert` the offending commit(s) on `main`.
2. Bump the patch version **forward** — never reuse or move a version number.
   A customer who already pulled the bad version will not re-download the same
   string, and moving a tag leaves installs that fetched the old commit
   undetectably stale.
3. Release as usual, and say in the CHANGELOG what was rolled back and why.

Open questions:

- Is there any way to signal "stop using version X" to an existing install?
  Currently no — which argues for a small canary audience over a fast rollback
  story.
- Should a yanked version be documented somewhere machine-readable?

---

## Repo-admin actions

None of the following can be done in a pull request. They need a repository or
organization administrator.

| Action | Why | Status |
| --- | --- | --- |
| **Required status checks** on `main` | Org ruleset CBS-1205 requires a PR and one approval, but **no status check is required** — a PR can merge with `build-and-verify-dist` red, shipping a stale `dist/` to customers. | Not configured |
| **`CODEOWNERS`** | No CODEOWNERS file exists. Pin files, `renovate.json5`, and `.github/workflows/` should require a named reviewer, so a pin bump cannot be approved by someone who did not read the upstream diff. | Missing |
| **Tag protection ruleset** for `*--v*` | Nothing stops a tag being force-moved to a different commit. Even as bookkeeping, a movable release tag makes `--since` diffs untrustworthy. | Not configured |
| **Restrict who can push tags** | Releases should come from a known set of people or from CI. | Not configured |
| Confirm branch protection on `coreweave/reference-architecture` | Its `main` already has PR-only merge enforcement, which is load-bearing for our SHA pin — the pin is reviewable only because upstream history is. | Verified, PR-only |

## Open items

- Reconcile `bump_plugin_version.py --since` and the `build.yml` advisory's
  `git describe` with the `{plugin-name}--v{version}` tag convention.
- Name a release owner, and a pinned-dependency owner (CONTRIBUTING.md leaves
  the latter blank on purpose).
- Decide the canary approach, or decide explicitly not to have one.
- Add a `CHANGELOG.md` per plugin, or one at the repo root with plugin
  sections. None exists today.
