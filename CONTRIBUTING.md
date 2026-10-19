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
│                                    Copied into skills at build via
│                                    shared_scripts.
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
│                                    correctness mirrors live next to each
│                                    workflow; standalone mirrors live here.
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

This is the most common contributor task. A workflow skill is one how-to
document, written once, that Claude drives for a customer against their live
CoreWeave account — so treat every command you write as something an agent
will really run.

### 1. Pick the grain

One skill, one how-to doc.

- ✓ `cw-create-cluster`
- ✓ `cw-provision-sunk-cluster`
- ✗ `cw-add-user` — too granular; make it a snippet
- ✗ `cw-everything-cks` — too broad; split it

Sitting between two of those? Take the narrower one. Bundling later is easy;
splitting later is not.

### 2. Copy the template

```bash
cp -r skills/_example-skill-template skills/<your-skill-name>
```

**Every skill name starts with `cw-`.** The directory name, the
`frontmatter.name` in `skill.yaml`, and the name a customer types after
`/<plugin>:` are all the same string, and the prefix is what makes a CoreWeave
skill recognisable in a session that has other plugins loaded. Use
`cw-<verb>-<object>`: `cw-create-cluster`, `cw-load-model-to-bucket`,
`cw-verify-workload-health`. Renaming a shipped skill later is a breaking
change for anyone who invokes it by name, so get this right before the first
release. The same rule applies to standalone skills declared in
`standalone-skills.yaml` (see
[Promote a snippet to standalone](#promote-a-snippet-to-standalone-dual-use)).

Edit two files: `skill.yaml` and `body.md`. Never hand-edit `dist/` or
`plugins/<plugin>/skills/` — the build owns those trees. Plugin manifests under
`plugins/<plugin>/.claude-plugin/` are maintained separately; see
[`docs/RELEASING.md`](docs/RELEASING.md).

### 3. Fill in `skill.yaml`

The manifest. The build reads it for the frontmatter to emit, the plugin to
ship in, and the snippets to inline.

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
  disallowed-tools:
    - WebFetch
    - WebSearch

plugin: <coreweave-cks-skills | coreweave-storage-skills | …>

includes:
  - name: create-api-token
    params:
      TOKEN_NAME: my-workflow-token
      TOKEN_SCOPE: read-only
      TOKEN_ROLES: >-
        **CKS Viewer** (read-only: list and view clusters and VPC
        resources) and **Access Token User** (mint this token)
      TOKEN_ROLES_NOTE: ""
      TOKEN_EXPIRY: 8 hours
      TOKEN_403_NOTE: ""
      SECRET_STORE_HINT: your password manager
```

Do:

- Write a **pushy** `description` (see [Glossary](#glossary)) — concrete
  phrases a customer would actually say. The description helps the agent
  select a skill; routing also depends on the request, context, and available
  skills. Test it with the trigger evals.
- Declare `disallowed-tools:` with the tools your workflow must never reach.
  This is the key that actually narrows a skill: the loader removes those
  tools from the model's pool while the skill is active in Claude Code. Other clients
  may handle this field differently; verify the target client before relying
  on it as a restriction.
- Name a `plugin:`. Every hand-authored skill ships somewhere.

Don't:

- **Treat `allowed-tools:` as a restriction.** The loader reads it as a
  permission *pre-approval*: listed tools skip the customer's prompt, and
  unlisted tools stay callable. The build deliberately drops it from the
  emitted `SKILL.md` — shipping `allowed-tools: [Bash, Read, Write]` would
  auto-approve arbitrary shell execution for a workflow that runs
  `terraform apply` and mints API tokens, removing the confirmation the
  Checkpoint steps depend on. Keep it in `skill.yaml` as a record of what
  your workflow legitimately needs.
- Declare neither `disallowed-tools:` nor a waiver. That fails the build, so a
  dropped or misspelled key can't quietly ship an unrestricted skill. To waive
  it, use the **top-level** key `disallowed-tools-waived: "<reason>"` — the
  reason is mandatory, setting both keys fails, and the waiver is never
  emitted.
- Waive for tools you can't enumerate statically. A deny-list needs no escape
  hatch: just don't name them. `cw-create-cluster` drives the Console with
  environment-provided browser tools and needs no waiver.

**Tool scoping is not token scoping.** If your workflow needs a CoreWeave API
access token, read the next section too — that's a different credential with
its own rules.

### 3b. Declare what the API token needs

Skip this if your workflow doesn't include `create-api-token`.

A CoreWeave API access token **cannot be scoped to this workflow**. The [token
documentation](https://docs.coreweave.com/security/authn-authz/manage-api-access-tokens)
lists Name, Expiration, and Note in the creation dialog (the Console observation
recorded in our token policy calls the last field Comment). The resulting token
carries every permission its creating user holds, org-wide, until it expires. So
never write content telling a customer to "choose a scope": there is nothing to
choose. See [`docs/token-scope-policy.md`](docs/token-scope-policy.md).

What you declare instead, in the `create-api-token` include's `params:`

| Param | Rendered? | What it's for |
| --- | --- | --- |
| `TOKEN_ROLES` | Yes | The minimal IAM roles the workflow needs, as Markdown. This is the honest substitute for scope: a token inherits its creating user's roles, so naming the minimum lets a customer mint it as a least-privilege user instead of an admin. Use real role names from [IAM roles](https://docs.coreweave.com/security/iam/access-policies/roles). |
| `TOKEN_ROLES_NOTE` | Yes | Any caveat about those authorizations, rendered as its own sentence(s) right after `TOKEN_ROLES`. Use `""` when there is none — the param is **required**, because the snippet tests it and the Jinja env uses `StrictUndefined`. Keep `TOKEN_ROLES` a bare noun phrase and put explanatory prose here; the snippet closes the sentence itself, so a value that trails off mid-clause can't break the surrounding text. |
| `TOKEN_EXPIRY` | Yes | Recommended expiration. Build-enforced: it must be one of *1 hour*, *8 hours*, *One month*, *90 days*, *One year*, and it must **not** be *Never*. Use `8 hours` unless the workflow genuinely needs longer — the policy records *One month* as the default, observed in the Console on 2026-08-26. |
| `TOKEN_403_NOTE` | Yes | Workflow-specific troubleshooting appended to the shared `403` note, as its own sentence(s). Use `""` when there is none; also **required**. Consumer-specific facts belong here rather than in the shared block — the CKS `403`/`401` semantics and **Observability Viewer** are wrong for object storage, which is why they are not in the block itself. |
| `TOKEN_SCOPE` | **No** | Lint-only: `read-only` or `read-write`. Not rendered, because the customer can't act on it. |

Any workflow whose `TOKEN_SCOPE` is `read-write` must record why, in the
**top-level** manifest key `token-scope-justification: "<reason>"` — same
audit-trail shape as `disallowed-tools-waived`: mandatory non-empty reason,
rejected inside `frontmatter:`, never emitted, and a build error if it's
missing (or if it lingers after the scope narrows back to `read-only`). Say
what the workflow actually writes and what narrowing you *did* apply.

One trap worth knowing, since the repo already fell into it: a param no
snippet references is silently dropped by Jinja2 and fails nothing at runtime.
`TOKEN_SCOPE` sat in five manifests that way, so no customer ever saw the
recommendation it implied. The build now rejects unreferenced params — if a
param is genuinely build-only, add it to `SOURCE_ONLY_INCLUDE_PARAMS` in
`build.py` rather than leaving it to rot.

### 4. Write `body.md`

Write the prose unique to your workflow. To inline a shared procedure, put an
include marker on its own line:

```markdown
## Step 1 — Get an API token

{{include:create-api-token}}

## Step 2 — Do the workflow-specific thing
…
```

Every `{{include:NAME}}` must also appear under `includes:` in `skill.yaml`,
or the build fails pointing at the missing entry. If the snippet you need
doesn't exist yet, see
["Add a shared snippet"](#add-a-shared-snippet).

#### Gate destructive commands with a Checkpoint

Put a human-confirmation gate before every destructive command you write. The
gate is a blockquote line opening with this literal marker:

```markdown
> **Checkpoint:** Show the customer <the thing> and get confirmation before proceeding.
```

Do:

- Write the marker exactly — `> **Checkpoint:**`, that case, colon inside the
  bold, opening the line. The build rejects near-misses, so the marker can't
  drift into inert prose.
- Reword everything after the marker however the step needs.
- Gate the command in the step it appears in. A gate reaches its own section
  and the next one, so one Checkpoint per step is the shape that works — a
  Checkpoint on page one will not cover an appendix.
- Keep the gate in the same file as the command. If the command lives in a
  snippet, the Checkpoint belongs in the snippet too, so it travels with the
  command into every skill that inlines it.
- Write the word "checkpoint" unemphasized in ordinary prose. An emphasized
  one fails the build as a drifted marker.

Don't:

- Rely on `-auto-approve`, or on a tool's own prompt. The Checkpoint replaces
  it, and several bodies deliberately pair the two.
- Take a green build as proof your commands are gated. `build.py` enforces
  five command classes — `terraform apply`, `terraform destroy`,
  `helm install`, `helm upgrade`, `aws s3api create-bucket` — and nothing
  else. `kubectl apply`, `kubectl delete`, `rm`, and anything reached through
  a script or a variable are on you and your reviewer.
- Add yourself to `CHECKPOINT_BASELINE` in `build.py` to get a green build.
  That list grandfathers a handful of ungated commands that predate the
  check, and it only ever shrinks — new entries are not accepted. Add the
  Checkpoint instead. (If the build tells you an existing entry is stale,
  that is the ratchet working: the command it named got gated or removed, so
  delete the entry.)

### 5. Build

```bash
python build.py
```

Install the dependencies once first — see [Run the build
locally](#run-the-build-locally). The build parses non-underscore-prefixed
`skills/*/skill.yaml`, indexes the tagged regions in `_snippets/*.md`, renders
your `body.md` with each `{{include:NAME}}` substituted (Jinja2 first evaluates
the `params:` you declared), writes `dist/<your-skill-name>/SKILL.md`, mirrors
it into your plugin, and validates the result against the Checkpoint contract.

### 6. Read the rendered output

```bash
$EDITOR dist/<your-skill-name>/SKILL.md
```

Read it end-to-end, the way the agent will. Inlined snippets should sit
naturally next to your prose — they're written as `## Heading` blocks for
exactly that reason. If a parameter reads wrong, fix `params:` in `skill.yaml`
and rebuild.

### 7. Add evals

Every shipped skill needs both kinds, and they live in different repos:

- **Trigger evals** (`evals/`) — at least three positive queries (phrasings
  that should fire your skill) and two negative ones (phrasings that should
  not). PR CI validates the corpus; the model-based router sweep runs on
  pushes to `main` and manual dispatch, not on PRs. See
  [`evals/README.md`](evals/README.md).
- **Correctness evals** — in
  **[coreweave/skills-evals](https://github.com/coreweave/skills-evals)**. That repo
  owns the scenarios that exercise your rendered `SKILL.md` end-to-end, and
  it is where you author or change one. It is a separate internal repo; if
  you can't reach it, ask the skills team.

  `skills/<your-skill-name>/evals/evals.json` in this repo is a **generated
  mirror** of that repo's answer keys — a manifest of which scenarios cover
  your skill, so the coverage is visible next to the skill source. Don't
  hand-write it:

  ```bash
  python3 evals/sync_skill_evals.py --write --harness /path/to/skills-evals
  ```

  To check for drift without writing, omit `--write` and keep `--harness`. With
  no arguments, the script looks at `SKILLS_EVALS_REPO`, then a sibling
  `skills-evals` checkout. It reads committed files at `origin/master` by
  default; fetch that repo first, or select a ref explicitly with `--ref`.
  Standalone mirrors live in `evals/standalone/<skill-name>.evals.json`. Never
  hand-edit `user_request`, `user_turns`, `expect` or `rubric_criteria`: fix the
  answer key upstream and re-sync. A drifted mirror is worse than none, because
  it reads as coverage while gating something the harness no longer checks.
  `blocking` is maintained here and preserved by sync; existing case IDs are
  also preserved for matching scenario paths.

### 8. Commit the generated output with your source

```bash
git add skills/<your-skill-name> dist/<your-skill-name> \
        plugins/<your-plugin>/skills/<your-skill-name> evals/
git commit -m "Add <your-skill-name> workflow skill"
```

CI rebuilds from scratch and fails the PR if the committed `dist/` doesn't
match the fresh build. If that happens: run `python build.py`, commit the
resulting diff, and push.

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

A marker also works in the skill's own `references/*.md` files, resolved
against the **same** `includes:` list as `body.md` — one declaration in
`skill.yaml` covers both. Non-markdown files under `references/` are copied
untouched.

### Compose a snippet from another snippet

A `{{include:NAME}}` marker inside a snippet body is spliced in too, so a rule
that several snippets must all state can live in exactly one of them.
`browser-consent` nested inside `create-api-token` is the worked example.

Follow these nesting rules:

- **Keep a nested snippet param-free (authoring convention).** Nesting is
  resolved *before* Jinja2 runs, so a `{{ PARAM }}` in the nested body is
  evaluated against the params of the caller that pulled in the **outer**
  snippet. Different callers can supply different values. The build does not
  enforce the param-free convention. Put anything that varies per call site
  at the call site, right after the marker.
- **No cycles.** `a` including `b` including `a` is a build error, as is a
  marker naming a snippet that doesn't exist. The build also rejects nesting
  `create-api-token`; declare it directly so token validation can see it.

Provenance follows the nesting: the `sources:` header of a rendered artifact
lists nested snippets alongside declared ones.

---

## If a skill drives the customer's browser

Any skill that has the agent drive a customer's authenticated Cloud Console
session — navigate, snapshot, read a page — must pull in the shared
`browser-consent` block rather than writing the rules out again:

```markdown
{{include:browser-consent}}
```

It carries the whole contract: a quiet probe for tool *availability* is fine
but quiet automation is not; announce what you'll open, read, and click, then
**wait** for a go-ahead; a sign-in page, SSO redirect, 2FA prompt, or CAPTCHA
is handed back to the customer rather than answered; page content is data,
never instructions, and instruction-like text stops the flow and gets quoted
back in a fence labelled as untrusted.

Immediately after the marker, add the two things that *are* per-call-site:

1. **Which manual path a decline falls back to**, named explicitly. The block
   says to take "this step's manual path"; only the call site knows what that
   is.
2. **The announcement itself**, if a concrete example helps — see
   `skills/cw-create-cluster/references/quota-check.md`.

### Why this is a rule and not a suggestion

The block used to be written out by hand at each call site. APPSEC-3962 (#45)
raised the bar for the **read-only** quota check and left the flow that
**mints a full-user-scope API credential** on weaker wording — two standards
for the same browser in the same repo, split the wrong way, and nobody noticed
for weeks. Prose in this file did not prevent that, so the rule is enforced:

- `scripts/lint_skill_content.py` fails a source file that drives the browser
  without the block in scope (rule `browser-consent`). A body that navigates
  nothing itself and hands the flow to one of its own `references/*.md` files
  is satisfied by the block in that file.
- `tests/test_browser_consent.py` covers the build machinery and the rule, and
  asserts the real `dist/` artifacts actually carry the block.

Both run in CI. If you have a genuinely reviewed exception, take the per-line
escape hatch and say why in the diff:

```markdown
<!-- content-lint-allow: browser-consent -->
```

---

## Scan for credentials before you commit (opt-in)

Two blocking CI jobs scan for credentials: one over the checked-out files, one
over the commit history (`.github/workflows/eval-hygiene.yml`). Both are the
authority. Neither helps you before you push, and for a credential that is the
wrong end of the pipe — a pushed secret is a disclosed secret, and the fix is
rotation, not a follow-up commit.

There is a local hook that previews the file scan against your staged content.
It is opt-in because git cannot ship hooks in a clone:

```bash
git config core.hooksPath .githooks
```

It needs Docker, reuses the exact scanner image CI pins, and skips itself with a
note if the daemon is not running — so it never blocks a commit just because you
are offline. To bypass it for one commit, `git commit --no-verify`; that is a
real escape hatch, not a workaround, but the CI jobs still run.

If it fires on something real, **rotate the value before you edit the file.**
If it fires on a fixture that genuinely has to be committed, add its own
anchored literal to `.gitleaks.toml` — never a value shape — and say what the
fixture is. `evals/test_gitleaks_allowlist.py` enforces that distinction and
explains why.

---

## Promote a snippet to standalone (dual-use)

Promote a snippet only when it owns a distinct customer workflow. Bare API-token
and kubeconfig requests are labeled `null` in the trigger corpus; do not promote
them without revisiting the routing policy in
[`evals/README.md`](evals/README.md).

[`standalone-skills.yaml`](standalone-skills.yaml) documents the schema and
contains live entries. The following is a schema example, not an instruction to
ship a standalone API-token skill:

```yaml
create-api-token:
  snippet: create-api-token
  plugin: coreweave-platform-skills
  frontmatter:
    name: cw-create-api-token
    description: >-
      Walk the customer through creating a CoreWeave Cloud API
      token. Triggers on phrases like "create an API token",
      "I need a CoreWeave token", "how do I get credentials for the
      CoreWeave API".
    allowed-tools:
      - Bash
      - Read
    disallowed-tools:
      - WebFetch
      - WebSearch
  params:
    TOKEN_NAME: my-coreweave-token
    TOKEN_SCOPE: read-only
    TOKEN_ROLES: >-
      **CKS Viewer** (read-only: list and view clusters and VPC
      resources) and **Access Token User** (mint this token)
    TOKEN_ROLES_NOTE: ""
    TOKEN_EXPIRY: 8 hours
    TOKEN_403_NOTE: ""
    SECRET_STORE_HINT: your password manager
```

If this entry is added, running `python build.py` ships the snippet in **two**
places from the same source:

- Inlined into every workflow that requested it via `includes:`.
- As a standalone skill at `dist/cw-create-api-token/SKILL.md` (and
  copied into the declared plugin).

`frontmatter.name` follows the same rule as a workflow skill: it starts with
`cw-`. That includes include-only entries, which no plugin ships but which the
evals harness still triggers by name.

The standalone's description should be especially **"pushy"**. Standalones live
or die by router accuracy.

Tool scoping works the same here as in a workflow `skill.yaml`:
`allowed-tools` is source-only (it pre-approves rather than restricts, so it
is never emitted), and `disallowed-tools` under `frontmatter:` is what the
Skill loader enforces. Each entry must declare a non-empty
`disallowed-tools:` list or waive it with a top-level
`disallowed-tools-waived: "<reason>"` (non-empty reason required; never
emitted).

### Include-only: render it, but don't ship it

Omit `plugin:` and the entry becomes **include-only**. The build still writes
`dist/<name>/SKILL.md`, but copies it into no plugin, so a customer cannot
install or trigger it on its own. The content still reaches them inlined in
every workflow skill that lists the snippet in `includes:`.

Use it when a snippet is worth rendering whole — so the eval harness, which
mounts `dist/` directly, can score it as a unit — but isn't worth distributing
alone. `cw-get-kubeconfig` is the live example: the current snippet gets the
kubeconfig through the Console and falls back to manual download when browser
tools are unavailable.

Flipping a shipped entry to include-only **deletes its plugin copy** on the next
build, and CI's generated-output check fails if that deletion isn't committed.
`scripts/check_plugin_parity.py` checks the resulting packaging. Bump the
affected plugin's `version` in `plugins/<plugin>/.claude-plugin/plugin.json` in
the same PR — `claude plugin update` compares version strings only, so without a
bump every existing install keeps serving the withdrawn skill and reports itself
up to date.

---

## Add a shared script

Use `_shared-scripts/` when **two or more workflow skills will execute the same
code**.

Declare shared files or directories with paths relative to `_shared-scripts/`:

```yaml
shared_scripts:
  - my-helper.py
```

The build copies each entry to `dist/<skill>/scripts/<entry-basename>` and
mirrors it into the plugin. It rejects missing paths and paths that escape
`_shared-scripts/`; it does not create symlinks. Choose distinct basenames.

A skill's own `skills/<name>/scripts/` directory is not copied by the build. Use
`shared_scripts:` for packaged helpers, or keep a skill-specific helper under
`references/`, which the build copies, and refer to that path in the skill.

---

## Run the build locally

```bash
# Requires Python 3.11 or newer.
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

## Test the install locally

Point the marketplace at your working tree, so you can iterate on a skill and
still exercise the real install path:

```text
/plugin marketplace add /absolute/path/to/this/repo
/plugin install coreweave-cks-skills@coreweave-skills
```

This loads from your checkout, including uncommitted changes. Start a new
session or run `/reload-plugins` after editing to load the changes. Run `python
build.py` first: the marketplace serves `plugins/`, not `skills/`.

For the normal install from the published marketplace, see the
[README](README.md#install-the-skills).

---

## Pinned dependencies

**Changes to the pins below require review of the upstream diff.** These pins
cover the reference architecture, the listed Helm charts, s5cmd, and CI tools.
They do not cover every dependency: the storage setup still recommends an
unpinned Hugging Face CLI installation and the latest cwic release. Review such
installation steps explicitly; a passing pin check does not validate them.

That rule exists because these skills run their downloads under the customer's
own credentials: `terraform apply` against their account, Helm charts onto their
cluster, a binary onto their `PATH`. An unpinned fetch means whatever landed
upstream this morning executes tonight, with nobody in the loop.

### What is pinned, and where

| Dependency | Pinned as | Source of truth |
| --- | --- | --- |
| `coreweave/reference-architecture` | commit SHA (`CW_REF_ARCH_SHA`) | [`_snippets/coreweave-cks.md`](_snippets/coreweave-cks.md), `fetch-pinned-ref-arch` |
| CoreWeave Helm charts (cert-manager, traefik) | `--version` flag | [`skills/cw-self-managed-inference/body.md`](skills/cw-self-managed-inference/body.md) |
| `coreweave/s5cmd` | release tag + SHA-256 checksum | [`skills/cw-load-model-to-bucket/references/s3-client-setup.md`](skills/cw-load-model-to-bucket/references/s3-client-setup.md) |
| GitHub Actions | commit SHA | workflow files, pinned by Renovate |
| `gitleaks` scanner image | tag + image digest | [`.github/workflows/eval-hygiene.yml`](.github/workflows/eval-hygiene.yml) |

GitHub Actions and the gitleaks image run in CI. The gitleaks image needs a
custom Renovate manager because the pin lives inside a `run:` string. It has a
custom manager plus a packageRule that re-enables it against the blanket "no
container updates" rule; that packageRule has to stay **last** in the list,
because Renovate resolves later matches over earlier ones.

Edit pins in their source files; the gitleaks pin occurs in both scanner jobs in
the same workflow and must be updated in both. The reference-architecture SHA in
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

### Reviewing a Renovate pin PR

Renovate proposes the pin change. For eligible same-repo Renovate PRs,
[renovate-build.yml](.github/workflows/renovate-build.yml) rebuilds and commits
`dist/` and `plugins/` automatically using the configured GitHub App. If that
job fails or has not run, generated-output and pin-consistency checks can remain
red; inspect the rebuild job before rebuilding manually. CI-only pin changes do
not necessarily change rendered skills.

**What to actually review.** The new version number tells you nothing on its
own. Open the compare link in the PR body, read the upstream diff, and answer:

- Does anything new run during `terraform apply` — a new provider, a new
  `local-exec`, a changed module source?
- Did a chart change what it deploys, its RBAC, or its default image tag?
- Did anything start reading credentials, or writing outside the working
  directory?
- For `s5cmd`: is the release still built from the CoreWeave fork, and does
  `s5cmd_checksums.txt` cover the asset the skill downloads?

If the diff does not let you answer those, the PR is not ready to approve. An
approval on the version number alone is this control failing quietly, which is
the failure mode the whole pin exists to prevent.

**Manual rebuild fallback.** If automation has not committed the output:

```bash
gh pr checkout <number>
python build.py
git commit -am "Rebuild the rendered trees for the new pin"
git push
```

Then run the affected skill's evals, or smoke-test it against a real cluster.
The rebuild is step 3 of the update loop above — the version bump in step 5 is
what actually delivers it to anyone.

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

**Owner: @coreweave/docs.**
Pin PRs are that team's to review, and "review" means reading the upstream diff
— a pin bump approved on the version number alone is the control failing
quietly.

[`.github/CODEOWNERS`](.github/CODEOWNERS) assigns these pin sources, workflows,
and Renovate configuration to @coreweave/docs. Release procedures are in
[`docs/RELEASING.md`](docs/RELEASING.md).

---

## Evals and CI

Two layers, two homes:

- **Per-skill correctness evals** →
  [coreweave/skills-evals](https://github.com/coreweave/skills-evals), with
  generated mirrors in `skills/<name>/evals/evals.json` and `evals/standalone/`.
  Answers: "given this skill was triggered, did it produce the right outcome?"

- **Bundle-level trigger evals** → `evals/`. Cross-cutting. Answers: "given a
  realistic customer query, did the Skill router pick the right skill (or
  correctly pick none)?" Cases carrying `expected_chain` additionally answer
  "for a composite request, did the run consult the *whole* sequence, or fire
  the first skill and hand-roll the rest?"

See [`evals/README.md`](evals/README.md) for the bundle-level set, including the
target of 200 to 300 realistic queries and how to contribute entries when you ship
a new skill.

### Correctness status is required for skill changes

[correctness-required.yml](.github/workflows/correctness-required.yml) runs
`require-correctness` using the gate script from the PR's base commit. Relevant
skill changes need a successful `skills-evals/correctness` status on the exact
PR head SHA from a login in `CORRECTNESS_STATUS_CREATORS`. Have a maintainer run
the `correctness-gate` workflow in coreweave/skills-evals with `skills_sha`
set to that SHA and optional `pr_number`, then re-run `require-correctness`.
A new commit needs a new result. The local mirror validator checks schema, not behavior or upstream
drift. Documentation-only changes outside the gate's relevant paths pass without
a correctness run; see
[`scripts/require_correctness_status.py`](scripts/require_correctness_status.py).

### Corpus hygiene is a blocking gate, not a review habit

Both corpora ship in a repo customers can read, so `evals/README.md`'s "sanitize
before committing" rule is enforced, not advised.
[`.github/workflows/eval-hygiene.yml`](.github/workflows/eval-hygiene.yml) runs
three jobs on every PR: the repository scanner, gitleaks on checked-out files,
and gitleaks on commit history. A separate workflow checks PR text:

- [`evals/check_eval_hygiene.py`](evals/check_eval_hygiene.py) — the
  repo-specific scanner. Emails, internal handles, API-key and token shapes,
  JWTs, PEM headers, IPs, ticket IDs, UUIDs, and tenant-bearing console URLs —
  over repository files selected by the scanner, subject to its exclusions and
  allowlist. It scans file *names* as well as contents, and re-scans decoded
  JSON so `\uXXXX` escaping can't hide a match. Findings are reported as
  described below and are redacted — the gate never echoes the value it caught.

  **Most findings warn rather than block.** Credential shapes and
  scan/configuration errors fail your PR; an email, IP or ticket ID has
  legitimate look-alikes, and a gate that stops a merge over a documentation IP
  is one people switch off.

  On same-repo PRs, findings on commentable diff lines become review threads,
  up to the posting limit. Off-diff, filename, overflow, and failed-inline
  findings appear in a summary comment. Required conversation resolution
  blocks unresolved review threads, not every summary finding. Review both
  and resolve false positives. Fork PRs do not receive these comments.
  `--strict` makes all findings blocking when running the scanner locally.
- **Paste-residue rules** in the same scanner. Non-breaking and zero-width
  spaces, curly quotes, Slack mention markup, mail quote headers — evidence
  that text arrived by *copy-paste* rather than by authoring, which is when
  sanitization gets skipped. Provenance itself is undetectable (a sanitized
  quote and a synthetic query are the same artifact); a careless paste is not.
  Retype the character in ASCII and move on.
- **gitleaks**, pinned by image digest, as an independent second opinion.
- **`pr-text-hygiene.yml`**, which runs the *identifier* rules over the PR
  description, every comment, and every review. A PR body is gated — edit it
  and the check clears. A comment is an **alarm only**, reported as a warning
  that does not fail the job: it was public the moment you posted it, so a hit
  there is a disclosure to handle, not a typo to edit. The paste-residue rules
  are skipped on PR text; a curly apostrophe in a sentence is an apostrophe.

Run both the scanner and its self-test before you push:

```bash
python scripts/check_eval_hygiene_selftest.py
python evals/check_eval_hygiene.py
```

The self-test comes first on purpose: this gate's failure mode is silence, so a
rule that quietly stopped matching would report a leaking corpus clean.
[`evals/HYGIENE.md`](evals/HYGIENE.md) is the operator guide — how to fix a hit
(rotate a real credential, never just edit the string), how to extend the
allowlist, and the residual gaps stated plainly.

**A corpus PR needs code-owner approval from someone other than its author.**
`evals/trigger-evals.jsonl` and `skills/*/evals/` are owned by **@coreweave/docs
and @coreweave/solutions-architecture** — approval from *either* satisfies it.
This is the half of the control the scanner structurally cannot do. Reviewing a
corpus entry means looking for what has no shape to match:

- a customer or org name sitting in ordinary prose;
- a *fingerprint* rather than an identifier — the unusual GPU mix, the
  one-of-a-kind deploy pattern, the detail that identifies an account without
  naming it (`evals/README.md` calls these out explicitly);
- a "paraphrase" still close enough to the original to search back to the
  thread it came from.

**If an entry came from a transcript, say so and get a second reviewer.**
Nothing can detect this for you: a well-sanitized transcript quote and a
well-written synthetic query are the same artifact by construction, so no CI
check can tell them apart — and none tries. What CI *can* catch is a careless
paste (see the paste-residue rules above), which is a different thing.

So this part is a convention, not a gate:

- Say in the PR description which added entries are transcript-derived, and
  what you sanitized. Never link or name the source thread.
- Ask for a reviewer who would recognize the account — that is what
  `@coreweave/solutions-architecture` is on those paths for. If a transcript
  entry is in your PR, say so explicitly in your review request rather than
  letting a docs approval clear it by default.
- If sanitizing costs you the phrasing pattern you were trying to capture,
  write a synthetic equivalent instead and skip all of the above.

Prefer synthetic queries, as `evals/README.md` says. The eval cares that the
phrasing *distribution* matches reality, never that a particular sentence was
really said — so the safest entry is one that was never anyone's words.

Three things are worth knowing before you touch it. The scanner is
**pattern-only**: every rule matches a shape, and it deliberately holds no list
of customer or org names — that history, and why a hashed list is the wrong
answer in a public repo, is in HYGIENE.md under "Why there is no customer-name
list". Catching a customer *name* in otherwise-clean prose is a reviewer's job,
not this gate's. The allowlist is **data the gate reads**, so a PR that adds a
leak could suppress its own finding by appending one regex; it and the scanner
are therefore in [`CODEOWNERS`](.github/CODEOWNERS). And a red run on the
push-to-`main` backstop is a disclosure, not a flake — the content is already
public by then, so treat it as one.

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

- **"Pushy" description.** A skill `description` that aggressively lists
  concrete trigger phrases ("create an API token", "I need credentials", "how do
  I authenticate") rather than a vague summary. Specific descriptions make the
  intended routing clearer; validate reliability with evals. The bundle-level
  trigger eval is the safety net for going *too* pushy.

- **`context: fork`.** A skill-frontmatter directive that directs the Skill
  loader to spawn a sub-conversation when the skill triggers, rather than
  mutating the user's main conversation. Useful for skills that fetch a lot of
  context the user shouldn't see.

- **Dynamic command preprocessing.** Claude Code supports shell commands
  embedded with exclamation-mark/backtick syntax and substitutes their output
  before the model reads the skill. **Banned here** by `bang-directive` in
  `scripts/lint_skill_content.py`. Claude Code checks permissions before
  executing injected commands; preprocessing is not a general permission bypass.
  It still runs before the model can follow a Checkpoint in the body, so write
  commands as ordinary workflow instructions instead. See the [Claude Code
  permission
  behavior](https://code.claude.com/docs/en/skills#permission-checks-on-injected-commands).

---

## Sources and further reading

Pull from these sources for deeper context:

1. Anthropic, [Claude skills
documentation](https://code.claude.com/docs/en/skills). 2. Anthropic, [Claude
Code plugin marketplaces
documentation](https://code.claude.com/docs/en/plugin-marketplaces). 3.
Anthropic engineering, [_Equipping agents for the real world with Agent
Skills_](https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills).

CoreWeave engineers should also consult the internal architecture design doc and
the CoreWeave documentation style guide.

If you find a source missing from this list, open a PR adding it.
