Investigate the user's SUNK question with authorized read-only evidence. Explain
what happened, what remains unknown, and the next useful check. This skill does
not submit or cancel jobs, change policy, install exporters, or grant access.
Use the SUNK evidence toolset exposed by the connected Mission Control MCP
server. If it is unavailable, explain that prerequisite; do not substitute shell
commands. Read the linked references through the host agent's file or skill
resource reader. Authorization is enforced by the MCP service and its backend,
not by these instructions.

## Establish scope and time

Use the user's cluster and incident time. Resolve the regional datasource and
the exact `cluster`, `slurm_cluster`, and controller `namespace` metrics labels.
A Kubernetes context name alone does not establish those labels. Reuse verified
scope from the conversation; if several deployments remain, ask which one before
querying a job. Job IDs can repeat between Slurm deployments.

Use available datasource discovery and bounded label discovery scoped to the
known cluster. Read [scope and time](references/scope-and-time.md) if a label,
datasource, namespace, or historical window is uncertain. Follow the tool's
current input schema; omit unused optional arguments rather than sending null.

## Choose the first tool by the question

Tool names below have the prefix `coreweave_sunk_get_`. Discover only the tools
needed for this question if they are not already available. Load the matching
reference before interpreting its evidence; do not
load every reference or call every tool.

| Question | Tool suffix | Reference |
| --- | --- | --- |
| What happened to this job? Did it finish or fail? | `job_evidence` | [Job history](references/job-history.md) |
| Why is it pending? Are resources or a policy limit involved? | `scheduling_evidence` | [Scheduling and queues](references/scheduling-and-queues.md) |
| Which higher-priority jobs share its queue? | `queue_evidence` | [Scheduling and queues](references/scheduling-and-queues.md) |
| Is the scheduler/backfill slow? What runtime settings were observed? | `slurm_scheduler_runtime_evidence` | [Runtime and preemption](references/runtime-and-preemption.md) |
| What preemption policy or activity was observed? | `preemption_evidence` | [Runtime and preemption](references/runtime-and-preemption.md) |
| Why are idle nodes insufficient? Does this segment request fit? | `topology_evidence` | [Topology](references/topology.md) |
| What resources were allocated, and what usage was measured? | `resource_usage_evidence` | [Resource usage](references/resource-usage.md) |

Scheduling already includes job state: do not routinely fetch job evidence too.
For a queue snapshot, first establish a relevant pending observation time from
existing evidence or scheduling history. For an individual job's preemption,
establish its state history before interpreting cluster-level preemption context.

## Read the evidence before expanding

Every tool probes its producers. Read `data_sources`, `status`, sample times,
gaps, and recommendations. Syncer evidence is the baseline; exporter enrichment
is optional. A source marked `not_observed` was not observed in this scope/window;
that alone does not establish it was never installed. `not_queried` is different.

Use available `syncer_metrics`, `syncer_summaries`, `syncer_counts`, and any
tool-specific summaries. Do not discard useful syncer evidence because the
exporter is missing. `partial` or `incomplete` can support a narrower answer.
Name the missing capability and relay applicable exporter recommendations as an
option, not as permission to deploy it or a requirement for every diagnosis.

Make the next call only to answer a specific unresolved question that could
change the conclusion. One SUNK call can fan out into dozens of metrics queries.
Use fixed incident windows, start small, reuse results, and keep queue examples
small (five is a useful starting point). Extra useful queries are fine; repeated
overlapping queries without a new hypothesis are not. Narrow after a size or
timeout error rather than repeating it. Stop a denied path; do not bypass access
through another tool or pod exec. Use generic scoped tools only for a missing
fact the SUNK tools cannot supply, within the same authorization boundary.

## Turn evidence into an answer

Build the answer around the user's decision:

1. State what the evidence establishes, with the decisive values and observation
   times. Keep the headline within that boundary; a caveat later does not repair
   an unsupported opening claim.
2. Explain what those observations mean for this job or question. Keep a
   possible explanation conditional unless evidence distinguishes it from the
   alternatives. A useful next check need not be the most likely cause.
3. Give the next check that would change the decision, and explain how its
   possible results would change the conclusion. If the needed source is not
   available, identify the missing evidence without pretending to have read it.
   Describe what to look for, not what a future tool response will necessarily
   contain or prove. Before recommending another SUNK tool, read its reference
   if you need to establish its capability or limits.

Before sending, check each claim of cause, timing, health, or sizing against the
actual response. Preserve metric names and units. Distinguish observed state
from application success, window summaries from ordered events, and aggregate
context from evidence attributable to the target job. Do not fill these gaps
with plausible domain knowledge. Keep the answer focused rather than repeating
all the tool output or every possible caveat.

Missing data is not zero, completion, or health. A limit correlation, idle-node
count, queue example, or scheduler counter alone does not prove a job's blocker.
Do not promise a start time, infer an exact transition between scrapes, or
recommend a precise resource/configuration change without the evidence it needs.
