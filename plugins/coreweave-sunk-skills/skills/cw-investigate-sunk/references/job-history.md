# Job history and outcome

Use `coreweave_sunk_get_job_evidence` for state/history without a scheduling
question. Read `outcome` alongside the underlying samples and gaps.

- Syncer `slurm_job_state` uses the label `id`. It can report terminal states
  such as COMPLETE or FAILED while Slurm still lists the job; retained samples
  may preserve those observations after it disappears.
- Exporter `slurmctld_job_state` uses `job_id` and adds pending reasons.
  `slurmctld_job_info` adds fields such as segment size, exclusivity, and requeue.
  Exporter disappearance does not establish a terminal outcome.
- A last RUNNING or PENDING sample followed by silence leaves the final outcome
  unknown. A syncer terminal observation supports that observed outcome; it does
  not supply an exact termination time, application exit code, or reason for
  failure unless those are separately evidenced. Preserve conflicting states.

Example: exporter last reported RUNNING at 10:00, while syncer reported COMPLETE
at 10:05. Report the completion observation at 10:05 with its source. Do not
ignore it because the exporter lacks the job, or invent an exact finish time.
If neither source reports a terminal state, identify the missing outcome and
suggest retained accounting or job-event evidence if available through approved
tools. Do not treat a proposed check as something already performed.

RUNNING means a scheduling state, not healthy execution. Use resource usage or
an authorized application-specific diagnostic only when the question requires it.
