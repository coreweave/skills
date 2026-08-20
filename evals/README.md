# Evals

Two layers of evaluation live in this repo. They answer different
questions and have different homes.

## 1. Per-skill correctness evals — `skills/<name>/evals/evals.json`

These are owned by the skill author and live next to the skill source.
They answer: **given that this skill was triggered, did it produce the
correct outcome end-to-end?**

A skill's `evals.json` is a list of scenario records — input state,
expected tool calls, and acceptance criteria for the rendered output.
They are run by the per-skill harness (separate work item) and gate
that skill alone. Failures block only the PR that touches that skill.

The scaffold does **not** include an example `evals.json`. The first
real workflow skill should set the template.

## 2. Bundle-level trigger evals — `evals/` (this directory)

This directory holds the **trigger eval set** for the entire library.
It answers a different question: **given a realistic customer query,
did the Skill router pick the right skill (or correctly pick none)?**

Trigger evals catch the failure mode that per-skill evals can't:
- A new skill that hijacks queries meant for an existing skill.
- A skill whose description is too narrow and never fires.
- Two skills with overlapping descriptions that confuse the router.

Target size: **200–300 realistic customer queries**, drawn from real
support transcripts, Slack DMs, and Glean searches. Each entry pairs a
natural-language query with the skill that *should* fire (or with
`null` if the correct answer is "no skill applies, just chat").

> **Sanitize before committing.** This directory is committed to a
> repo that ships to customers — anything you put here is effectively
> public. Before you paste a query from a customer transcript, Slack
> thread, or internal channel:
>
> - Remove customer/org names, account IDs, cluster names, project
>   names, ticket IDs, and any other identifiers. Replace with generic
>   placeholders ("my org", "the staging cluster").
> - Remove email addresses, names of internal employees, and any URL
>   that includes a tenant or account identifier.
> - Remove any technical detail that would let a reader infer who the
>   customer was (an unusual GPU mix, a one-of-a-kind deploy pattern).
> - When in doubt, paraphrase rather than quote.
>
> If a query can't be sanitized without losing the phrasing pattern
> you're trying to capture, write a *synthetic* equivalent in the
> same register. The eval doesn't care that the words are real — it
> cares that the phrasing distribution matches reality.

Schema:

```jsonl
{"query": "I need to spin up a CKS cluster with 8 H100s", "expected_skill": "cw-create-cluster"}
{"query": "how do I create an API token", "expected_skill": null}
{"query": "what's the weather in Paris", "expected_skill": null}
```

`expected_skill` is the **first** skill that should fire, or `null` for "no
skill applies, just chat".

### Multi-skill chains

A composite request needs more than one skill in sequence, and the failure mode
there is invisible to a single-skill label: the first skill fires, the run then
hand-rolls everything downstream instead of consulting the next skill. Add an
optional `expected_chain` to score that separately:

```jsonl
{"query": "Make me a Hello World inference service. Make everything new - cluster, etc.", "expected_skill": "cw-create-cluster", "expected_chain": ["cw-create-cluster", "cw-self-managed-inference"]}
```

- `expected_skill` keeps its exact meaning, so every single-skill entry is
  unaffected. Omit `expected_chain` and nothing changes.
- Order is matched as a **subsequence**, not adjacency — a real run legitimately
  interleaves another skill, such as `verify-coreweave-workload-health`,
  between the skills you named.
- Chain verdicts (`CHAIN_PASS` / `CHAIN_PARTIAL` / `CHAIN_OUT_OF_ORDER`) are
  reported in their own block, because chaining and routing fail for different
  reasons and have different fixes.

**Put in the chain only what should actually fire.** A greenfield request does
*not* chain into `cw-create-node-pool`: that skill's own description sends the
customer to `cw-create-cluster` when they have no cluster yet, because
`cw-create-cluster` creates the first node pool itself.

