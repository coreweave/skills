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

3. Commit the source **and** the regenerated `dist/` and `plugins/` trees,
   using a [Conventional Commit](https://www.conventionalcommits.org/en/v1.0.0/)
   message — `fix:` for a fix, `feat:` for new capability. The prefix is what
   decides the next version, so it is worth a moment's thought. See
   [CONTRIBUTING.md, "Commit and open a PR"](../CONTRIBUTING.md#8-commit-and-open-a-pr).
4. **Do not edit any `plugin.json` version, and do not edit a `CHANGELOG.md`.**
   Both are written by the release PR — see
   [Why ordinary PRs never bump versions](#why-ordinary-prs-never-bump-versions).

Once merged, your change sits in a release PR until someone ships it. Nothing
you do here reaches a customer.

---

## Cut a release

Merging a fix does not deliver it — see
[Why a release step exists](#why-a-release-step-exists). Releases are automated:
you don't run a bump script, you review a pull request.

**@coreweave/docs owns releases.** They review and merge the release PRs, and
they are the only humans who can create a tag by hand. Anyone can land a skill
change; shipping it is theirs.

**Releases are per plugin.** Each plugin carries its own version in its
`plugin.json` and its own tag line. A commit touching one plugin's skills opens
a release PR for that plugin only.

1. **Write Conventional Commits as you go.** This is what decides the version;
   there is no separate bump step.

   | Commit prefix | Effect |
   | --- | --- |
   | `fix:` | patch — wording, fixes, pin bumps that don't change what a skill asks permission to do |
   | `feat:` | minor — new skills, or new steps in an existing skill |
   | `feat!:` or `BREAKING CHANGE:` | major — a workflow a customer has to relearn |
   | `chore:`, `docs:`, `ci:`, `test:` | no release on their own |

   Scope the commit to the plugin you changed, so only that plugin releases.

2. **Merge to `main` as usual.** On each merge, release-please opens or updates
   a standing release PR titled `chore(<plugin>): release <version>`. Nothing is
   tagged and nothing reaches customers yet. Merges accumulate in that PR.

3. **Review the release PR.** This is the human gate, and the reason the
   automation stops here. Check:

   - **the version increment is right.** A mislabelled commit produces a
     mislabelled release. A `fix:` that actually adds a step should have been
     `feat:`.
   - **the changelog entry reads for a customer, not a contributor.** What
     release-please drafts is a list of commit subjects. Edit it in the PR to
     say what changed and **what the customer has to do** — usually
     `claude plugin update <name>`, occasionally "re-run the skill against
     existing clusters", sometimes nothing.
   - **any behavior or permission change is called out** — a new tool the skill
     uses, a new credential it asks for, a command it now runs unprompted.
   - **any pin movement is described**, with the upstream compare link (see
     [CONTRIBUTING.md, "Pinned dependencies"](../CONTRIBUTING.md#pinned-dependencies)).
     Describe it; don't paste the command. The content lint scans every `.md`
     under `plugins/`, so a literal `helm install` or curl-pipe-shell line in a
     changelog entry fails CI the same way it would in a skill body.

4. **Merge the release PR.** That is the ship-it action. release-please then
   cuts the tag `{plugin-name}-v{version}` at that commit.

**Only plugins the marketplace lists can be released.** `plugins/` holds five
directories, but `.claude-plugin/marketplace.json` catalogs three
(`coreweave-platform-skills`, `coreweave-cks-skills`,
`coreweave-storage-skills`), and only those three are in
`release-please-config.json`. The other two are uncatalogued, so nobody can
install them and bumping one would publish nothing. Add the marketplace entry
and a config entry first, in its own PR.

### What the repo enforces

Tag rules are enforced by repository rulesets, not convention:

- only @coreweave/docs and the release app can create or delete a tag;
- nobody can force-move a tag to another commit;
- every tag must match `^[a-z][a-z0-9]*(-[a-z0-9]+)*-v[0-9]+\.[0-9]+\.[0-9]+$`,
  so a bare `v0.1.1` and a double-dash `name--v0.1.1` are both rejected at push.

On `main`, `build-and-verify-dist` and `content-lint` must pass before a PR can
merge, and the pin and gate files listed in
[`.github/CODEOWNERS`](../.github/CODEOWNERS) need a code-owner review. The
release PR is an ordinary PR: it has to go green like any other.

### What this needs from an administrator

The release workflow does not work until both of these exist. It fails fast with
a clear message rather than opening a PR nobody can merge.

| Needed | Why |
| --- | --- |
| `RELEASE_APP_ID` variable and `RELEASE_APP_PRIVATE_KEY` secret, from a GitHub App with contents and pull-requests write | GitHub does not fire `pull_request` workflows for PRs opened by `GITHUB_TOKEN`. Since `main` requires status checks, a release PR opened that way would sit at "waiting for status" forever and could never merge. |
| That app added as a bypass actor on the tag-creation ruleset | Tag creation is restricted. Without the bypass, release-please can open the PR but cannot cut the tag when it merges. |

---

## Roll back a release

Installs read `main`, not tags (see
[Tags do not gate what customers install](#tags-do-not-gate-what-customers-install)),
so a rollback is a forward release of reverted content:

1. `git revert` the offending commit(s) on `main`, with a `fix:` message so it
   produces a patch release.
2. Review the release PR that appears, and say in the changelog entry what was
   rolled back and why.
3. Merge it.

The version only ever moves **forward**. Never reuse or move a version number: a
customer who already pulled the bad version won't re-download the same string,
and moving a tag leaves installs that fetched the old commit undetectably stale.
The rulesets make that mistake hard — a tag cannot be force-moved.

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
- Hand-editing a version competes with the release PR for the same line, and the
  release PR is the one that also writes the changelog and cuts the tag.
- `build.py` must stay a pure function of source content, or
  `git diff --exit-code dist/` in CI could never come back clean. Versions live
  in hand-authored `plugin.json` files, which the build does not generate, so
  release-please can own them without fighting it.

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

- Retire `scripts/bump_plugin_version.py` and the `build.yml` advisory that
  calls it. release-please now owns the bump, so the script is redundant and its
  `git describe --tags --abbrev=0` baseline was wrong for a per-plugin diff
  anyway. Left in place here rather than deleted, because it landed recently and
  removing it deserves its own review.
- Enforce Conventional Commit messages. Nothing checks them today, and a
  mislabelled commit silently produces the wrong version. Reviewing the release
  PR catches it, but a PR-title lint would catch it earlier.
- `plugins/coreweave-networking-skills/` and `plugins/coreweave-sunk-skills/`
  are **reserved scaffolding, not plugins awaiting release.** Each holds a
  single `plugin.json` at `0.0.0` and nothing else: no skills, and no
  `skill.yaml` anywhere targets them. Do not add marketplace entries to make
  them releasable — that would publish two installable plugins containing
  nothing. Adding the first real skill to one is what makes it a plugin; give it
  a marketplace entry and a `release-please-config.json` entry then.
