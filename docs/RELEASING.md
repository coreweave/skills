# Releasing the CoreWeave skills library

This guide covers **what to do** for each kind of change. The reasoning behind
the rules, and the questions still open, are in [Background](#background) at the
end.

Status: **draft.** The procedures describe what the repo already does.
[Canary releases](#canary-then-promote-unresolved) and [anything beyond a
revert-and-reissue rollback](#a-recall-signal-unresolved) are unresolved.

| If this is your situation | Go to |
| --- | --- |
| You fixed or added a skill | [Ship an ordinary PR](#ship-an-ordinary-pr) |
| Merged work needs to reach installed customers | [Cut a release](#cut-a-release) |
| A released version is broken | [Roll back a release](#roll-back-a-release) |
| You administer the repository | [Repo-admin actions](#repo-admin-actions) |

---

## Ship an ordinary PR

1. Edit the source under `skills/`, `_snippets/`, or `_shared-scripts/`.
2. Run the build:

   ```bash
   python build.py
   ```

3. Commit the source **and** the regenerated `dist/` and `plugins/` trees. See
   [CONTRIBUTING.md, "Commit and open a PR"](../CONTRIBUTING.md#8-commit-and-open-a-pr).
4. **Do not bump any plugin version.** Cutting the release is a separate act.

On pushes to `main`, `bump_plugin_version.py --check` posts an advisory
annotation about unbumped changes. It runs with `continue-on-error: true` and
never blocks. Ignore it on ordinary PRs; act on it when you
[cut a release](#cut-a-release). For why ordinary PRs stay out of the version
file, see [Why ordinary PRs never bump versions](#why-ordinary-prs-never-bump-versions).

---

## Cut a release

Merging a fix does not deliver it — see
[Why a release step exists](#why-a-release-step-exists). A release PR does one
thing: it makes merged work installable.

1. **Find what changed** since the last release:

   ```bash
   python scripts/bump_plugin_version.py --check --since <last-tag>
   ```

   The repo has **no tags yet**, so the first release has no baseline to diff
   against. Until one exists, pass the first commit
   (`--since $(git rev-list --max-parents=0 HEAD)`) or review the plugin trees
   by hand.

2. **Bump only the plugins whose skills changed.** Leave the others alone; a new
   version number on untouched content tells customers something changed when
   nothing did.

   ```bash
   python scripts/bump_plugin_version.py --since <last-tag>
   ```

   Pick the increment:

   | Increment | Use for |
   | --- | --- |
   | **patch** | wording, fixes, and pin bumps that don't change what the skill asks permission to do |
   | **minor** | new skills, or new steps in an existing skill |
   | **major** | a workflow a customer has to relearn |

3. **Add an entry to each bumped plugin's `CHANGELOG.md`**, at
   `plugins/<plugin>/CHANGELOG.md`. Move the `Unreleased` heading down to the
   new version number and write under it. Keep it short and aim it at someone
   deciding whether to update today. Cover:

   - what changed, in one line;
   - any **behavior or permission change** — a new tool the skill uses, a new
     credential it asks for, a command it now runs unprompted;
   - any **pin movement**, with the upstream compare link (see
     [CONTRIBUTING.md, "Pinned dependencies"](../CONTRIBUTING.md#pinned-dependencies));
   - the JIRA ticket and PR link;
   - **what the customer has to do** — usually `claude plugin update <name>`,
     occasionally "re-run the skill against existing clusters", sometimes
     nothing.

   The file lives at the plugin root, so it ships with the plugin and an
   installed copy carries its own history. `build.py` only rewrites
   `plugins/<plugin>/skills/`, so a rebuild won't touch it.

   Two things to watch. `content-lint` scans every `.md` under `plugins/`,
   so **describe** a pin movement rather than pasting the command — a literal
   `helm install` or curl-pipe-shell line in a changelog entry fails CI the
   same way it would in a skill body. And commit the file: CI fails on
   untracked files under `plugins/`.

4. **Merge to `main`.** Never tag a commit that isn't on `main`.

5. **Tag each released plugin.** From the plugin directory:

   ```bash
   claude plugin tag --push
   ```

   The command derives the tag from the manifest and the marketplace entry,
   validates the plugin, checks that `plugin.json` and the marketplace entry
   agree on the version, requires a clean working tree, and refuses if the tag
   already exists. `git tag coreweave-cks-skills--v0.1.1` by hand is equivalent
   if you keep the two files in sync yourself.

   The tag convention for a repository hosting several plugins is
   `{plugin-name}--v{version}`, where the version matches that commit's
   `plugin.json`. The name prefix is what lets each plugin hold an independent
   version line. Do **not** use bare `v0.1.1` tags: with several plugins in one
   repository, a bare tag says nothing about which plugin it released.

   **Only release plugins the marketplace lists.** `plugins/` holds five
   directories, but `.claude-plugin/marketplace.json` catalogs three
   (`coreweave-platform-skills`, `coreweave-cks-skills`,
   `coreweave-storage-skills`). `coreweave-networking-skills` and
   `coreweave-sunk-skills` are on disk and uncatalogued, so nobody can install
   them: bumping one publishes nothing, and `claude plugin tag` has no
   marketplace entry to check the version against. Add the marketplace entry
   first, in its own PR.

> **Known inconsistency.** [`scripts/bump_plugin_version.py`](../scripts/bump_plugin_version.py)
> documents `--since v0.1.0`, and the `build.yml` advisory resolves its baseline
> with `git describe --tags --abbrev=0`, which returns the most recent tag of
> *any* plugin. Both predate the per-plugin convention and need reconciling with
> it before the first release. Tracked in [Open items](#open-items).

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

There is currently no way to tell an existing install to stop using a version —
see [A recall signal](#a-recall-signal-unresolved).

---

## Repo-admin actions

None of the following can be done in a pull request. They need a repository or
organization administrator.

| Action | Why | Status |
| --- | --- | --- |
| **Required status checks** on `main` | `build-and-verify-dist` and `content-lint` must pass before a PR can merge, so a stale `dist/` or unpinned remote code can no longer be merged past a red build. `pin-review` is deliberately NOT required: it is advisory, never fails, and only runs on PRs that touch a pin. | **Configured** — repository ruleset `Security CI` |
| **`CODEOWNERS`** | `.github/CODEOWNERS` routes the pin files, `renovate.json5`, `.github/workflows/`, and the two gate scripts to @coreweave/docs, and the ruleset requires an approving review from a code owner on any PR touching them. | **Done** |
| **Tag ruleset** for `*--v*` — see [the spec below](#tag-ruleset-spec) | Nothing stops a release tag being force-moved or deleted. Even as bookkeeping, a movable tag makes `--since` diffs untrustworthy. The same ruleset restricts who can create one, so releases come from a known set of people or from CI. | Not configured. No tags exist yet, so there is nothing to protect until the first release — configure it alongside the first tag. |
| Confirm branch protection on `coreweave/reference-architecture` | Its `main` already has PR-only merge enforcement, which is load-bearing for our SHA pin — the pin is reviewable only because upstream history is. | Verified, PR-only |

Several rulesets combine on `main`, with the most restrictive setting winning.
Two carry `pull_request` rules: the organization ruleset supplies the approval
count, stale-review dismissal, and last-push approval, and the repository
ruleset `Security CI` supplies code-owner review and the required status checks.
Other organization rulesets contribute deletion, non-fast-forward, branch-name,
and file-path rules. Reading any one alone will misrepresent what is enforced —
check the effective rules instead:

```bash
gh api repos/coreweave/skills/rules/branches/main
```

### Tag ruleset spec

Not applied. One ruleset covers both force-moves and who may create a tag —
`deletion` and `non_fast_forward` protect existing tags, `creation` restricts
new ones to the ruleset's bypass actors. Fill in the release team or CI app
under `bypass_actors` before running this, or nobody will be able to tag at all:

```bash
gh api --method POST repos/coreweave/skills/rulesets --input - <<'JSON'
{
  "name": "Release tags",
  "target": "tag",
  "enforcement": "active",
  "conditions": { "ref_name": { "include": ["refs/tags/*--v*"], "exclude": [] } },
  "rules": [
    { "type": "deletion" },
    { "type": "non_fast_forward" },
    { "type": "creation" }
  ],
  "bypass_actors": []
}
JSON
```

Confirm it took effect the same way as the branch rules:

```bash
gh api repos/coreweave/skills/rulesets --jq '[.[] | select(.target=="tag") | .name]'
```

"Require branches to be up to date before merging" is deliberately **off**. It
would catch the case where two PRs are each green alone but produce a stale
`dist/` together; it also forces every open PR to be updated whenever anything
lands. Revisit it, or a merge queue, when the PR queue is quieter.

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
users only receive updates when you bump it."* It was also observed in the field
on 2026-08-03, when an install made that morning served skills from a ten-week-old
commit while both `marketplace update` and `plugin update` reported success. See
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

This decides whether the canary and rollback proposals below are viable at all.

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

Until one of those is true, the only thing standing between `main` and a
customer's next install is branch protection on `main` — the control to invest
in first.

### Canary then promote (unresolved)

*Not implemented.* The intent is to expose a new version to a small audience
before everyone gets it. With relative-path sources there is no "channel" to
promote between, because an install reads `main`. Options, roughly in order of
cost:

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

### A recall signal (unresolved)

*Not implemented.* "Roll back by promoting the previous tag" only works if
installs consume tags, which they don't. The revert-and-reissue procedure in
[Roll back a release](#roll-back-a-release) is what's available today.

Open questions:

- Is there any way to signal "stop using version X" to an existing install?
  Currently no — which argues for a small canary audience over a fast rollback
  story.
- Should a yanked version be documented somewhere machine-readable?

---

## Open items

- Reconcile `bump_plugin_version.py --since` and the `build.yml` advisory's
  `git describe` with the `{plugin-name}--v{version}` tag convention.
- Name a **release** owner. The pinned-dependency owner is settled —
  @coreweave/docs — but whoever cuts releases is still unassigned, and the two
  need not be the same team.
- Decide the canary approach, or decide explicitly not to have one.
- Apply the [tag ruleset](#tag-ruleset-spec) when the first tag is cut, and
  name the bypass actors it should allow.
- Give `coreweave-networking-skills` and `coreweave-sunk-skills` marketplace
  entries, or delete them. Until then they are unreleasable and have no
  `CHANGELOG.md`.
