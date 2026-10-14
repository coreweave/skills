# Resource allocation and measured usage

Use `coreweave_sunk_get_resource_usage_evidence` for a scoped job. Its `namespace`
is the **compute exporter's scrape namespace**, which can differ from controller
tools. Pass the verified controller namespace as `controller_namespace` to obtain
syncer allocation. The same cluster/Slurm deployment/job must be preserved.
An optional exact exporter filter can narrow a multi-source result.

Syncer allocation reports observed Slurm nodes, CPUs/GPUs per node, and uptime
while the job runs. Allocation is not utilization or a recommendation to resize.
Slurm node names are not automatically Kubernetes/DCGM node names.

Exporter cgroup evidence adds measured CPU, memory, pressure, and GPU allocation.
Keep sources separate: each may cover only part of a multi-node job, and duplicate
sources must not be summed. Use returned valid summaries and their coverage.
Do not use requested CPUs as a utilization denominator. A cgroup CPU quota is
not necessarily the Slurm allocation; `-1` means no finite quota. Memory includes
cache; working set is not application RSS. Zero, negative, or sentinel-like
memory limits cannot support meaningful utilization ratios.

GPU context is joined by verified allocation UUID and cluster, not ordinal or
guessed node-name mapping. DCGM describes a device; observed allocation does not
prove continuous or exclusive ownership. Do not attribute samples outside the
allocation observations to this job. High/low device utilization alone does not
establish job efficiency or application health.

Separate three statements: what Slurm allocated, what usage was measured during
covered observations, and what remains unknown. Missing samples are not zero
usage, no GPUs, or completion. Offer a conditional next measurement when coverage,
workload intent, or performance goals are missing. Do not turn a short quiet
sample into a precise provisioning or resource-reduction recommendation.
