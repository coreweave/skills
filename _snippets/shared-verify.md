<!--
  EXAMPLE SNIPPET FILE — not production content.

  Cross-cutting verification procedures used by workflows in any product
  line (Grafana checks, kubectl-based health probes, W&B run sanity
  checks). See coreweave-platform.md for the tagged-region convention.
-->

<!-- snippet:verify-in-grafana -->
## Verify the result in Grafana

1. Open the Grafana dashboard at
   `https://grafana.coreweave.com{{ DASHBOARD_PATH }}`.
2. Set the time range to **Last 15 minutes** and pick the variable
   value matching the resource you just created or modified.
3. Confirm that the **`{{ EXPECTED_METRIC }}`** panel is reporting
   non-zero data. If it stays flat for more than 5 minutes:

   - Re-check the resource exists (`kubectl get …` or the matching
     `coreweave` CLI command).
   - Confirm the Prometheus scrape target is `UP` from the **Targets**
     view in the Grafana datasource explorer.
   - File a ticket with the run/cluster ID if both of the above pass.

> Grafana is the source of truth for "did it actually start working?"
> Do not declare a workflow complete on the basis of `kubectl` /
> Console state alone — those reflect intent, not realized behavior.
<!-- /snippet:verify-in-grafana -->
