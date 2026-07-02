
# Orient in the Console: quota, capacity, and region

You are helping a CoreWeave customer — often on their **first login** — answer a
deceptively simple question: *"What capacity do I actually have, of what type,
in which region, and what should I do first?"* This is the orientation step
before creating clusters or inviting a team.

## Read this first — an honesty note about scope

**CoreWeave has no quota, usage, or capacity API today.** There is no
`cw quota check` command, no REST endpoint, and no Terraform data source that
returns "how much GPU quota does my org have and how much is used." Anyone who
tells you otherwise is describing something aspirational. Quota lives **only in
the Cloud Console UI**, and quota *increases* are requested through a support
ticket, not an API call.

What this means for how this skill works:

- The **quota numbers** (how many nodes of each instance type your org is
  allowed) can only be read from the Console **Quotas** page. This skill reads
  that page with **browser automation if it's available**, and otherwise walks
  you through it **manually**. It cannot be a fully autonomous check.
- The **list of clusters you already have** (and their zones) *can* be read
  programmatically — via the CKS API or `cwic` — so this skill uses that where
  it helps, and cross-references it against the Console for the full picture.
- **Do not invent APIs or CLI commands.** If you catch yourself about to type
  `cw quota ...`, stop — it does not exist.

Be upfront with the customer about this. It sets correct expectations and it's
the truth.

---

## The four questions this skill answers

By the end, the customer should be able to answer all four:

1. **How many clusters am I allowed, and how many do I already have?**
2. **What node/instance types do I have quota for, and how much is used vs. free?**
3. **Which region and Availability Zone (AZ) is that capacity in?**
4. **What should I do first, and which skill takes it from here?**

CoreWeave organizes locations as Geo → Super Region → Region → **Availability
Zone (AZ)**, e.g. `US-EAST-04A`. **A CKS cluster lives in exactly one AZ** — it
is single-AZ — so "which region is my capacity in" really means "which AZ is
each cluster and its quota in." Keep zone and cluster tied together in
everything you report.

---

## Before you start

Confirm the following:

- The customer has a CoreWeave organization and can sign in to the Cloud Console
  at **`console.coreweave.com`** (not `cloud.coreweave.com`).
- To see quota, their user needs read access to the organization. Viewing the
  **Quotas** page and the **Clusters** page requires being signed in to the org;
  if the customer can't see these pages, they may need an IAM role from an admin
  (hand off to `cw-add-users`).
- Set expectations per the honesty note above: quota is Console-only.

### Probe for browser access before asking

Attempt a browser tool such as `tabs_context_mcp` (or your environment's
equivalent) **silently**. There are three outcomes:

1. **Connected** — browser tools are live. Tell the customer:
   > "I can open the Console and read your quota and clusters for you — I won't
   > change anything, just read. Want me to go ahead?"
   If yes, read `references/quota-check.md` and follow it.

2. **Not connected, but tools exist** — browser tools are loaded but Chrome
   isn't reachable. Tell the customer:
   > "I have browser tools but they're not connected to Chrome yet. I can walk
   > you through a ~2-minute setup, or I can guide you through this manually.
   > Which do you prefer?"
   If they want setup, use the `cw-console-browser-access` skill, then retry.

3. **No browser tools** — no browser MCP in scope. Skip the offer and use the
   **manual walkthrough** in `references/manual-walkthrough.md`.

Whichever path you take, this is a **read-only** orientation. Never create,
delete, or change anything in this skill — that's what the hand-off skills are
for.

---

## Step 1 — Discover the clusters the customer already has

Start with what exists, because it anchors everything else to a zone.

**If browser tools are available:** navigate to the **Clusters** page at
`console.coreweave.com/clusters` (left sidebar → **Clusters**). Read the list of
clusters and, for each, capture its **name**, **zone/AZ**, and **status**. See
`references/quota-check.md` for the extraction details.

**If the customer has an API access token and prefers the CLI/API:** the CKS
API can list clusters across all zones. This is the *one* piece of orientation
that is genuinely available programmatically:

```bash
curl -s -X GET https://api.coreweave.com/v1beta1/cks/clusters \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $CW_API_ACCESS_TOKEN"
```

Each returned cluster includes `name`, `zone`, `vpcId`, and Kubernetes
`version`. (`cwic cluster get` returns the same information if the customer has
the CoreWeave Intelligent CLI installed.) Note that this lists clusters and
their zones — it does **not** return quota.

**If neither is available:** ask the customer to open the Clusters page and read
you the cluster names, zones, and statuses.

Record the result as a simple table — you'll join quota onto it in Step 2:

```
| Cluster        | Zone (AZ)     | Status  |
|----------------|---------------|---------|
| prod-training  | US-EAST-04A   | Running |
| (none yet)     | —             | —       |
```

If the customer has **zero clusters**, that's expected on first login — say so,
and note that Step 4 will point them at `cw-create-cluster`.

---

## Step 2 — Read quota: clusters allowed and node-type limits

This is the part with **no API**. Read it from the Console **Quotas** page at
`console.coreweave.com/organization/quotas` (left sidebar → **Administration** →
**Quotas**; you can browse or search for a specific quota).

**If browser tools are available:** follow `references/quota-check.md` to
navigate and extract. Pull out:

- **Cluster quota** — maximum clusters allowed vs. currently in use.
- **Node/instance-type quota** — for each instance type (e.g.
  `gd-8xh100ib-i128`, a CPU type), the allowed count, the used count, and the
  remaining count.

**If browser tools are not available:** use
`references/manual-walkthrough.md`, which tells the customer exactly what to
click and read back to you.

Join the quota onto the cluster table from Step 1 so the customer sees capacity
*and* where it lives:

