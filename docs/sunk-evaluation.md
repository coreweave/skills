# SUNK diagnostic skill evaluation

`cw-investigate-sunk` guides investigation across the seven SUNK evidence tools:
job history, scheduling, queue, runtime, preemption, topology, and resource usage.
Its references distinguish baseline syncer evidence from optional exporter data.
A valid partial answer is preferable to an unsupported diagnosis.

## Contribution status

The source, rendered skill, SUNK plugin, marketplace entry, and ten synthetic
trigger queries are prepared. The rendered bundle also loads through MCA's
native `SkillCatalog`, `activate_skill`, and `read_skill_resource` interfaces.
The documented tool-denylist waiver is necessary because that MCA loader rejects
`disallowed-tools` metadata. Permission enforcement belongs to the consuming
runtime and MCP server; skill text does not provide an authorization boundary.

The correctness scenarios are staged separately for `wandb/skills-evals`, as
required by this repository's contributing guide. Access to that repository was
unavailable. Its task scaffold and verifier have not been run, and the generated
`skills/cw-investigate-sunk/evals/evals.json` mirror is intentionally absent.
This contribution is not ready for a claim of complete official eval coverage.

## Correctness coverage to import

Eight authored synthetic cases exercise terminal syncer history versus stale
exporter state, capacity versus schedulability, queue sample coverage, runtime
resets and health baselines, preemption attribution, whole-segment arithmetic,
allocation versus utilization, and useful diagnostics without an exporter.

Each staged task includes a prompt, isolated evidence fixture, answer key, and
criterion-based grading instructions. The evidence is synthetic and compact;
it is not a recorded response from a customer's cluster. The staging package's
README explains how to adapt it to the official mock MCP and verifier, commit
it there, and generate the mirror with `evals/sync_skill_evals.py`.

The separate development evaluator also exercises recorded native MCP evidence
and bounded live read-only requests. Raw captures, credentials, and customer
identities must remain outside this public repository.

## What the experiments establish

The initial expanded version was compared with a previous concise skill and no
skill on 18 cases, with identical evidence per case and the same model. Results
were 16/18 passing for the expanded skill, 18/18 for the previous skill, and 15/18
for no skill. These are single-run, provisional model judgments, not release
gates or evidence of a universal improvement. An available skill was not always
activated; activation is reported separately from answer quality.

The first synthetic checks found overconfident health claims without a baseline
and placement recommendations beyond the available evidence. The source now
makes those limits explicit and asks for application logs or accounting when
RUNNING state cannot answer an execution-health or final-outcome question.
Post-feedback runs are development checks on known cases, not an independent
holdout. Extra queries are reported as cost, not deducted from answer quality.

Before release, complete the official harness integration, review disputed
model grades, and test the intended consumer's actual tool discovery and model
settings. A local isolated MCA loop does not establish hosted service behavior.