**What a chain case measures in the safe arm.** `Bash`/`Write`/`Task` are denied
by default, so a chain case measures whether the run *consults* each skill, not
whether it executes them. That is the intended signal: the observed defect is
that later skills are never loaded at all.

### Why bundle-level matters

A skill that passes its own correctness eval but routes incorrectly is
worse than no skill at all — it hijacks queries from skills that *would*
have handled them well. The bundle-level eval is the safety net for
"pushy" descriptions: writers are encouraged to be aggressive about
listing trigger phrases, and this eval catches when they cross the line
from "pushy" into "promiscuous".

### Running the bundle eval

Two runners share this corpus. They measure the same question at different
fidelities, and only one of them can gate CI:

| | `run_router_evals.py` (CI gate) | `run_trigger_evals.py` (session harness) |
| --- | --- | --- |
| Mechanism | One `messages.create` API call per query: the model is shown each shipped skill's `name` + `description` (from `dist/*/SKILL.md` — the same evidence the production router sees) and must pick one skill or `none` via a strict forced tool call | One headless `claude -p` session per run: the real product, real plugins, real competing tools |
| Needs | `ANTHROPIC_API_KEY` and `pip install -e ".[evals]"` | A Claude Code login with the plugins installed |
| Measures | Top-1 routing on `expected_skill` | Routing **and** `expected_chain`, tool competition, MCP attractors |
| Where | CI (`.github/workflows/trigger-evals.yml`) and locally | Local/manual only |

#### CI gate: `run_router_evals.py`

```bash
pip install -e ".[evals]"     # once; needs the anthropic SDK
export ANTHROPIC_API_KEY=...  # or let CI supply the repo secret

python evals/run_router_evals.py --output results.json
python evals/run_router_evals.py --votes 3   # majority of 3 calls per query
```

Gate semantics — the run **fails (exit 1)** when either holds:

- top-1 accuracy < `--min-accuracy` (default **0.90** — CI deliberately does
  not override the flag, so the script default *is* the gate and local runs
  can never disagree with CI about the threshold);
- any entry marked `"required": true` failed, regardless of overall accuracy.

Exit 2 is a config/environment error: a missing `ANTHROPIC_API_KEY`, a label
naming a skill that isn't in `dist/` (or is include-only), malformed JSONL,
and packaging drift are all caught **before any API call**; a credential
rejection or a request the API refuses outright (bad `--model`) also exits 2
mid-run. Exit 3 means the API kept failing transiently after retries. Entries
may carry optional `id`, `required`, and `notes` fields; `expected_chain` is
ignored by this runner (a single forced-choice call can't measure chaining —
that's the session harness's job). `--votes` must be odd; a ballot with no
strict majority scores as a routing failure.

There is also an experimental `--baseline <previous results.json>` regression
gate (any entry that passed in the baseline must still pass; entries new
since the baseline are exempt). **CI does not wire a baseline yet** — no job
produces or consumes one — so today it is a local comparison tool only.

Router candidates are the **shipped** skills only. Which dist skills ship is
owned by `standalone-skills.yaml` (no `plugin:` = include-only), read through
`scripts/check_plugin_parity.py`, and cross-checked against the committed
`plugins/*/skills/` mirrors — any disagreement refuses to run. Include-only
skills such as `get-coreweave-kubeconfig` are excluded because the production
router never sees them (the same reasoning as the no-broader-skill rule
below); `--include-unshipped` adds them back for experiments.

CI (`.github/workflows/trigger-evals.yml`) runs the gate on every PR touching
skill sources, packaging, or `dist/`, on every push to `main`, and nightly
with `--votes 3`. Same-repo runs **fail loudly** until a maintainer sets the
secret (`gh secret set ANTHROPIC_API_KEY --repo coreweave/skills`). Fork PRs
are skipped at the job level — they never receive repo secrets — so for an
outside contribution the gate lands on the push-to-main run after merge. The
results JSON is uploaded as the `trigger-eval-results` artifact.

