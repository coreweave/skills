# Manual walkthrough: read quota, clusters, and capacity by hand

Use this reference when **no browser tools are available** (or the customer
prefers to click through themselves). Because CoreWeave has **no
quota/usage/capacity API**, this is the honest fallback: guide the customer
through the Console and have them read values back to you, then you assemble the
orientation summary.

Keep it conversational and one step at a time. Ask the customer to sign in to
**`console.coreweave.com`** first. Everything here is **read-only** — the
customer is only looking, not creating.

---

## Question 1 — What clusters do I already have, and where?

Ask the customer to:

1. Click **Clusters** in the left sidebar (or go to
   `console.coreweave.com/clusters`).
2. Read back, for each cluster: its **name**, its **zone / Availability Zone**
   (like `US-EAST-04A`), and its **status**.

If the list is empty, that's expected for a new org — note it and move on.

> If the customer has a CoreWeave API access token and is comfortable with a
> terminal, they can instead run:
> ```bash
> curl -s https://api.coreweave.com/v1beta1/cks/clusters \
>   -H "Authorization: Bearer $CW_API_ACCESS_TOKEN"
> ```
> (or `cwic cluster get`). This returns each cluster's name, zone, and VPC — but
> **not** quota. Quota is still Console-only.

---

## Question 2 — What am I allowed, and how much is used?

Ask the customer to:

1. Click **Administration** in the left sidebar, then **Quotas** (or go to
   `console.coreweave.com/organization/quotas`).
2. Read back the **cluster quota**: how many clusters are allowed vs. how many
   are in use.
3. For each **instance / node type** listed (GPU types like `gd-8xh100ib-i128`,
   plus CPU types), read back: the **allowed** count, the **used** count, and
   the **remaining** count. They can use the page's search box to find a
   specific type.

<!-- TODO(SME): verify the exact on-screen labels a customer will see for
     allowed / used / remaining on the Quotas page, and whether each row shows
     its zone. Phrase the questions to the customer using whatever the UI
     actually calls these once confirmed. -->

If any type shows **0 remaining**, tell the customer plainly that raising quota
requires a **support ticket** (there is no API): they log in to CoreWeave
Freshdesk and submit a request. See the quota docs linked in the main SKILL.md.

---

## Question 3 — Does the zone actually have capacity right now?

Quota is your allowance; capacity is whether the zone can place nodes today.
Ask the customer to:

1. Open the **Compute** page in the sidebar and click the **Capacity finder**
   tab.
2. Enter an **instance type** (GPU and/or CPU) and a **Node count**, then click
   **Find capacity**.
3. Read back, for each **Zone card**: the **zone name**, the **availability
   label** (*Likely available*, *Possibly available*, *Limited availability*,
   *Not available*, or *SKU unavailable*), and whether it says they already have
   a cluster there.

Remind them Capacity Finder is **informational and Spot-oriented**: General
Access zones only, no Superchips (GH200 / GB200 / GB300), cached (not live)
data, and no reservation guarantee.

If they'd rather just confirm *where a GPU type is offered at all* (before
checking live capacity), point them at the **Instance availability matrix** and
per-zone instance lists in the docs (linked in the main SKILL.md).

---

## Question 4 — Put it together and decide the next move

Read the assembled summary back to the customer: clusters (and zones), cluster
quota used/remaining, node-type quota used/free per zone, and any capacity
signal. Then recommend one next step and offer to hand off:

- **No clusters yet** → offer `cw-create-cluster`.
- **Free quota + capacity in a zone** → offer `cw-create-cluster` (new cluster
  or a node pool in an existing one).
- **Quota exhausted / zone full** → point them at the Freshdesk quota-increase
  ticket, and/or an alternate zone that showed capacity.
- **They need team access, not compute** → offer `cw-add-users`.

Reminder to give the customer: this was orientation only — nothing was changed.
