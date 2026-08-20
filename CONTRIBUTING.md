# Contributing to the CoreWeave skills library

This guide is for CoreWeave engineers adding or editing skills. If you're a
customer who wants to install and use the skills, see the [README](README.md).

This repository does not accept external contributions. Pull requests from
outside CoreWeave will be closed. If you've found a bug or have an idea for a
skill, please open an issue instead — feedback submitted through issues or pull
requests is subject to the feedback terms in the [LICENSE](LICENSE).

Every shipped skill is built from sources in this repo by a single Python build
step (`build.py`) that inlines shared procedures, resolves parameters, and emits
fully rendered `SKILL.md` files into `dist/` and into the marketplace plugin
trees under `plugins/`.

This guide covers **how** to add and build a skill. CoreWeave engineers can find
the architecture design doc, which covers **why** the library is built this way,
in the team's internal documentation.

---

## Architecture at a glance

```
.
├── .claude-plugin/
│   └── marketplace.json             Repo-root marketplace catalog. Lists
│                                    every plugin in plugins/ and is what
│                                    `/plugin marketplace add` reads.
│
├── _snippets/                       Tagged regions of shared prose, inlined
│                                    into workflow skills at build time.
│   ├── coreweave-platform.md        Cross-cutting (tokens, kubeconfig, …)
│   ├── coreweave-cks.md             CKS-specific atomics
│   ├── coreweave-storage.md         Storage atomics (CAIOS, DFS, PV)
│   ├── shared-interview.md          Shared interview / prompting atomics
│   └── shared-verify.md             Verification atomics (Grafana, kubectl)
│
├── _shared-scripts/                 Code reuse (separate from prose reuse).
│                                    Copied/symlinked into skills at build.
│
├── skills/                          One subdir per workflow skill — the
│                                    sources humans actually edit.
│   └── <name>/
│       ├── skill.yaml               Frontmatter + plugin + include list.
│       └── body.md                  Bespoke prose with {{include:…}} markers.
│
├── standalone-skills.yaml           Declares which snippets ALSO ship as
│                                    standalone skills (dual-use).
│
├── plugins/                         One marketplace plugin per product
│                                    line + a shared platform plugin.
│   └── <plugin-name>/
│       ├── .claude-plugin/
│       │   └── plugin.json          Per-plugin manifest (name, version,
│       │                            description, author).
│       └── skills/                  Built SKILL.md files. Claude Code
│                                    auto-discovers everything in here —
│                                    there is no skill list to maintain.
│                                    A plugin with no skills yet is PARKED:
│                                    its directory and manifest stay, but it
│                                    is left out of marketplace.json so a
│                                    customer can't install an empty plugin.
│                                    Add the entry back in the same PR that
│                                    ships its first skill — CI fails until
│                                    you do (scripts/check_plugin_parity.py).
│
├── build.py                         Inlines snippets, resolves params,
│                                    emits dist/ + plugin trees.
│
├── evals/                           Bundle-level trigger evals. Per-skill
│                                    correctness evals live next to each
│                                    skill, not here.
│
├── dist/                            Rendered SKILL.md output. COMMITTED.
│                                    CI fails if it drifts from sources.
│
└── .github/workflows/build.yml      Rebuilds on every PR, fails if dist/
                                     is stale.
```

The directory split mirrors the two reuse dimensions:

- **Prose reuse → `_snippets/`** (markdown bodies inlined into many skills).
- **Code reuse → `_shared-scripts/`** (scripts copied into many skills).

Prose and code are kept separate because they have different reuse rules: a
snippet is inlined and parameterized at build time, whereas a script is copied
and executed as-is.

---

## Quickstart: add a new workflow skill

This is the most common contributor task. A new workflow skill = one how-to
document, written once, that Claude can drive for a customer.

### 1. Pick the right grain

A skill maps **1:1 to a how-to doc**:
- ✓ `deploying-cks-cluster`
- ✓ `provisioning-a-sunk-cluster`
- ✗ `add-one-user` (too granular, so make this a snippet)
- ✗ `everything-cks` (too broad, so split it up)

