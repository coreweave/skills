# Topology placement

Use `coreweave_sunk_get_topology_evidence` for segment/block constraints, especially
Resources pending with apparently idle nodes. It accepts an optional `job_id` or
a hypothetical `segment_size` and `node_count`; job values take precedence and
conflicting supplied values are flagged. State explicitly when assessing a
hypothetical configuration rather than the observed job.

Syncer can provide partition node states and, on newer versions, the job's
`segment_size`. Missing segment size is not a value of one. Without exporter
evidence, segment rules can still be checked if the required values are known,
but block capacity and exclusive-topology settings remain unknown.

Read the returned `segment_rule` before interpreting capacity. A node count not
divisible by an applicable segment size cannot be placed under the described
block rules. A segment larger than the job's node count is version-dependent:
upstream Slurm and SUNK's Slurm have differed. Verify the version/returned
limitation and seek authorized controller-log evidence if needed; do not apply
one universal rule. `exclusive=topo` requires unused blocks, not merely idle
nodes in blocks shared with another job.

For block placement, whole segments that fit are the sum of
`floor(available_nodes_in_block / segment_size)`. For example, two blocks with
four candidates each provide zero whole five-node segments despite eight
candidates in total. Use the tool's assessments and preserve controller/topology
grouping; do not combine capacity from different controllers.

**Block availability describes the most recent block evaluation, potentially for
a different job.** Its candidate nodes were already filtered for that evaluation's
partition/features/exclusivity. Even a fresh capacity shortage is context, not
proof that it blocked the target job. Align time and identify this attribution
gap before making a causal claim. Blocks are indices, not names; old indices
may persist across reconfiguration. Node-to-block membership is not exported.

Topology families are discovered dynamically. Block topology currently supports
placement capacity; another plugin may expose inventory only. Do not apply block
arithmetic to inventory from an unsupported topology type. Missing topology
metrics do not establish that topology is unconfigured. Scheduler-cycle activity
helps select a controller but does not establish leadership.
