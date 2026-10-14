# Scheduler runtime and preemption

These tools are cluster/controller context, not per-job diagnoses. They accept
no `job_id`. Keep controllers separate and use exact controller filters when
needed; retained old-pod series and fresh scrape times do not prove leadership.

## Runtime

`coreweave_sunk_get_slurm_scheduler_runtime_evidence` returns syncer sdiag window
summaries and optional exporter settings, cycle activity, and exit reasons.
Syncer `slurm_scheduler_*` values include counts that reset with Slurm statistics;
they are gauges, not monotonic event counters. Do not compute event totals by
subtracting their endpoints. Summaries use 60-second steps, not exact events.

Exporter counter increases cover only continuous observed pairs. Resets, gaps,
stale markers, and conflicts break the segments. One sample has unknown increase,
not zero. Do not extrapolate to the entire incident or sum controllers. Preserve
configuration parameter names, signed values, and unknown units/defaults rather
than inventing effective `slurm.conf`. Constant counters do not prove inactivity
or a healthy collector. An exit-reason counter does not identify the jobs tested
or prove why this job waited.

## Preemption

`coreweave_sunk_get_preemption_evidence` uses syncer `slurm_queue_preempted` as a
count of jobs currently retained in that state. It is neither an event total
nor a list of victims. A job's syncer PREEMPTED observation supports that state,
but a cluster count does not attribute it to that job.

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