If your idea sits between two of those bullets, lean toward the narrower one.
Bundling can come later. Splitting is harder.

### 2. Create the skill directory

```bash
cp -r skills/_example-skill-template skills/<your-skill-name>
cd skills/<your-skill-name>
```

You now have two files to edit: `skill.yaml` and `body.md`. You will **not**
create anything under `dist/` by hand.

### 3. Fill in `skill.yaml`

This is the manifest. The build reads it to figure out (a) what frontmatter to
emit, (b) which plugin to ship in, and (c) which snippets to inline.

```yaml
frontmatter:
  name: <your-skill-name>
  description: >-
    Walk the customer through <workflow>. Triggers on phrases like
    "<phrase 1>", "<phrase 2>", "<phrase 3>". Use this when the
    customer mentions <X> or <Y>.
  allowed-tools:
    - Bash
    - Read

plugin: <coreweave-cks-skills | coreweave-storage-skills | …>

includes:
  - name: create-api-token
    params:
      TOKEN_NAME: my-workflow-token
      TOKEN_SCOPE: read-only
      SECRET_STORE_HINT: your password manager
```

The `description` is the single most important field. Be **"pushy"** (see
[Glossary](#glossary)): list concrete phrases a customer would actually say,
not a vague summary. If you're unsure whether you're being too aggressive,
that's what the bundle-level trigger eval set is for. Add three positive and
two negative queries (see [`evals/README.md`](evals/README.md)) and let CI confirm
you're not stealing traffic from another skill.

### 4. Write `body.md`

Open `body.md` and write the bespoke prose that's unique to this workflow.
Wherever you want a shared procedure inlined, drop an include marker on its own
line:

```markdown
## Step 1 — Get an API token

{{include:create-api-token}}

## Step 2 — Do the workflow-specific thing
…
```

Every `{{include:NAME}}` you reference must also appear in `skill.yaml` under
`includes`. Otherwise, the build fails with a clear error pointing at the
missing entry.

If a snippet you need doesn't exist yet, see
["Add a shared snippet"](#add-a-shared-snippet).

### 5. Run the build

Install the build dependencies once (see
[Run the build locally](#run-the-build-locally)), then run:

```bash
python build.py
```

The build performs the following steps:

1. Parses every `skills/*/skill.yaml`.
2. Indexes every tagged region in `_snippets/*.md`.
3. Renders `body.md` by substituting `{{include:NAME}}` with the matching
   snippet, after running the snippet through Jinja2 with the `params` you
   declared.
4. Writes `dist/<your-skill-name>/SKILL.md`.
5. Copies the same file into
   `plugins/<your-plugin>/skills/<your-skill-name>/SKILL.md`.

### 6. Check the rendered output

```bash
$EDITOR dist/<your-skill-name>/SKILL.md
```

Read the rendered output end-to-end. The inlined snippets should read naturally
next to your bespoke prose. They're written as `## Heading` blocks for exactly
this reason. If a parameter looks wrong, fix the `params:` block in `skill.yaml`
and rebuild.

### 7. Add evals

Each skill needs two kinds of evals:

- **Trigger evals** (`evals/`): add at least three positive queries (phrasings
  that should fire your skill) and two negative queries (phrasings that should
  *not* fire it). See [`evals/README.md`](evals/README.md).
- **Correctness evals** (`skills/<your-skill-name>/evals/evals.json`): per-skill
  scenarios that exercise the rendered SKILL.md end-to-end. Copy from an
  existing skill's `evals/` directory as a starting point.

### 8. Commit and open a PR

```bash
git add skills/<your-skill-name> dist/<your-skill-name> \
        plugins/<your-plugin>/skills/<your-skill-name> evals/
git commit -m "Add <your-skill-name> workflow skill"
```

CI rebuilds from scratch and fails your PR if the committed `dist/` doesn't
match the fresh build. If that happens: run `python build.py` locally, commit
the resulting diff, and push.

---

## Add a shared snippet

Extract a procedure into `_snippets/` when **three or more workflow skills will
inline the same content**, or when the procedure is canonical enough that drift
between copies would be a correctness bug (for example, the official way to mint
API tokens).

### Pick the right file

| File | What goes in it |
| --- | --- |
| `_snippets/coreweave-platform.md` | Cross-cutting CoreWeave platform atomics (tokens, kubeconfig, IAM). |
| `_snippets/coreweave-cks.md` | CKS-specific (clusters, node pools, operators). |
| `_snippets/coreweave-storage.md` | Storage (CAIOS, DFS, PV). |
| `_snippets/shared-interview.md` | Shared interview and prompting atomics. |
| `_snippets/shared-verify.md` | Verification procedures (Grafana, kubectl probes). |

The build doesn't care which file a snippet lives in. Names are globally
unique. The file split is for human navigation.

### Add a tagged region

```markdown
<!-- snippet:my-new-procedure -->
## A heading the inlined output should open with

Step-by-step prose. Use `{{ PARAMETER_NAME }}` (Jinja2 syntax, with
spaces) anywhere the value should be filled in per-workflow.

1. First step using `{{ TOOL_NAME }}`.
2. Second step.
<!-- /snippet:my-new-procedure -->
```

Conventions (a maintainer can change these, but the build doesn't enforce them):

- Names are kebab-case verbs (`create-api-token`, not `api-tokens`).
- Open with a `##` heading so the inlined result reads as a sub-section.
- Parameter placeholders are `{{ UPPER_SNAKE_CASE }}` with spaces.

### Consume it from a workflow

Add an entry under `includes:` in the workflow's `skill.yaml`:

```yaml
includes:
  - name: my-new-procedure
    params:
      TOOL_NAME: kubectl
      PARAMETER_NAME: foo
```

And reference it in `body.md`:

```markdown
{{include:my-new-procedure}}
```

Rebuild. The rendered `dist/<workflow>/SKILL.md` has the snippet spliced
in with parameters substituted.

---

## Promote a snippet to standalone (dual-use)

Some snippets are useful as standalone skills, too: a customer who only wants to
mint an API token shouldn't have to ask Claude to "deploy a CKS cluster" to
trigger that procedure.

Open [`standalone-skills.yaml`](standalone-skills.yaml). It contains a
commented-out example block as the schema reference. Copy that block, strip the
leading `# ` characters from every line, and edit the values:

```yaml
create-api-token:
  snippet: create-api-token
  plugin: coreweave-platform-skills
  frontmatter:
    name: create-coreweave-api-token
    description: >-
      Walk the customer through creating a scoped CoreWeave Cloud
      API token. Triggers on phrases like "create an API token",
      "I need a CoreWeave token", "how do I get credentials for the
      CoreWeave API".
    allowed-tools:
      - Bash
      - Read
  params:
    TOKEN_NAME: my-coreweave-token
    TOKEN_SCOPE: read-only
    SECRET_STORE_HINT: your password manager
```

After running `python build.py`, the snippet now ships in **two** places from
the same source:

- Inlined into every workflow that requested it via `includes:`.
- As a standalone skill at `dist/create-coreweave-api-token/SKILL.md` (and
  copied into the declared plugin).

The standalone's description should be especially **"pushy"**. Standalones live
or die by router accuracy.

### Include-only: render it, but don't ship it

Omit `plugin:` and the entry becomes **include-only**. The build still writes
`dist/<name>/SKILL.md`, but copies it into no plugin, so a customer cannot
install or trigger it on its own. The content still reaches them inlined in
every workflow skill that lists the snippet in `includes:`.

Use it when a snippet is worth rendering whole — so the eval harness, which
mounts `dist/` directly, can score it as a unit — but isn't worth
distributing alone. `get-coreweave-kubeconfig` is the live example: the
kubeconfig download is browser-only, with no CLI path, so on its own the
skill can do nothing but recite a manual procedure.

Flipping a shipped entry to include-only **deletes its plugin copy** on the
next build, and `scripts/check_plugin_parity.py` fails if that deletion isn't
committed. Bump the affected plugin's `version` in
`plugins/<plugin>/.claude-plugin/plugin.json` in the same PR — `claude plugin
update` compares version strings only, so without a bump every existing
install keeps serving the withdrawn skill and reports itself up to date.

---

## Add a shared script

Use `_shared-scripts/` when **two or more workflow skills will execute the same
code**.

- Per-skill scripts → `skills/<name>/scripts/` (private, no build involvement).
- Shared scripts → `_shared-scripts/` (build copies or symlinks into every skill
  that requests them).

How the build wires them in is intentionally deferred: a future
`shared_scripts:` list in `skill.yaml` will declare which entries a given skill
needs, and the build will place them at `dist/<skill>/scripts/`. Until then,
copy by hand and flag it in your PR.

---

## Run the build locally

```bash
# One-time setup: install the build's dependencies (Jinja2,
# python-frontmatter, PyYAML) from pyproject.toml.
pip install -e .

# Rebuild after you edit skills/, _snippets/, standalone-skills.yaml,
# or _shared-scripts/.
python build.py
```

The build is idempotent and deterministic: running it twice from clean sources
produces byte-identical `dist/` output. Because `dist/` is committed, CI re-runs
the build on every PR and fails if the committed output drifts from a fresh
build (see [Evals and CI](#evals-and-ci)).

---

## Test before the repo is public

While the repo is private, the marketplace works exactly the same. Claude Code
uses your existing Git credentials. Three options, lowest-friction first:

1. **Local checkout** (recommended for active development).
   ```text
   /plugin marketplace add /absolute/path/to/this/repo
   /plugin install coreweave-cks-skills@coreweave-skills
   ```
   Pulls from your working tree. Useful for iterating on a skill and testing the
   install end-to-end without pushing.

2. **Private GitHub repo through `gh` or SSH**.
   ```text
   /plugin marketplace add coreweave/skills
   ```
   Works as long as you have `gh auth login` set up, an SSH key loaded in
   `ssh-agent`, or a Git credential helper. Interactive `/plugin` commands reuse
   those credentials.

3. **Background automatic updates on a private repo**.
   Claude Code's background marketplace refresh runs without an interactive
   prompt, so token-based auth is required. Export one before launching:
   ```bash
   export GITHUB_TOKEN=ghp_…
   ```
   Without this, manual `/plugin marketplace update` still works, but the silent
   automatic update at startup skips the refresh.

Once the repo is public, options 2 and 3 work for everyone with no auth.

---

## Pinned dependencies

**No customer-facing skill downloads "whatever is latest." Every executable
dependency a skill fetches is pinned, and changing a pin is a reviewed code
change where the reviewer reads the upstream diff — not just the changed pin
line.**

That rule exists because these skills run their downloads under the customer's
own credentials: `terraform apply` against their account, Helm charts onto their
cluster, a binary onto their `PATH`. An unpinned fetch means whatever landed
upstream this morning executes tonight, with nobody in the loop (APPSEC-3964 /
TM-005).

### What is pinned, and where

| Dependency | Pinned as | Source of truth |
| --- | --- | --- |
| `coreweave/reference-architecture` | commit SHA (`CW_REF_ARCH_SHA`) | [`_snippets/coreweave-cks.md`](_snippets/coreweave-cks.md), `fetch-pinned-ref-arch` |
| CoreWeave Helm charts (cert-manager, traefik) | `--version` flag | [`skills/cw-self-managed-inference/body.md`](skills/cw-self-managed-inference/body.md) |
| `coreweave/s5cmd` | release tag + SHA-256 checksum | [`skills/cw-load-model-to-bucket/references/s3-client-setup.md`](skills/cw-load-model-to-bucket/references/s3-client-setup.md) |
| GitHub Actions | commit SHA | workflow files, pinned by Renovate |

Each pin has exactly one editable home. The reference-architecture SHA in
particular lives in the shared snippet, not in the three skills that fetch the
repo, so bumping it is a one-line change that `python build.py` propagates to
every rendered copy. Never hand-edit a pin under `dist/` or `plugins/`.

`coreweave/reference-architecture` publishes no tags and no releases, so a
commit SHA is the only thing available to pin to. If that repo starts cutting
releases, switch the pin to a tag and simplify this.

### The update loop

1. **Upstream moves.** Renovate opens a PR proposing the new value
   ([`.github/renovate.json5`](.github/renovate.json5) has a custom regex
   manager per pin). Renovate proposes; it never merges these.
2. **Review the upstream diff.** The PR body carries a compare link. Read
   `git log <old>..<new>` on the upstream repo and ask what now executes on a
   customer's cluster that did not before. Approving the version number alone
   defeats the whole control.
3. **Rebuild.** `python build.py`, and commit the regenerated `dist/` and
   `plugins/` trees.
4. **Prove it still works.** Run the affected skill's evals, or smoke-test
   against a real cluster. A pin bump is a behavior change until tested.
5. **Release.** Pin bumps ship to installed customers only through a version
   bump, so land the change and then follow
   [`docs/RELEASING.md`](docs/RELEASING.md). An ordinary PR that moves a pin
   does *not* reach anyone who already installed the plugin.

### What CI enforces

[`scripts/check_pinned_deps.py`](scripts/check_pinned_deps.py) fails the build
when a pin is inconsistent or when the automation has rotted:

- every copy of a pin agrees, source and rendered alike;
- the reference-architecture SHA appears exactly once outside the generated
  trees;
- no pin value exists only in `dist/` or `plugins/` (the mark of a hand-edited
  rendered file);
- every Renovate custom manager still matches something — reformat a pin out
  from under its regex and Renovate stops proposing updates *silently*, which
  is the failure mode that lets a pin quietly rot for a year.

> **Not yet wired into CI.** The `check_pinned_deps.py` step is not in
> [`.github/workflows/build.yml`](.github/workflows/build.yml) yet — adding it
> needs a token with the `workflow` scope. Until then, run it locally. See the
> PR description for the exact step to paste in.

Its sibling [`scripts/lint_skill_content.py`](scripts/lint_skill_content.py)
rejects content that is *unpinned* in the first place (`git clone`, `git pull`,
branch-head tarballs, `curl | sh`, `helm install` without `--version`). Run both
before you push:

```bash
python scripts/lint_skill_content.py
python scripts/check_pinned_deps.py
```

### Owner and cadence

Pinned dependencies need a named owner — otherwise Renovate PRs accumulate
unreviewed, which is strictly worse than no automation, because it looks like
coverage. The owner reviews open pin PRs **weekly** and audits the full pin
table **quarterly**, confirming each pin still resolves and that nothing new
crept in unpinned.

> **Owner: _unassigned_.** Fill this in before the repo goes public, and add
> the same person to `CODEOWNERS` for the files in the table above.

---

## Evals and CI

Two layers, two homes:

- **Per-skill correctness evals** → `skills/<name>/evals/evals.json`. Owned by
  the skill author. Answers: "given this skill was triggered, did it produce
  the right outcome?"

- **Bundle-level trigger evals** → `evals/`. Cross-cutting. Answers: "given a
  realistic customer query, did the Skill router pick the right skill (or
  correctly pick none)?" Cases carrying `expected_chain` additionally answer
  "for a composite request, did the run consult the *whole* sequence, or fire
  the first skill and hand-roll the rest?"

See [`evals/README.md`](evals/README.md) for the bundle-level set, including the
target of 200 to 300 realistic queries and how to contribute entries when you ship
a new skill.

### The "fail PR if dist/ is stale" pattern

`dist/` is committed. This is deliberate: downstream consumers (the Skill
loader, the marketplace) never need to run Python.

The trade-off is that `dist/` can drift from sources if a contributor forgets to
rebuild. The CI workflow
([`.github/workflows/build.yml`](.github/workflows/build.yml)) makes that
impossible to merge:

1. Check out the PR.
2. Install deps from `pyproject.toml`.
3. Run `python build.py`.
4. Fail if `git diff` shows changes in `dist/` or `plugins/`, or if the build
   produced untracked files there.

Step 4 fails the PR if a fresh build produced anything that wasn't already
committed. Fix: run `python build.py` locally, commit the diff, and push again.

This is the
[same pattern Supabase uses for generated docs](https://github.com/supabase/supabase).

---

## Glossary

- **Plugin.** A directory under `plugins/` with a `.claude-plugin/plugin.json`
  manifest and a `skills/` subdir. One plugin per major product line (CKS,
  Storage, Networking, SUNK), plus `coreweave-platform-skills` for shared
  cross-cutting atomics. A customer installs a plugin and gets all of its
  skills.

- **Marketplace catalog.** The single
  [`.claude-plugin/marketplace.json`](.claude-plugin/marketplace.json) at the
  repo root. Lists every plugin the repo ships and is what `/plugin marketplace
  add` reads. Per-plugin metadata (version, description) lives in each plugin's
  own `plugin.json`. The catalog only needs `name` and `source` per entry.

- **Router skill.** A higher-level skill whose only job is to route queries to
  other skills (for example, a `coreweave-help` router that fires on broad
  questions and delegates to a specific workflow). The scaffold doesn't include
  one. They may appear later for navigation-heavy product lines.

- **Workflow skill.** The standard kind. Maps 1:1 to a how-to doc and walks the
  customer through a complete workflow end-to-end. Lives in `skills/<name>/`.

- **Atomic snippet.** A small, reusable procedure stored as a tagged region
  inside `_snippets/*.md` (for example, `create-api-token`,
  `generate-kubeconfig`, `verify-in-grafana`). Inlined into workflow skills at
  build time.

- **Dual-use.** A snippet that ships in both forms (inlined into workflows and
  as a standalone skill) from a single source. Declared in
  [`standalone-skills.yaml`](standalone-skills.yaml).

- **Tagged region.** The chunk of markdown inside a `_snippets/*.md` file
  bracketed by `<!-- snippet:NAME -->` and `<!-- /snippet:NAME -->` HTML-comment
  markers. The build's snippet indexer keys off these.

- **Progressive disclosure.** The principle that a SKILL.md should reveal detail
  in order of how often a reader needs it: trigger description first, then
  prerequisites, then steps in order, then troubleshooting last. Snippets and
  bespoke prose are interleaved in whatever order serves disclosure best.

- **"Pushy" description.** A skill `description` that aggressively lists concrete
  trigger phrases ("create an API token", "I need credentials", "how do I
  authenticate") rather than a vague summary. Pushy descriptions trigger
  reliably. Vague ones don't. The bundle-level trigger eval is the safety net
  for going *too* pushy.

- **`context: fork`.** A skill-frontmatter directive that directs the Skill
  loader to spawn a sub-conversation when the skill triggers, rather than
  mutating the user's main conversation. Useful for skills that fetch a lot of
  context the user shouldn't see.

- **`!command` preprocessing.** A directive inside a SKILL.md body that runs a
  command at load time and substitutes its output into the body before Claude
  sees it. Useful for "as-of" context (current cluster state, current quota).
  The build leaves these pass-through. The Skill loader resolves them at
  runtime, not at build time.

---

## Sources and further reading

Pull from these sources for deeper context:

1. Anthropic, [Claude skills documentation](https://docs.claude.com/en/docs/claude-code/skills).
2. Anthropic, [Claude Code plugin marketplaces documentation](https://docs.claude.com/en/docs/claude-code/plugins).
3. Anthropic engineering, [_Engineering effective AI agents with skills_](https://www.anthropic.com/engineering).
4. Supabase, [docs build and "fail PR if generated is stale" pattern](https://github.com/supabase/supabase).

CoreWeave engineers should also consult the internal architecture design doc and
the CoreWeave documentation style guide.

If you find a source missing from this list, open a PR adding it.