```
Cluster quota: 1 of 3 used (2 remaining)

Node-type quota by instance type:
| Instance type      | Allowed | Used | Free | Zones with your quota |
|--------------------|---------|------|------|-----------------------|
| gd-8xh100ib-i128   | 16      | 8    | 8    | US-EAST-04A           |
| cpu-... (CPU type) | 32      | 4    | 28   | US-EAST-04A           |
```

> **Note on "used":** the Quotas page shows allowed vs. used at the org level.
> To see *why* a specific Node Pool is over quota, a customer with cluster
> access can also run `kubectl get nodepool` and read the **QUOTA** column
> (`Under` / `Over` / `NotSet`) and the **Capacity** column — but that's a
> diagnostic detail, not a substitute for the Quotas page.

If any instance type shows **0 remaining**, or cluster quota is fully used, the
only way to get more is a **quota-increase support ticket** (see Step 4). There
is no self-serve API for it.

---

## Step 3 — Confirm region/zone availability for what they want

Quota tells you what you're *allowed*; it does not tell you whether a zone
currently has physical *capacity* to hand you those nodes. These are two
different gates, and both must pass. Two Console tools help:

- **Capacity Finder** (Console → **Compute** page → **Capacity finder** tab).
  Enter a GPU or CPU instance type and a Node count, click **Find capacity**,
  and it returns ranked **Zone cards** with an availability label
  (*Likely available*, *Possibly available*, *Limited availability*,
  *Not available*, *SKU unavailable*). Zones where the customer already runs a
  cluster and capacity is likely appear under **Recommended**.
  - Caveats to state plainly: Capacity Finder is **Spot-oriented and
    informational** — it evaluates General Access zones only, excludes Superchip
    types (GH200, GB200, GB300), uses cached samples (not live inventory), and
    does **not** guarantee a reservation.
- **Instance availability matrix / per-zone instance lists** in the docs — to
  confirm which zones a given GPU type is even offered in, before checking
  capacity. See the References section.

Reporting rule: a customer can only place nodes where **quota** (Step 2) and
**capacity** (this step) overlap **and** where the cluster's zone matches. Call
out that overlap explicitly.

If browser tools are available, read `references/quota-check.md` for how to
drive Capacity Finder. Otherwise, the manual walkthrough covers it.

---

## Step 4 — Orient: what to do first, and hand off

Now synthesize. Give the customer a short, prioritized "here's where you stand
and here's what to do next," then route them to the right skill. Pick the branch
that matches what you found:

**No clusters yet (fresh org).**
> "You have quota for _N_ `<type>` nodes in `<zone>` and cluster quota of
> _X_. You don't have any clusters yet, so the first move is to create one in a
> zone where you have both quota and capacity. I can start that for you."
Hand off to **`cw-create-cluster`** (it covers VPC + cluster + node pools, and
re-checks quota as part of its flow).

**Clusters exist and there's free quota + capacity.**
> "You've got _free_ quota (_N_ `<type>` nodes) in `<zone>`, and Capacity Finder
> shows that zone as _`<label>`_. You can add capacity to your existing cluster
> `<name>` there."
Hand off to **`cw-create-cluster`** for a new cluster, or point them at node
pool provisioning in their existing cluster.

**Quota is exhausted (0 remaining) or capacity is unavailable.**
> "You're at your quota limit for `<type>` (or the zone is out of capacity right
> now). The only way to raise quota is a support ticket — there's no API for it.
> Meanwhile, `<other zone>` shows capacity for the same type if that works for
> you."
Point them at the **quota-increase support ticket** (Freshdesk) and, if useful,
an alternate zone from Step 3.

**They actually need to set up their team, not compute.**
Hand off to **`cw-add-users`** (groups, policies, invitations).

Always end with a one-line recap of the four answers (clusters allowed/used,
node-type quota used/free, zone, recommended next step) so the customer has the
orientation in one place.

---

## Common pitfalls

**❌ Assuming quota == capacity.** Quota is your allowance; capacity is whether
the zone physically has nodes right now. A customer can be under quota and still
unable to provision because the zone is full. Always check both (Steps 2 and 3).

**❌ Forgetting a cluster is single-AZ.** Quota for a type in `US-EAST-04A`
doesn't help a cluster in `US-WEST-01A`. Tie every quota figure to its zone and
match it to the cluster's zone.

**❌ Inventing a quota API or CLI.** There isn't one. Read the Console, or hand
the customer the manual steps. Quota *increases* go through a support ticket.

**❌ Changing anything.** This skill is read-only orientation. Creating,
scaling, or deleting is the job of the hand-off skills, with their own
confirmation checkpoints.

---

## References

The following resources support this workflow:

- `references/quota-check.md` — How to drive the Console with browser tools to
  read the **Quotas** page, the **Clusters** page, and **Capacity Finder**:
  navigation, what to extract, and how to report it.
- `references/manual-walkthrough.md` — The no-browser fallback: exactly what to
  tell the customer to click and read back, for each of the four questions.
- CoreWeave docs — Resource quotas:
  https://docs.coreweave.com/products/cks/clusters/quotas
- CoreWeave docs — How do I check my resource quota?:
  https://docs.coreweave.com/support/cks/articles/how-do-i-check-my-resource-quota
- CoreWeave docs — Capacity Finder:
  https://docs.coreweave.com/platform/capacity-plans/capacity-finder
- CoreWeave docs — Regions and Availability Zones:
  https://docs.coreweave.com/platform/regions/about-regions-and-azs
- CoreWeave docs — Instance availability matrix:
  https://docs.coreweave.com/platform/instances/availability-matrix
- CoreWeave docs — List clusters (CKS API):
  https://docs.coreweave.com/products/cks/reference/cks-api/clusterservice/list-clusters
