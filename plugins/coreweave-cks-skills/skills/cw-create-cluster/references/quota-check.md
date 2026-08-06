# Quota check via Console browser automation

Use this reference when browser tools are available to check cluster and node type quota before creating a CKS cluster. There is no quota API or Terraform data source, so the Console is the only way to see the *whole* quota picture up front.

> **For node-type quota specifically, the Console is not the last word.** Once a
> cluster exists, a NodePool's own `status.conditions[type=Quota]` reports the
> org's quota for that instance type and zone in machine-readable form, and it
> is authoritative — see the `cw-create-node-pool` skill. Use the Console to
> plan; use the condition to confirm. If a customer reports quota from the
> Console and the condition later says `reason: NotSet`, believe the condition.
>
> Note also that the Console Quotas page **403s under browser automation** in
> some sessions. If that happens, fall back to asking the customer rather than
> reporting the quota check as done.

---

## Navigate to the Quotas page

1. Navigate to `https://console.coreweave.com`.
2. In the left sidebar, click **Administration**.
3. Click **Quotas** (or navigate directly to `/organization/quotas` if the sidebar path differs).
4. Take a snapshot to read the page content.

---

## What to extract

### Cluster quota

Look for a section showing cluster limits. Extract:
- **Maximum clusters allowed** — the total cluster quota for the organization.
- **Clusters currently in use** — how many clusters already exist.
- **Remaining** — how many more clusters can be created.

If the remaining count is 0, the customer must delete an existing cluster or contact CoreWeave support to request a quota increase before proceeding.

### Node type / instance type availability

Look for sections showing compute quota by instance type. Extract:
- **Instance types with quota** — e.g., `gd-8xh100ib-i128`, `cpu-4`, etc.
- **Quota per type** — how many nodes of each type are allowed.
- **Currently in use** — how many are already provisioned.
- **Available zones** — which zones each instance type is available in.

### Zone information

Cross-reference zones where the customer has quota with zones where they want to deploy. The VPC, cluster, and node pools must all be in the same zone.

---

## Reporting findings

After extracting quota data, summarize it for the customer in a table format:

```
Cluster quota: X/Y used (Z remaining)

Instance types available:
| Type                | Quota | Used | Remaining | Zones          |
|---------------------|-------|------|-----------|----------------|
| gd-8xh100ib-i128   | 10    | 4    | 6         | US-EAST-04A    |
| cpu-4               | 20    | 5    | 15        | US-EAST-04A    |
```

This information guides the zone and instance type choices in Step 2 of the cluster creation workflow.

---

## If the Quotas page layout changes

The Console UI may evolve. If the expected elements aren't found:
1. Take a screenshot and examine the page visually.
2. Look for tabs or filters that might separate cluster quota from compute quota.
3. If quota information can't be found, fall back to asking the customer to check manually.

---

## Browser automation patterns

Follow the same browser automation patterns documented in the `cw-add-users` skill's `references/browser-automation.md`:
- Take a snapshot after each navigation to get fresh element references.
- Use `aria/` or `text/` locators to find elements by their visible labels.
- If a page requires scrolling to see all quota information, scroll and take another snapshot.
- The Quotas page may paginate instance types — check for pagination controls and navigate through all pages.
