# Scheduling, policy limits, and queue context

Use `coreweave_sunk_get_scheduling_evidence` for why-pending questions. It includes
job state and context, so another job lookup is usually redundant.

Syncer supplies state history and partition capacity/node-state context near the
job's last PENDING observation. It does not supply a pending reason or policy
cap. A node can carry several state flags: do not sum state rows into a unique
node count. `partition_fit` is a limited CPU/GPU/node-capacity check. It does not
cover features, reservations, memory, QoS, or topology. Enough capacity under
that check does not establish schedulability.

Exporter evidence adds pending reason, requested resources, priority, and
controller-qualified account/QoS comparisons. Match resource meaning, account,
QoS, controller, and observation time before comparing a request/usage with a cap.
Use the tool's preceding-sample alignment; do not mix today's cap with an older
pending observation. An unmapped TRES ID remains unknown; do not guess CPU/GPU
meaning or suggest a numeric cap change from it. Missing usage is not zero.

An active-job cap can support a policy constraint when the relevant identity,
usage, limit, and reason align. A full cap alone does not prove it blocked this
job. Submitted-job limits are not running-job limits. A reason identifies a
constraint class without necessarily identifying its numeric cap or blocking
job. Starting after another job finishes does not by itself prove causation.

Use `coreweave_sunk_get_queue_evidence` when relative priority matters. Pass a
fixed pending observation as `endTime`; it accepts no start time. Start with
`limit=5`. The exporter returns a bounded selection of strictly higher-priority
pending peers in the same controller and partition, not exact queue rank or
blocking jobs. Syncer pending counts supply partition/account/QoS context without
priority ordering. Read `coverage`: the partition total, higher-priority total,
and returned examples have different meanings; separately scraped ratios are
approximate. A top-five sample does not describe every queued job.

If the reason is Resources despite apparently sufficient nodes, topology or
unavailable node flags may warrant the next check. If the concern is scheduler
throughput, use runtime evidence. Follow the user's question rather than taking
both paths automatically. Suggest waiting for capacity or policy-owner review
only conditionally; neither guarantees when a job will start.
