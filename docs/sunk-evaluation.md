# SUNK diagnostic skill: final v6 evaluation

`cw-investigate-sunk` is the evaluated expanded-v6 package. It guides diagnosis
across job history, scheduling, queues, runtime, preemption, topology, and resource
usage, distinguishing baseline syncer evidence from optional exporter data.
The source follows this repository's `skill.yaml` and `body.md` template, with
six domain references. `build.py` generates the dist and SUNK plugin copies.

## What we tested

Six controlled scenarios ran in Claude Code with `claude-sonnet-5-5`, identical
prompts, fixed incident times, supplied scope, and restricted synthetic MCP
responses. These are constructed development cases, not native cluster captures.
The skill was explicitly invoked; the reviewer (`claude-sonnet-4-6`) graded the
answer against the evidence actually returned. The v3 rubric, evidence, and
scoring stayed fixed while the skill changed. Query counts were recorded
separately, without an efficiency deduction from diagnostic quality.

The scenarios cover pending constraints and queue peers, disappeared jobs,
backfill counters and scheduler timing, whole-segment placement, resource sizing,
and preemption. Checks assess grounded conclusions, temporal and source
interpretation, appropriate tool use, uncertainty, and helpful next steps.
Positive credit rewards useful explanations and decision support.

| Skill | Model-review passes |
| --- | ---: |
| None | 4/6 |
| Concise v2 | 5/6 |
| Expanded v4 | 6/6 |
| **Expanded v6 (selected)** | **6/6** |

No skill failed runtime and topology; concise v2 failed runtime. Answer inspection
also found that v6 avoided an invented reset timeline, preserved unknown policy
limits, and deferred a concrete CPU allocation until representative measurements
exist. V4's passing grades missed some unsupported claims. These observations
explain the selection; they do not establish a universal or statistically
significant advantage over v4. The cases were used during development, and
answer inspection was performed by an assistant rather than independent engineers.

Ordinary-prompt checks activated v6 on 3/3 questions versus 2/3 for v4. Those checks
are separate from the explicitly invoked correctness runs and from this
repository's description-only router test.

Four existing native MCP captures were also replayed in local MCA's
`ToolCallingLLM`. They exposed remaining causal, event-time, and zero-counter
misinterpretations. Some model reviews timed out and remain unreviewed. This
replay does not establish effectiveness in live clusters or hosted MCA.
The [aggregate record](evidence/sunk-v6-summary.json) pins models, hashes,
source revisions, selected results, and limitations. Raw native payloads and
customer identifiers are not included here.

## Reproduction and contribution status

The [final eval handoff in coreweave/skills-evals](https://github.com/coreweave/skills-evals/tree/feat/azheng/sunk-diagnostic-evals/experiments/sunk-diagnostics/contributions/skills-evals-final)
contains six frozen scenarios, synthetic MCP fixtures, grading criteria, saved
answers and reviews, integrity checks, and rerun instructions. Repository access
is required. These standalone experiments are development evidence, not the
standard Harbor correctness gate.

The same branch includes a separate [18-investigation token comparison](https://github.com/coreweave/skills-evals/blob/feat/azheng/sunk-diagnostic-evals/experiments/sunk-diagnostics/evidence/token-comparison/REPORT.md).
It compares generic metrics, SUNK tools, and SUNK tools plus this skill in local
MCA over three synthetic scenarios, with two runs per configuration. Adding the
skill to the SUNK tools reduced diagnostic calls but increased cumulative token
usage by about 94%; it did not demonstrate token savings. Grading timeouts and
disagreements prevent a reliable answer-quality ranking for that experiment.

Ten synthetic trigger queries (seven positive, three negative) live in
`evals/trigger-evals.jsonl`. The unchanged description-only router selected the
expected result on all ten using `claude-sonnet-4-6`, one vote per query, against
all six shipped candidates. [Saved routing results](evidence/sunk-v6-router.json)
cover this subset; the full 221-query corpus passed preflight but was not model
tested in this packaging round. An initial Sonnet 5.5 attempt rejected forced
tool choice before any scored result. Run the standard full-bundle check here:

```bash
python build.py
python scripts/lint_skill_content.py
python scripts/check_plugin_parity.py
python evals/run_router_evals.py --dry-run
# Billed provider calls; configure ANTHROPIC_API_KEY privately first.
python evals/run_router_evals.py --output /tmp/skills-router-results.json
```

The skill is also bundled unchanged on the MCA local-demo branch. Native catalog
loading, activation, reference reads, and file hashes are tested there. The
manifest documents the `disallowed-tools` metadata compatibility waiver; the
runtime and MCP server must enforce permissions.

**Official correctness integration remains pending.** The contributing guide
refers to `wandb/skills-evals`, which now resolves to `coreweave/skills-evals`.
The prepared experiments are published on its `feat/azheng/sunk-diagnostic-evals`
branch, but are not yet registered Harbor tasks. Adapt the six cases to that
harness, run its verifier and tests, commit them, and generate the mirror here:

```bash
python evals/sync_skill_evals.py --write \
  --harness /path/to/skills-evals --ref <committed-task-ref>
python evals/validate_skill_evals.py
python evals/sync_skill_evals.py --harness /path/to/skills-evals \
  --ref <committed-task-ref>
```

`skills/cw-investigate-sunk/evals/evals.json` is intentionally absent until that
step. Do not hand-write a mirror or treat the custom experiment as the official
correctness CI result. Before release, also obtain engineer review of the saved
answers and validate the intended consumer on fresh conversations.
