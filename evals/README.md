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

Schema sketch (final shape TBD when the harness lands):

```jsonl
{"query": "I need to spin up a CKS cluster with 8 H100s", "expected_skill": "deploy-cks-cluster"}
{"query": "how do I create an API token", "expected_skill": "create-coreweave-api-token"}
{"query": "what's the weather in Paris", "expected_skill": null}
```

### Why bundle-level matters

A skill that passes its own correctness eval but routes incorrectly is
worse than no skill at all — it hijacks queries from skills that *would*
have handled them well. The bundle-level eval is the safety net for
"pushy" descriptions: writers are encouraged to be aggressive about
listing trigger phrases, and this eval catches when they cross the line
from "pushy" into "promiscuous".

### Running the bundle eval

TBD — the harness is a separate work item. Once it exists, CI will
add a second job to `.github/workflows/build.yml` that runs the eval
against every PR and reports the trigger-accuracy delta versus `main`.

### Contributing trigger eval entries

When you ship a new workflow skill, add **at least five** queries to
this set:
- Three that *should* trigger your skill (varied phrasings).
- Two that *look like* they might trigger your skill but should
  actually route elsewhere (or to none).

The negatives are more valuable than the positives — they're how we
catch regressions when someone else ships a similarly-worded skill
later.
