# CoreWeave + W&B customer-facing Claude skills

A library of [Claude skills](https://docs.claude.com/en/docs/claude-code/skills)
that drive CoreWeave Cloud and Weights & Biases products on behalf of
customers. Every shipped skill is built from sources in this repo by a
single Python build step that inlines shared procedures, resolves
parameters, and emits fully-rendered `SKILL.md` files into `dist/` and
into the marketplace plugin trees under `plugins/`.

The design source of truth — full rationale, alternatives considered,
sources cited — lives in the
[architecture design doc](https://docs.google.com/document/d/19eN25fQov6Cp0tsXBTdpYQvmXPeq2efK8yEPrn8ZLn4/edit).
This README is the **how**; the doc is the **why**.

---

## Architecture at a glance

```
.
├── _snippets/                       Tagged regions of shared prose, inlined
│                                    into workflow skills at build time.
│   ├── coreweave-platform.md        Cross-cutting (tokens, kubeconfig, …)
│   ├── coreweave-cks.md             CKS-specific atomics
│   ├── coreweave-storage.md         Storage atomics (CAIOS, DFS, PV)
│   ├── wandb-sdk.md                 W&B SDK atomics (auth, artifacts, …)
│   └── shared-verify.md             Verification atomics (Grafana, …)
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
│   ├── coreweave-cks-skills/
│   ├── coreweave-storage-skills/
│   ├── coreweave-networking-skills/
│   ├── coreweave-sunk-skills/
│   ├── wandb-models-skills/
│   ├── wandb-weave-skills/
│   └── coreweave-platform-skills/   Shared cross-cutting atomics.
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

See the design doc for why these are kept separate.

---

## Quickstart: adding a new workflow skill

This is the most common contributor task. A new workflow skill = one
how-to document, written once, that Claude can drive for a customer.

### 1. Pick the right grain

A skill maps **1:1 to a how-to doc**:
- ✅ `deploying-cks-cluster`
- ✅ `running-a-wandb-sweep`
- ❌ `add-one-user` (too granular — make this a snippet)
- ❌ `everything-cks` (too broad — split it up)

If your idea sits between two of those bullets, lean toward the
narrower one. Bundling can come later; splitting is harder.

### 2. Create the skill directory

```bash
cp -r skills/_example-skill-template skills/<your-skill-name>
cd skills/<your-skill-name>
```

You now have two files to edit: `skill.yaml` and `body.md`. You will
**not** create anything under `dist/` by hand.

### 3. Fill in `skill.yaml`

This is the manifest. The build reads it to figure out (a) what
frontmatter to emit, (b) which plugin to ship in, and (c) which
snippets to inline.

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

plugin: <coreweave-cks-skills | wandb-models-skills | …>

includes:
  - name: create-api-token
    params:
      TOKEN_NAME: my-workflow-token
      TOKEN_SCOPE: read-only
      SECRET_STORE_HINT: your password manager
```

The `description` is the single most important field. Be **"pushy"**
(see glossary): list concrete phrases a customer would actually say,
not a vague summary. If you're unsure whether you're being too
aggressive, that's what the bundle-level trigger eval set is for —
add 3 positive and 2 negative queries (see [`evals/README.md`](evals/README.md))
and let CI confirm you're not stealing traffic from another skill.

### 4. Write `body.md`

Open `body.md` and write the bespoke prose that's unique to this
workflow. Wherever you want a shared procedure inlined, drop an
include marker on its own line:

```markdown
## Step 1 — Get an API token

{{include:create-api-token}}

## Step 2 — Do the workflow-specific thing
…
```

Every `{{include:NAME}}` you reference must also appear in `skill.yaml`
under `includes` — otherwise the build will fail with a clear error
pointing at the missing entry.

If a snippet you need doesn't exist yet, see
["Adding a shared snippet"](#adding-a-shared-snippet) below.

### 5. Run the build

```bash
python build.py
```

> **Current state (scaffold).** `build.py` is a documented skeleton
> today — every phase raises `NotImplementedError`, and running it
> produces no output. Steps 5–6 below describe the *intended*
> behavior; once the build is implemented they'll be actionable. Until
> then, treat them as a contract preview and leave `dist/` alone.

The build:
1. Parses every `skills/*/skill.yaml`.
2. Indexes every tagged region in `_snippets/*.md`.
3. Renders `body.md` by substituting `{{include:NAME}}` with the
   matching snippet, after running the snippet through Jinja2 with the
   `params` you declared.
4. Writes `dist/<your-skill-name>/SKILL.md`.
5. Copies the same file into
   `plugins/<your-plugin>/skills/<your-skill-name>/SKILL.md`.

### 6. Check the rendered output

```bash
$EDITOR dist/<your-skill-name>/SKILL.md
```

Read the rendered output end-to-end. The inlined snippets should read
naturally next to your bespoke prose — they're written as `## Heading`
blocks for exactly this reason. If a parameter looks wrong, fix the
`params:` block in `skill.yaml` and rebuild.

### 7. Add evals

- **Trigger evals** (`evals/`): add at least three positive queries
  (phrasings that should fire your skill) and two negative queries
  (phrasings that should *not* fire it). See [`evals/README.md`](evals/README.md).
- **Correctness evals** (`skills/<your-skill-name>/evals/evals.json`):
  per-skill scenarios that exercise the rendered SKILL.md end-to-end.
  The scaffold doesn't include a template — copy from an existing
  skill once one exists.

### 8. Commit and open a PR

```bash
git add skills/<your-skill-name> dist/<your-skill-name> \
        plugins/<your-plugin>/skills/<your-skill-name> evals/
git commit -m "Add <your-skill-name> workflow skill"
```

CI will rebuild from scratch and fail your PR if the committed `dist/`
doesn't match the fresh build. If that happens: run `python build.py`
locally, commit the resulting diff, push.

---

## Adding a shared snippet

Extract a procedure into `_snippets/` when **three or more workflow
skills will inline the same content**, or when the procedure is
canonical enough that drift between copies would be a correctness
bug (e.g., the official way to mint API tokens).

### Pick the right file

| File | What goes in it |
| --- | --- |
| `_snippets/coreweave-platform.md` | Cross-cutting CoreWeave platform atomics (tokens, kubeconfig, IAM). |
| `_snippets/coreweave-cks.md` | CKS-specific (clusters, node pools, operators). |
| `_snippets/coreweave-storage.md` | Storage (CAIOS, DFS, PV). |
| `_snippets/wandb-sdk.md` | W&B SDK procedures shared between Models and Weave. |
| `_snippets/shared-verify.md` | Verification procedures (Grafana, kubectl probes, run sanity checks). |

The build doesn't care which file a snippet lives in — names are
globally unique. The file split is for human navigation.

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

Conventions (a maintainer can change these; the build doesn't enforce
them):

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

Rebuild. The rendered `dist/<workflow>/SKILL.md` will have the snippet
spliced in with parameters substituted.

---

## Promoting a snippet to standalone (dual-use)

Some snippets are useful as standalone skills, too: a customer who
just wants to mint an API token shouldn't have to ask Claude to
"deploy a CKS cluster" to trigger that procedure.

Open [`standalone-skills.yaml`](standalone-skills.yaml). It contains a
commented-out example block as the schema reference. Copy that block,
strip the leading `# ` characters from every line, and edit the values:

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

After running `python build.py`, the snippet now ships in **two**
places from the same source:

1. Inlined into every workflow that requested it via `includes:`.
2. As a standalone skill at
   `dist/create-coreweave-api-token/SKILL.md` (and copied into the
   declared plugin).

The standalone's description should be especially **"pushy"** —
standalones live or die by router accuracy.

---

## Adding a shared script

Use `_shared-scripts/` when **two or more workflow skills will
execute the same code**.

- Per-skill scripts → `skills/<name>/scripts/` (private, no build
  involvement).
- Shared scripts → `_shared-scripts/` (build copies or symlinks into
  every skill that requests them).

How the build wires them in is intentionally deferred: a future
`shared_scripts:` list in `skill.yaml` will declare which entries a
given skill needs, and the build will place them at
`dist/<skill>/scripts/`. Until then, copy by hand and flag in your PR.

---

## Running the build locally

```bash
# One-time setup
pip install -e .          # installs Jinja2, python-frontmatter, PyYAML

# Every time you edit skills/, _snippets/, or standalone-skills.yaml
python build.py
```

The build is currently a **skeleton** — phases are stubbed with
`NotImplementedError` and docstrings. Implementing them is a separate
work item. Once implemented, the build will be idempotent and
deterministic: running it twice from clean sources produces byte-
identical `dist/` output.

---

## Evals and CI

Two layers, two homes:

1. **Per-skill correctness evals** → `skills/<name>/evals/evals.json`.
   Owned by the skill author. Answers: "given this skill was triggered,
   did it produce the right outcome?"

2. **Bundle-level trigger evals** → `evals/`. Cross-cutting. Answers:
   "given a realistic customer query, did the Skill router pick the
   right skill (or correctly pick none)?"

See [`evals/README.md`](evals/README.md) for the bundle-level set,
including the target of 200–300 realistic queries and how to contribute
entries when you ship a new skill.

### The "fail PR if dist/ is stale" pattern

`dist/` is committed. This is deliberate: downstream consumers (the
Skill loader, the marketplace) never need to run Python.

The trade-off is that `dist/` can drift from sources if a contributor
forgets to rebuild. The CI workflow ([`.github/workflows/build.yml`](.github/workflows/build.yml))
makes that impossible to merge:

1. Check out the PR.
2. Install deps from `pyproject.toml`.
3. Run `python build.py`.
4. `git diff --exit-code dist/ plugins/`.

Step 4 fails the PR if a fresh build produced anything that wasn't
already committed. Fix: run `python build.py` locally, commit the diff,
push again.

This is the
[same pattern Supabase uses for generated docs](https://github.com/supabase/supabase).

---

## Glossary

- **Plugin.** A directory under `plugins/` with a
  `.claude-plugin/marketplace.json`. One plugin per major product line
  (CKS, Storage, Networking, SUNK, W&B Models, W&B Weave), plus
  `coreweave-platform-skills` for shared cross-cutting atomics. A
  customer installs a plugin and gets all of its skills.

- **Router skill.** A higher-level skill whose only job is to route
  queries to other skills (e.g., a `coreweave-help` router that fires
  on broad questions and delegates to a specific workflow). The
  scaffold doesn't include one; they may appear later for
  navigation-heavy product lines.

- **Workflow skill.** The standard kind. Maps 1:1 to a how-to doc and
  walks the customer through a complete workflow end-to-end. Lives in
  `skills/<name>/`.

- **Atomic snippet.** A small, reusable procedure stored as a tagged
  region inside `_snippets/*.md` (e.g., `create-api-token`,
  `generate-kubeconfig`, `verify-in-grafana`). Inlined into workflow
  skills at build time.

- **Dual-use.** A snippet that ships in both forms — inlined into
  workflows AND as a standalone skill — from a single source.
  Declared in [`standalone-skills.yaml`](standalone-skills.yaml).

- **Tagged region.** The chunk of markdown inside a `_snippets/*.md`
  file bracketed by `<!-- snippet:NAME -->` and `<!-- /snippet:NAME -->`
  HTML-comment markers. The build's snippet indexer keys off these.

- **Progressive disclosure.** The principle that a SKILL.md should
  reveal detail in order of how often a reader needs it: trigger
  description first, then prerequisites, then steps in order, then
  troubleshooting last. Snippets and bespoke prose are interleaved in
  whatever order serves disclosure best.

- **"Pushy" description.** A skill `description` that aggressively
  lists concrete trigger phrases ("create an API token", "I need
  credentials", "how do I authenticate") rather than a vague summary.
  Pushy descriptions trigger reliably; vague ones don't. The
  bundle-level trigger eval is the safety net for going *too* pushy.

- **`context: fork`.** A skill-frontmatter directive (see the design
  doc for full semantics) that tells the Skill loader to spawn a sub-
  conversation when the skill triggers, rather than mutating the
  user's main conversation. Useful for skills that fetch a lot of
  context the user shouldn't see. Not used by anything in the
  scaffold.

- **`!command` preprocessing.** A directive inside a SKILL.md body
  that runs a command at load time and substitutes its output into the
  body before Claude sees it. Useful for "as-of" context (current
  cluster state, current quota). The build leaves these pass-through —
  they're resolved by the Skill loader at runtime, not at build time.

---

## Sources and further reading

- **Primary**: [architecture design doc](https://docs.google.com/document/d/19eN25fQov6Cp0tsXBTdpYQvmXPeq2efK8yEPrn8ZLn4/edit)
  — full rationale for every decision summarized in this README.

The design doc cites these eight sources; pull from them for deeper
context:

1. Anthropic, [Claude skills documentation](https://docs.claude.com/en/docs/claude-code/skills).
2. Anthropic, [Claude Code plugin marketplaces documentation](https://docs.claude.com/en/docs/claude-code/plugins).
3. Anthropic engineering, [_Engineering effective AI agents with skills_](https://www.anthropic.com/engineering).
4. Supabase, [docs build & "fail PR if generated is stale" pattern](https://github.com/supabase/supabase).
5. Internal CoreWeave docs IA & style guide (Confluence — see DevX space).
6. W&B docs site (`docs.wandb.ai`) and SDK reference.
7. CoreWeave Grafana dashboards inventory (Confluence — DevX space).
8. CoreWeave support transcripts corpus (Glean — used to seed the
   bundle-level trigger eval set).

If you find a source missing from this list, open a PR adding it —
the design doc and this README should stay in sync.
