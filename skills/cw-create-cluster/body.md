
# Create a CKS cluster with Terraform

You are helping a CoreWeave customer create a new CKS cluster programmatically using the [CoreWeave reference architecture](https://github.com/coreweave/reference-architecture). You will clone the repo, configure Terraform, run applies, and manage the two-phase workflow (Phase 1: VPC + cluster, Phase 2: node pools).

---

## Before you start

Confirm these prerequisites:

- The customer has a **CoreWeave account** and can sign in to `console.coreweave.com`.
- They have the **CKS Admin** role (or are an org administrator).
- They have a **CoreWeave API access token**. If they need one, walk them through the shared atomic below:

  {{include:create-api-token}}

- **Terraform >= 1.2** is installed locally. Check with `terraform version`.
- They have available **cluster quota**. Use Step 1 to check this.

**The Cloud Console is at `console.coreweave.com`** (not `cloud.coreweave.com`).

---

## Step 1 — Check quota

CoreWeave has no quota API or Terraform data source. Quota must be checked via the Console UI before attempting cluster creation — otherwise Terraform will fail at apply time with a quota error.

### With browser tools

Probe for browser access silently. If connected, read `references/quota-check.md` and follow it to navigate to the Quotas page and extract:

- **Cluster quota** — how many clusters are allowed vs. how many exist.
- **Node type availability** — which GPU/CPU instance types have quota, and in which zones.
- **Zone availability** — which zones have capacity for the desired instance types.

Report findings to the customer so they can make informed choices in Step 2. If there is insufficient quota, advise them to request a quota increase from CoreWeave support before proceeding. Do not suggest pressing the button. 

### Without browser tools

Ask the customer to check manually:

> "Before we create the cluster, can you check your quota? Go to **console.coreweave.com**, then **Administration** → **Quotas**. I need to know:
> 1. How many clusters you're allowed (and how many you already have)
> 2. Which GPU/CPU instance types you have quota for
> 3. Which zones those instance types are available in"

---

## Step 2 — Gather configuration

Collect these details from the customer. Suggest sensible defaults where noted.

| Field | Required | Default | Notes |
|-------|----------|---------|-------|
| **Cluster name** | Yes | — | Max 30 chars. Lowercase letters, numbers, hyphens. Suggest location-first naming like `use04a-prod`. Do not suggest names of clusters that already exist|
| **Zone** | Yes | — | e.g., `US-EAST-04A`. Base this on quota findings from Step 1. |
| **Kubernetes version** | Yes | `v1.35` | Latest supported. Use this unless they need an older version. |
| **VPC name** | Yes | `<cluster_name>-vpc` | Derived from cluster name by default. This module always **creates** a VPC — see below if the customer wants an existing one. |
| **API access** | Yes | Public | Private clusters require contacting CoreWeave support. |
| **VPC CIDRs** | No | Reference architecture defaults | Only ask if the customer has specific networking requirements. The defaults work for most deployments. |
| **OIDC / Auth webhooks** | No | None | Only ask if the customer mentions SSO, OIDC, or webhook auth. |

For VPC CIDR defaults and sizing guidance, see `references/terraform-reference.md`.

### If the customer wants to reuse an existing VPC

Say so plainly rather than improvising: **this path cannot attach a new cluster to
an existing VPC.** The reference architecture exposes only `vpc_name` and
`vpc_prefixes`, both of which feed a VPC it creates itself. There is no
`create_vpc = false`, and no data source for looking one up. Setting `vpc_name` to
an existing VPC's name does not adopt it — the apply fails on the name already
being taken.

Offer the customer the two real options:

1. **Create a new VPC** (the default, and almost always the right answer). VPCs
   carry no compute cost, and a cluster-scoped VPC keeps the blast radius of a
   later `terraform destroy` contained.
2. **Adopt the existing VPC into Terraform state** with
   `terraform import`, then apply. This makes the existing VPC a managed resource,
   which means a later destroy will delete it — including for anything else
   already using it. Only suggest this when the customer explicitly wants that
   VPC and understands that consequence.

Do not silently pick option 2 for them.

---

## Step 3 — Set up Terraform

{{include:fetch-pinned-ref-arch}}

Only after `PINNED OK`, work from the Terraform directory:

```bash
cd /tmp/claude/cw-ref-arch/terraform
```

### Write terraform.tfvars

Write this complete file, substituting the customer's cluster name, VPC name, and zone. Every variable below is required, so a shorter file fails at `terraform plan`. Keep the CIDR values as they are unless the customer asked for something specific.

```hcl
cluster_name       = "<CLUSTER-NAME>"
vpc_name           = "<VPC-NAME>"
zone               = "<ZONE>"
kubernetes_version = "v1.35"

# Phase 1 only. Node pools come later, in Step 7.
create_nodepool = false
create_dfs_pvc  = false

vpc_prefixes = [
  { name = "pod cidr",         value = "10.0.0.0/13" },
  { name = "service cidr",     value = "10.16.0.0/22" },
  { name = "internal lb cidr", value = "10.32.4.0/22" },
]

host_prefixes = [
  { name = "primary", type = "PRIMARY", prefixes = ["10.16.192.0/18"] }
]
```

Three things about this file reject the apply if you get them wrong:

- `vpc_prefixes` needs at least three entries, and order is positional: index 0 becomes the pod CIDR, index 1 the service CIDR, and index 2 and beyond the internal load balancer CIDRs.
- `host_prefixes` must contain at least one entry. The reference architecture's own module comment says you can leave it empty to pick up the zone default, and that is wrong. An empty set fails validation.
- `type` must be `PRIMARY`, `ROUTED`, or `ATTACHED`, uppercase. Anything else fails at apply time, not plan time, so it costs you a full round trip.

For the full variable reference, optional variables, and CIDR sizing guidance, see `references/terraform-reference.md`. Read it with a path relative to this skill's own directory, not relative to the cloned reference architecture.

### Set the API token

`coreweave_api_token` is a required variable with no default, so **`terraform
plan` fails without it** — this is not a step you can skip and fix later.

Do not guess at variable names. Search by pattern, and search a login shell as
well as your own environment: a token exported from `~/.zshrc` or `~/.bash_profile`
is **not** present in a non-interactive shell, so plain `env` finds nothing even
when the customer does have one set.

```bash
# Prints only the NAME of any CoreWeave-looking token variable, never its value.
{ env; zsh -ic env 2>/dev/null; bash -lc env 2>/dev/null; } \
  | grep -oE '^[A-Za-z_]*(CW|COREWEAVE)[A-Za-z_]*(TOKEN|API_KEY)[A-Za-z_]*' \
  | sort -u
```

If that finds one or more names, show the customer the **names** and ask which to
use. Never echo, print, or paste a token value, and never ask the customer to
paste one into the chat — have them export it in their own shell instead. If the
search finds nothing, ask the customer to export a token before continuing.

Identify the organization the customer is using in all messages. Set the token as
an environment variable — never write it to tfvars:

```bash
# Substitute the variable name you found above.
export TF_VAR_coreweave_api_token="$CW_API_TOKEN"
```

Confirm it is set without revealing it:

```bash
[ -n "$TF_VAR_coreweave_api_token" ] && echo "token is set" || echo "NOT set — terraform plan will fail"
```

> **Checkpoint:** Show the customer the generated `terraform.tfvars` and get confirmation before proceeding.

---

## Step 4 — Create VPC and cluster (Phase 1)

Run Terraform in sequence, pausing for customer approval after the plan:

```bash
terraform init
terraform plan
```

> **Checkpoint:** Show the plan output to the customer. Confirm they want to proceed before applying. The plan should show creation of a VPC (`coreweave_networking_vpc`) and a CKS cluster (`coreweave_cks_cluster`). No node pools or DFS resources should appear. State the cost in the same message: nothing in this plan is billed as GPU compute — the VPC carries no compute cost, and no GPU nodes or public IPs are created in this phase. If the plan shows anything beyond the VPC and cluster, stop and re-check the tfvars instead of applying.

Run the apply in the background and poll the log. Do not run it in the foreground: it routinely outruns the default tool timeout, and a killed apply can leave a VPC or cluster created in CoreWeave but absent from Terraform state, where `terraform destroy` will not clean it up.

```bash
terraform apply -auto-approve > /tmp/claude/cw-apply.log 2>&1 &
echo $! > /tmp/claude/cw-apply.pid
```

Poll until the process exits, then read the result:

```bash
kill -0 "$(cat /tmp/claude/cw-apply.pid)" 2>/dev/null && echo RUNNING || echo DONE
tail -20 /tmp/claude/cw-apply.log
```

Phase 1 creates the VPC and starts cluster provisioning. The apply itself completes in a few minutes, but the cluster takes approximately **45 minutes** to become ready.

If an apply was interrupted before you took over, reconcile state before applying again. `terraform state list` shows what Terraform knows about, and the Console shows what actually exists. Anything present in the Console but missing from state needs `terraform import` or manual deletion, because a fresh apply will fail on the name already being taken.

---

## Step 5 — Wait for cluster readiness

After `terraform apply` completes, the cluster is in **Creating** state. Monitor it until it reaches **Running**.

### Polling approach

Run `terraform plan` periodically — it reads the cluster's current status from the API. The cluster status appears in the plan output or can be checked via:

```bash
terraform output cks_status
```

Poll every 3–5 minutes. Report status updates to the customer periodically.

### Alternative: Console

The customer can also watch progress at:
**Console → Compute → Clusters → [cluster name]**

The status transitions: Creating → Running (healthy) or Unhealthy (investigate).

> **Important:** Do not proceed to Phase 2 until the cluster status is **Running**.

---

## Step 6 — Prompt for node pools

Reuse the quota you already read in Step 1. Only re-read it if this run created or
deleted a node pool since then, or if Step 1 was skipped.

### First, establish what the cluster is for

The answer changes which pools belong in the same Phase 2 apply, so settle it
**before** instance types.

**Read the customer's stated goal first — do not just ask.** If they have
already said anywhere in the conversation that this cluster is for an inference
endpoint, a served model, a web service, TLS, or anything "reachable", the
answer is yes and you already have it. Only ask when the goal is genuinely
unstated:

> "Before we size the compute — will this cluster serve network traffic? For
> example an inference endpoint, a web service, or anything reachable over
> HTTP/HTTPS."

> **A pool list in the request does not settle this.** A customer who says
> "create a cluster and add a small GPU node pool, then deploy an inference
> endpoint" has specified the GPU pool and *implied* the CPU pool. Treat the
> stated list as under-specified and correct it, out loud — "you'll also need a
> small CPU pool for the ingress controller, so I'm adding one to the same
> apply." Silently building exactly the GPU-only pool list they typed is how a
> run ends up an hour later with no reachable endpoint.

**If yes, the cluster needs a CPU node pool in addition to the GPU pool, and it
should go in this same apply.** Ingress on CKS runs Traefik, whose node affinity
refuses GPU nodes. A GPU-only cluster leaves Traefik `Pending` forever and
produces no reachable endpoint, so `cw-self-managed-inference` will stop and send
the customer back here for a second apply. Adding it now costs one pool; adding it
later costs another plan/apply cycle, another quota check, and another kubeconfig
context round trip.

Confirm CPU quota specifically before promising it — CPU SKUs are quota-gated
separately from GPU, and an org can hold GPU quota with **zero** CPU quota. If
there is no CPU quota, say so plainly and tell the customer that ingress cannot
work until they request some. Do not quietly proceed to build a GPU-only cluster
for a customer who told you they want an endpoint.

### Then collect the pools

> "Your cluster is running. Now let's add compute capacity. Based on your quota,
> you have access to these instance types: [list from Step 1]. How many nodes of
> each type do you want?"

| Field | Required | Default | Notes |
|-------|----------|---------|-------|
| **Pool name** | Yes | — | e.g., `gpu-pool`, `cpu-pool` |
| **Instance type** | Yes | — | Use an exact SKU from the customer's own quota table, e.g. `gd-8xl40-i128` or `gd-8xh100ib-i128` for GPU, `cd-hc-a384ib-genoa` or `turin-gp-l` for CPU. Do not invent short names like `cpu-4`; instance types are zone-specific and must match quota exactly. |
| **Node count** | Yes | — | Target number of nodes. GPU nodes are sold whole — an `8x` SKU bills all 8 GPUs even if the workload uses one. |
| **Autoscaling** | No | `false` | If true, also collect min and max nodes |

Cross-reference requested instance types against the quota from Step 1. Warn if the customer is requesting more nodes than their quota allows.

Multiple pools go in **one** Phase 2 apply through the `nodepools` map — see
`references/terraform-reference.md` for that format. Do not apply them one at a
time.

---

Phase 2 runs through the Terraform **Kubernetes provider**, so it needs a kubeconfig
for the cluster Phase 1 just created. This is the first point in the workflow where
one can exist — the download is per-cluster, so it could not have been done up front.
Walk the customer through the shared atomic below, then continue to Step 7.

{{include:generate-kubeconfig}}

> The Terraform Kubernetes provider reads this same kubeconfig, so the context
> selected above is what decides where the node pools are created. Getting it wrong
> puts them on whichever cluster was previously active.

---

## Step 7 — Create node pools (Phase 2)

> **Scope.** This step covers node pools on the cluster **you just created in this same Terraform run**, so a plain `terraform apply` is correct. To add a node pool to a **pre-existing** cluster — one created earlier, or outside this Terraform state — stop here and use the `cw-create-node-pool` skill instead; it needs a targeted apply and a hand-downloaded kubeconfig.

### Update terraform.tfvars

Set `create_nodepool = true`, the kubeconfig path, and the node pool configuration:

```hcl
cks_kubeconfig_path = "/path/to/kubeconfig"
create_nodepool     = true
```

For a single node pool, set the `nodepool_*` variables. For multiple pools, use the `nodepools` map. See `references/terraform-reference.md` for both formats.

### Apply Phase 2

```bash
terraform plan
```

> **Checkpoint:** Show the plan. It should show node pool creation (as `kubernetes_manifest` resources). Then gate the apply on all three of the following, and never proceed on a mismatch or an unverifiable context — fail closed, not open:
>
> 1. **Context check — run it now, not from memory.** Run `kubectl config current-context` at this moment and include the resolved context name verbatim in the confirmation message, e.g. "About to apply to cluster: `<resolved-context>` — expected: `<CLUSTER_NAME>`". If the resolved context does not exactly match the target cluster, or the command errors, **STOP — do not run the apply.** Remediate per the fail-closed rule in the kubeconfig section above (re-export `KUBECONFIG` and `kubectl config use-context <CLUSTER_NAME>` in a single shell call), re-run the check, and proceed only after it prints the target cluster exactly.
> 2. **Cost.** State what this apply bills, with the quantities read from the plan: "This creates N × `<instance-type>` GPU nodes — billed while running regardless of load — and M × `<instance-type>` CPU nodes." GPU nodes are sold whole: an `8x` SKU bills all 8 GPUs even if the workload uses one.
> 3. **Fresh, size-scaled confirmation.** The apply proceeds only on a fresh customer reply to this gate message (the one carrying the context and cost lines) — an earlier "yes" at the tfvars stage does not count. If the request is large — more than **4 GPUs total** or more than **2 nodes** — a bare "yes" is not enough: end the gate message by requesting the reply format, e.g. "to proceed, reply with the quantity: yes, 8 nodes of gd-8xh100ib-i128", so one compliant reply satisfies the gate. At or below those thresholds, a plain fresh "yes" is fine.

```bash
terraform apply -auto-approve
```

Node pools are created as Kubernetes CRDs (`compute.coreweave.com/v1alpha1 NodePool`), which is why they require kubeconfig. The active kubectl context determines which cluster receives the node pools.

### After node pools are created

Verify the node pools were created on the correct cluster. Check the context
first, on its own — output read through a mismatched or unverifiable context
describes the wrong cluster and must never be reported as evidence:

```bash
kubectl config current-context   # must print the target cluster exactly
```

If it prints anything else, or errors, re-establish the context (fail-closed
rule in the kubeconfig section above) before running the proof command:

```bash
kubectl get nodepools
```

Remind the customer:

- **Do NOT install the NVIDIA GPU Operator** — CoreWeave manages it. Manual installation causes conflicts.
- Node pools may take a few minutes to provision nodes after creation.
- They can verify with `kubectl get nodepools` and `kubectl get nodes`.

---

## Common mistakes

**Creating node pools on the wrong cluster**
CoreWeave kubeconfig files typically contain contexts for multiple clusters. If you don't switch to the correct context before Phase 2, node pools will be created on whichever cluster was previously active — not the one you just created. Always run `kubectl config use-context <CLUSTER_NAME>` and verify with `kubectl config current-context` before proceeding — fail closed, per the Step 7 checkpoint: never apply on a mismatched or unreadable context.

**Trying to run Phase 2 before the cluster is Running**
Node pools are Kubernetes CRDs. If the cluster isn't ready, the Kubernetes provider can't connect and Terraform will fail.

**Forgetting to set the API token**
If `TF_VAR_coreweave_api_token` isn't set, `terraform plan` will prompt for it interactively — but `terraform apply -auto-approve` will fail. Always set it as an environment variable before running any Terraform commands.

**Requesting instance types without quota**
Terraform will accept the configuration but the node pool will fail to provision nodes. Always cross-reference against quota from Step 1.

**Choosing the wrong zone**
The VPC and cluster must be in the same zone. Instance types are zone-specific — not all types are available in all zones.

**Installing NVIDIA GPU Operator manually**
CoreWeave manages GPU drivers and operators on CKS. Manual installation causes conflicts and breakage.

**Running terraform destroy without understanding the blast radius**
Destroying the cluster also destroys all node pools, workloads, and data on it. Always confirm with the customer before any destroy operation.

---

## References

- `references/terraform-reference.md` — Full variable reference, example tfvars, CIDR guidance, module structure, and Terraform outputs.
- `references/quota-check.md` — Browser automation for checking cluster and node type quota via the Console Quotas page.
- `cw-create-node-pool` skill — Adding a node pool to a cluster that already exists (targeted apply against a cluster not in this Terraform state).
- [Reference architecture repo](https://github.com/coreweave/reference-architecture)
- [CoreWeave Terraform provider](https://registry.terraform.io/providers/coreweave/coreweave/latest/docs)
- [CKS documentation](https://docs.coreweave.com/products/cks/clusters/introduction)
- [VPC CIDR sizing](https://docs.coreweave.com/docs/products/networking/vpc/vpc-cidr)
