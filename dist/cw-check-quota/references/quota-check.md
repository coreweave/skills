# Read quota, clusters, and capacity via Console browser automation

Use this reference when browser tools are available and you're driving the
Cloud Console to answer the customer's orientation questions. There is **no
quota/usage/capacity API** — the Console UI is the only source for quota and
capacity numbers, so browser automation is how you read them without making the
customer copy values by hand.

This skill is **read-only**. You navigate and extract; you never create, scale,
or delete anything. If a page offers a **Create** or **Provision** button, note
it for the hand-off but do not click it.

---

## Core principles (same patterns as `cw-add-users`)

Follow the browser-automation conventions used across the Console skills:

1. **Tool-agnostic.** Use whatever browser toolset is connected (Claude in
   Chrome's `navigate` / `find` / `read_page`, Control Chrome, Playwright MCP,
   computer use). Prefer semantic locators (`text/`, `aria/`) over pixel
   coordinates — they survive UI refactors.
2. **Snapshot after each navigation** to get fresh element references and page
   text before you extract.
3. **Text anchors, not selectors.** Find the **Quotas** link, the **Clusters**
   link, the **Capacity finder** tab by their visible labels.
4. **Hand off at login/2FA.** If you hit a sign-in page, 2FA, or CAPTCHA, stop
   and ask the customer to sign in, then continue.
5. **Fail loud, fail safe.** If an element isn't found, screenshot, describe
   what's on screen, and ask the customer — never click blindly.

**Orient first.** Take a snapshot and confirm you're on `console.coreweave.com`
and signed in. If the customer belongs to more than one organization, confirm
which org is active before reading anything — quota is per-org. Prefer the
sidebar over typing admin URLs directly; the reliable landing page is
`console.coreweave.com/clusters`.

---

## Read the Clusters page (Question 1: what exists, and where)

1. In the left sidebar, click **Clusters** (or navigate to
   `console.coreweave.com/clusters`).
2. Snapshot / read the page.
3. For each cluster in the list, extract:
   - **Name**
   - **Zone / Availability Zone** (e.g. `US-EAST-04A`)
   - **Status** (e.g. Running)
4. If the list is empty, that's a valid result — the org has no clusters yet.

This anchors every later number to a zone. (Clusters are also listable via the
CKS API / `cwic` — see the main SKILL.md — but reading the page is fine and
keeps the customer's mental model tied to what they see.)

---

## Read the Quotas page (Question 2: allowed vs. used)

1. In the left sidebar, click **Administration**, then **Quotas** (or navigate
   directly to `console.coreweave.com/organization/quotas`). You can browse all
   quotas or use the page's search to find a specific one.
2. Snapshot to read the page content.

### What to extract

**Cluster quota**
- **Maximum clusters allowed** — total cluster quota for the org.
- **Clusters currently in use** — how many exist.
- **Remaining** — allowed minus used.

If remaining is 0, the customer must delete a cluster or request a quota
increase (support ticket) before creating another.

**Node-type / instance-type quota**
For each instance type shown (e.g. `gd-8xh100ib-i128`, a CPU type):
- **Instance type** name.
- **Quota (allowed)** per type.
- **Currently in use**.
- **Remaining**.
- **Zone(s)** the quota applies to, if the page shows them.

<!-- TODO(SME): verify the exact column labels and layout of the Quotas page
     (allowed / used / remaining, and whether zone is shown per row). The docs
     screenshot confirms the page exists at /organization/quotas and is
     browse/searchable, but does not enumerate the column headers. -->

### If the page paginates or the layout changed

- The instance-type list may paginate — check for pagination controls and read
  every page.
- If a type requires scrolling, scroll and snapshot again.
- If expected elements aren't found: screenshot, look for tabs or filters that
  might split cluster quota from compute quota, and if quota still can't be
  found, fall back to asking the customer to read the values from
  `references/manual-walkthrough.md`.

---

## Drive Capacity Finder (Question 3: does the zone have capacity now)

Quota ≠ capacity. Capacity Finder tells you whether a zone can physically place
the nodes right now.

1. In the sidebar, open the **Compute** page and select the **Capacity finder**
   tab.
2. Fill at least one row (each row is an instance type + a Node count):
   - a **GPU instance type** with a **Node count**, and/or
   - a **CPU instance type** with a **Node count**.
   Use `form_input` / your tool's set-value action for the fields rather than
   click-then-type.
3. Click **Find capacity**.
4. Snapshot and read the **Zone cards**. For each, extract:
   - **Zone name**.
   - The **availability label** on the GPU/CPU tag — one of:
     *Likely available*, *Possibly available*, *Limited availability*,
     *Not available*, *SKU unavailable*.
   - Whether the customer already operates a cluster in that zone (or
     **Cluster setup required**).
   - Note the action button (**Provision capacity** or **Create cluster**) for
     the hand-off — **do not click it** in this read-only skill.

State the caveats to the customer: Capacity Finder is informational and
Spot-oriented — it covers **General Access zones only**, **excludes** Superchip
types (GH200, GB200, GB300), uses **cached** samples (not live inventory), and
does **not** guarantee a reservation.

---

## Reporting findings

Join everything into one orientation summary. Example:

```
Organization: acme-ai   (active org confirmed)

Clusters:
| Cluster        | Zone (AZ)     | Status  |
|----------------|---------------|---------|
| prod-training  | US-EAST-04A   | Running |

Cluster quota: 1 of 3 used (2 remaining)

Node-type quota:
| Instance type      | Allowed | Used | Free | Zone        |
|--------------------|---------|------|------|-------------|
| gd-8xh100ib-i128   | 16      | 8    | 8    | US-EAST-04A |

Capacity (finder, informational):
| Zone        | H100 x2      | You have a cluster? |
|-------------|--------------|---------------------|
| US-EAST-04A | Likely avail | Yes                 |
| US-WEST-01A | Possibly     | No                  |

Recommendation: you have 8 free H100 nodes of quota in US-EAST-04A and that
zone shows capacity — you can add a node pool to prod-training there. Next
step: cw-create-cluster (node pool provisioning).
```

Then hand off per Step 4 of the main SKILL.md.

---

## What not to do

- **Don't** click **Create cluster**, **Provision capacity**, or any
  save/submit control. This skill only reads.
- **Don't** navigate to admin pages by guessing URLs beyond the two confirmed
  ones (`/clusters`, `/organization/quotas`) and the Compute page — use the
  sidebar; some paths redirect or 404 by Console version.
- **Don't** claim a quota/usage/capacity API exists. There isn't one.
