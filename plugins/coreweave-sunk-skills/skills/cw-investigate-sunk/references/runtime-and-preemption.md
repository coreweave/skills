# Scheduler runtime and preemption

These tools are cluster/controller context, not per-job diagnoses. They accept
no `job_id`. Keep controllers separate and use exact controller filters when
needed; retained old-pod series and fresh scrape times do not prove leadership.

## Runtime

`coreweave_sunk_get_slurm_scheduler_runtime_evidence` returns syncer sdiag window
summaries and optional exporter settings, cycle activity, and exit reasons.
Syncer `slurm_scheduler_*` values include counts that reset with Slurm statistics;
they are gauges, not monotonic event counters. Do not compute event totals by
subtracting their endpoints. A min/max/latest summary does not order the extrema
or place them before or after a reset. Report the observed range and latest
value separately; do not reconstruct a reset timeline, event total, or lower
bound on work completed in the incident window. Use a sampling interval only
when the response or verified tool contract establishes it.

Keep scheduler-cycle duration distinct from backfill-cycle duration. Preserve
the returned metric's meaning when converting units; short scheduler cycles do
not establish fast backfill, low queue delay, or useful scheduling progress.

A short cycle duration alone does not establish healthy scheduling or rule out
a slowdown. Without a comparable baseline, workload context, and a relevant
latency target, report the measured duration and say performance is undetermined.
Do not headline "not slow" or "healthy" and qualify it only later.

Exporter counter increases cover only continuous observed pairs. Resets, gaps,
stale markers, and conflicts break the segments. One sample has unknown increase,
not zero. Do not extrapolate to the entire incident or sum controllers. Preserve
configuration parameter names, signed values, and unknown units/defaults rather
than inventing effective `slurm.conf`. Constant counters do not prove inactivity
or a healthy collector. An exit-reason counter does not identify the jobs tested
or prove why this job waited. Job/scheduling evidence can establish observed
states and constraints; do not promise it will identify that a job started
through backfill. That needs explicit per-job evidence of the scheduling path.

## Preemption

`coreweave_sunk_get_preemption_evidence` uses syncer `slurm_queue_preempted` as a
count of jobs currently retained in that state. It is neither an event total
nor a list of victims. A job's syncer PREEMPTED observation supports that state,
but a cluster count does not attribute it to that job. An absent PREEMPTED sample
or a later FAILED/COMPLETE/CANCELLED observation does not rule out an earlier
preemption or requeue. Resolve this from retained event history if available;
do not turn incomplete state sampling into a negative event claim.

Exporter evidence adds QoS modes, directed relationships, action outcomes, and
trigger counters. Mode `off` can inherit an unknown global mode; it is not proof
that preemption is disabled. Missing relationships are unknown, not an empty
policy. Preserve unresolved `id:<n>` targets and unknown modes. The observations
do not establish full effective eligibility or complete global policy.

Action counters identify QoS relationships, not victim job IDs. A trigger may
identify a preemptor but is not a successful victim count. Do not join a trigger,
action, and state transition into the same event solely because their times
are close. Apply the same counter reset/gap rules as runtime. Recommend a
specific policy or event check when attribution is missing, rather than claiming
which job preempted which other job.
