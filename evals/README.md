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
  interleaves `get-coreweave-kubeconfig` or
  `verify-coreweave-workload-health` between the skills you named.
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

CI does not yet gate on this. The intended next step is a job in
`.github/workflows/build.yml` reporting the trigger-accuracy delta versus `main`.

### Contributing trigger eval entries

When you ship a new workflow skill, add **at least five** queries to
this set:
- Three that *should* trigger your skill (varied phrasings).
- Two that *look like* they might trigger your skill but should
  actually route elsewhere (or to none).

The negatives are more valuable than the positives — they're how we
catch regressions when someone else ships a similarly-worded skill
later.