#### Local session harness: `run_trigger_evals.py`

`run_trigger_evals.py` spawns one headless `claude -p` per run and scores what
the router did. Routing is stochastic, so `--runs` is **per case**, not a total.

```bash
cd evals
./run_trigger_evals.py --dry-run            # session count, spends nothing
./run_trigger_evals.py --limit 3 --runs 1   # small real sweep
./run_trigger_evals.py --no-mcp             # isolate the docs-MCP attractor
```

By default `Bash`, `Write`, `Edit`, `NotebookEdit` and `Task` are denied, so a
run physically cannot provision. That denial also shortens the tool list, which
can itself shift the routing decision being measured — see the module docstring.
`--allow-exec` gives a faithful tool list but a run may create billable
resources.

Chain cases get a larger tool budget (`--chain-max-tools`, default 40) because a
chain needs room to reach its second skill; single-skill cases still stop at
`--max-tools` (default 4).

CI does not gate on this harness — it needs a Claude Code login and installed
plugins. The CI gate is the router eval above.

### Labeling a bare credential query — the no-broader-skill rule

Some snippets render into `dist/` but ship in no plugin — they are
`include-only` (no `plugin:` in
[`standalone-skills.yaml`](../standalone-skills.yaml)). Customers get that
content **inlined** into the workflow skills that request the snippet, never
as a skill they can trigger by name. `generate-kubeconfig` and
`create-api-token` are both in that state today.

**The rule (scampbell, 2026-08-03):** a query that mentions the API token or
the kubeconfig *alone*, outside the context of a broader use case, is labeled
`null`. It must NOT be routed to a workflow skill that happens to inline the
procedure.

> "those two skills should be inlined wherever they make sense. If someone
> mentions the key or the kubeconfig alone outside the context of the other
> use cases, they should not be routed to one of the broader skills."

Why it matters that this is a rule and not two ad-hoc labels: the tempting
alternative is to point a bare "download my kubeconfig" at whichever workflow
skill carries the inlined copy. That reads helpful and scores green, but it
teaches the router that `cw-self-managed-inference` owns bare-credential
queries — so a customer who only wanted a kubeconfig gets a vLLM deployment
skill loaded, and a genuine inference request now competes with credential
chatter. A skill should claim a credential query only when the customer has
signalled the larger job the credential is *for*.

Applies to every query aimed at include-only content, including the
`create-api-token` ones. JSONL takes no comments, so labels covered by this
rule are recorded here:

| Query | Label | Why |
| --- | --- | --- |
| "how do I get my kubeconfig so I can run kubectl against my cluster?" | `null` | `get-coreweave-kubeconfig` is include-only (browser-first). Bare credential ask. |
| "download the kubeconfig for my CKS cluster" | `null` | Same. |

Two consequences worth stating:

- **Fix the labels in the same PR that withdraws the skill.** The session
  harness scores an expectation naming an uninstalled skill as
  `INVALID_LABEL`, which silently shrinks the scorable set rather than
  failing loudly — a stale label can sit for weeks looking like a pass. The
  CI router gate closes that hole: it refuses to run (exit 2) when a label
  names a skill that isn't in `dist/` or is include-only, so a same-repo
  withdrawing PR goes red until its labels are fixed (for a fork PR, that
  failure lands on the push-to-main run instead).
- **The inlining is what serves the customer**, so the rule only holds if the
  snippet really is included everywhere it belongs. When you withdraw a
  standalone, audit the workflow skills for the include — otherwise `null` is
  just a hole.

### Contributing trigger eval entries

When you ship a new workflow skill, add **at least five** queries to
this set:
- Three that *should* trigger your skill (varied phrasings).
- Two that *look like* they might trigger your skill but should
  actually route elsewhere (or to none).

The negatives are more valuable than the positives — they're how we
catch regressions when someone else ships a similarly-worded skill
later.
