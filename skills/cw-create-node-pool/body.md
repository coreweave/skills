
# Add a node pool to an existing CKS cluster

You are helping a CoreWeave customer add a **node pool** (GPU or CPU compute) to a
CKS cluster that **already exists and is Running**. This is "Phase 2" of the
[CoreWeave reference architecture](https://github.com/coreweave/reference-architecture)
workflow, run on its own against a cluster you did not necessarily create in this
Terraform state.

> **Scope check.** If the customer does **not** yet have a cluster, stop and use the
> `cw-create-cluster` skill instead — it creates the VPC, cluster, and first node
> pool in one flow. This skill is only for adding compute to an existing cluster.

---

## Before you start

Confirm these prerequisites:

- The customer has a **running CKS cluster** (status **Running** in the Console).
- They have the **CKS Admin** role (or are an org administrator).
- They have a **CoreWeave API access token**. If they need one, walk them through the shared atomic below:

  {{include:create-api-token}}

- **Terraform >= 1.2** and **kubectl** are installed locally. Check with
  `terraform version` and `kubectl version --client`.
- They have available **node-type quota** for the instance types they want.
- They have a **downloaded kubeconfig** for the target cluster. Node pools are
  applied through the Terraform **Kubernetes provider**, which reads this same file
  — so the context selected in it decides which cluster receives the pool. This is
  the one step an agent cannot do autonomously today: there is no programmatic way
  to fetch an existing cluster's kubeconfig. If they need one, walk them through the
  shared atomic below (get the token first — it is embedded in the kubeconfig):

{{include:generate-kubeconfig}}

**The Cloud Console is at `console.coreweave.com`** (not `cloud.coreweave.com`).

---

## Step 1 — Confirm the target cluster and check node-type quota

Node pools are created as Kubernetes CRDs *on* a cluster, so the cluster must be
**Running** first. Confirm the cluster name and that it is healthy (Console →
Compute → Clusters → [cluster name]).

CoreWeave has no quota API or Terraform data source that can be read *before* a
pool exists. But quota is **not** invisible: once a NodePool is created, the
CRD's own status carries an explicit, machine-readable verdict. That status is
authoritative — trust it over anything a person reports from the Console.

### The authoritative check — the NodePool's `Quota` condition

```bash
kubectl get nodepool <pool-name> -o jsonpath='{range .status.conditions[?(@.type=="Quota")]}{.status} {.message}{"\n"}{end}'
```

Two outcomes matter:

| Output | Meaning | What to do |
|---|---|---|
| `True nodePool is under quota for instance type …` | The org holds quota for this type in this zone. | Proceed; wait for nodes. |
| `False quota limit is 0 for instance type …` (reason `NotSet`) | The org has **no quota at all** for this type here. | **Stop waiting.** No node will ever arrive. |

`reason: NotSet` is a hard zero, not a queue. Distinguish it from the `Capacity`
condition's `QueuedAwaitingCapacity`, which *does* resolve on its own — that one
means "you have quota, the zone is busy." Confusing the two costs a long wait
for a node that was never coming.

> **Zone availability is not org quota.** A zone page listing an instance type
> means CoreWeave offers it there, not that this org may provision one. In
> US-EAST-04A, for example, `gd-1xgh200` is the only single-GPU type on offer
> and is the obvious pick for a small model — and an org can hold exactly zero
> quota for it. Only the `Quota` condition answers the org-specific question.

### Check quota BEFORE committing to a type

`spec.instanceType` is **immutable** on an existing NodePool. Changing it fails
with `Invalid value: "…": InstanceType cannot be changed`, so a wrong first
guess means destroying the pool and recreating it, not editing it.

While `status.currentNodes` is `0` no node has been billed, so destroying an
empty pool costs nothing — that is what makes the probe cheap:

```bash
# safe while currentNodes is 0
terraform destroy -auto-approve -target='module.nodepool["<pool-name>"].kubernetes_manifest.nodepool[0]'
```

If the customer does not know which types their org holds, applying an empty
pool, reading the `Quota` condition, and destroying it is a legitimate and
inexpensive way to find out. Say what you are doing and why, rather than
silently cycling through types.

### Asking the customer, as a supplement

The Console's **Administration → Quotas** page is still useful for the whole
picture (how many of each type, across zones), and worth asking for when the
cluster does not exist yet:

> "Before we add the node pool, can you check **console.coreweave.com →
> Administration → Quotas** and tell me which GPU/CPU instance types you have
> quota for in the cluster's zone, and how many?"

Treat the answer as a starting hypothesis, not a verdict. If the customer says
they have quota and the `Quota` condition says `NotSet`, the condition is right.

---

## Step 2 — Gather node pool configuration

Collect for each node pool the customer wants:

| Field | Required | Default | Notes |
|-------|----------|---------|-------|
| **Pool name** | Yes | — | e.g., `gpu-pool`, `cpu-pool` |
| **Instance type** | Yes | — | e.g., `gd-8xh100ib-i128`, `cpu-4`. Must match quota. |
| **Node count** | Yes | — | Target number of nodes |
| **Autoscaling** | No | `false` | If true, also collect min and max nodes |

Cross-reference requested instance types against the quota from Step 1. Warn if the
customer is requesting more nodes than their quota allows.

---

## Step 3 — Configure Terraform for a node-pool-only apply

{{include:fetch-pinned-ref-arch}}

Only after `PINNED OK`, work from the Terraform directory:

```bash
cd /tmp/claude/cw-ref-arch/terraform
```

### Write terraform.tfvars

The reference architecture's `main.tf` instantiates the `network` (VPC) and `cks`
(cluster) modules **unconditionally** — only `create_nodepool` and `create_dfs_pvc`
are gated. So you must still supply valid values for the required cluster/VPC
variables even though you are not creating them, plus the kubeconfig path and the
node pool config. See `references/nodepool-reference.md` for the full variable
reference and example tfvars (single pool and multi-pool `nodepools` map).

```hcl
# Required even for a node-pool-only apply (the modules are evaluated):
zone               = "US-EAST-04A"
vpc_name           = "<existing-vpc-name>"
cluster_name       = "<existing-cluster-name>"
kubernetes_version = "v1.35"
vpc_prefixes       = [...]   # no default; reuse the reference-architecture values
host_prefixes      = [...]   # no default; reuse the reference-architecture values

# Node pool:
cks_kubeconfig_path = "/path/to/downloaded/kubeconfig"
create_nodepool     = true
create_dfs_pvc      = false
```

### Set the API token

Never write the token to tfvars — set it as an environment variable:

```bash
export TF_VAR_coreweave_api_token="<TOKEN>"
```

> **Checkpoint:** Show the generated `terraform.tfvars` to the customer and confirm
> before proceeding. (The cost gate comes at the plan checkpoint in Step 4, where
> the quantities are read from the plan itself.)

---

## Step 4 — Apply the node pool (targeted)

Because the cluster and VPC already exist (and you have no spare cluster quota to
recreate them), apply **only** the node pool module so Terraform does not try to
create a new VPC + cluster:

```bash
terraform init
terraform plan -target=module.nodepool
```

> **Checkpoint:** Show the plan. It should show only node pool creation (as
> `kubernetes_manifest` resources) — **no** `coreweave_networking_vpc` or
> `coreweave_cks_cluster`. Then gate the apply on all three of the following, and
> never proceed on a mismatch or an unverifiable context — fail closed, not open:
>
> 1. **Context check — run it now, not from memory.** Run
>    `kubectl config current-context` at this moment and include the resolved
>    context name verbatim in the confirmation message, e.g. "About to apply to
>    cluster: `<resolved-context>` — expected: `<existing-cluster-name>`". If the
>    resolved context does not exactly match the target cluster, or the command
>    errors, **STOP — do not run the apply.** Remediate per the fail-closed rule
>    in the kubeconfig section above (re-export `KUBECONFIG` and
>    `kubectl config use-context <existing-cluster-name>` in a single shell
>    call), re-run the check, and proceed only after it prints the target
>    cluster exactly.
> 2. **Cost.** State what this apply bills, with the quantities read from the
>    plan: "This creates N × `<instance-type>` GPU nodes — billed while running
>    regardless of load — and/or M × `<instance-type>` CPU nodes." GPU nodes are
>    sold whole: an `8x` SKU bills all 8 GPUs even if the workload uses one.
> 3. **Fresh, size-scaled confirmation.** The apply proceeds only on a fresh
>    customer reply to this gate message (the one carrying the context and cost
>    lines) — an earlier "yes" at the tfvars stage does not count. If the
>    request is large — more than **4 GPUs total** or more than **2 nodes** — a
>    bare "yes" is not enough: end the gate message by requesting the reply
>    format, e.g. "to proceed, reply with the quantity: yes, 8 nodes of
>    gd-8xh100ib-i128", so one compliant reply satisfies the gate. At or below
>    those thresholds, a plain fresh "yes" is fine.

```bash
terraform apply -target=module.nodepool -auto-approve
```

> A `-target`ed apply does **not** import the existing cluster into Terraform state.
> Keep using `-target=module.nodepool` for follow-up changes against this cluster — a
> plain `terraform apply` would try to create a new VPC + cluster.

> **Upstream enhancement request — [coreweave/reference-architecture](https://github.com/coreweave/reference-architecture).** Two changes would make "node pool only, against an existing cluster" a first-class, agent-drivable path: (1) `create_cluster` / `create_vpc` toggles that gate the `network` and `cks` modules, mirroring the existing `create_nodepool` / `create_dfs_pvc` flags; and (2) a kubeconfig data source (e.g. `data "coreweave_cks_cluster"`) or a `coreweave` CLI step that fetches an existing cluster's kubeconfig without a manual Console download. Until both land, the targeted apply above is the only option.

---

## Step 5 — Verify

Check the context first, on its own — output read through a mismatched or
unverifiable context describes the wrong cluster and must never be reported as
evidence:

```bash
kubectl config current-context     # must print <existing-cluster-name> exactly
```

If it prints anything else, or errors, re-establish the context (fail-closed
rule in the kubeconfig section above) before running the proof commands:

```bash
kubectl get nodepools
kubectl get nodes
```

Remind the customer:

- **Do NOT install the NVIDIA GPU Operator** — CoreWeave manages it on CKS. Manual
  installation causes conflicts and breakage.
- Node pools may take a few minutes to provision nodes after creation.

### (Optional) Confirm the new nodes are actually doing work

The checks above confirm the node pool and nodes **exist and are `Ready`**.
This optional step goes one step further and proves the GPUs are actually
being **utilized** by a workload — useful when the customer has (or is about
to run) a workload on the new pool and wants confirmation the hardware is
live. Skip it if they only wanted the capacity added. Nodes with no workload
scheduled yet will correctly report zero GPU utilization — that is expected,
not a failure.

Use the node pool's namespace/workload as the pod selector (or omit the pod
row if no workload is running on the new nodes yet).

{{include:verify-workload-health}}

---

## Common mistakes

**Creating the node pool on the wrong cluster.** Multi-cluster kubeconfigs are
common. Always `kubectl config use-context <existing-cluster-name>` and verify
with `kubectl config current-context` before applying — fail closed, per the
Step 4 checkpoint: never apply on a mismatched or unreadable context.

**Running before the cluster is Running.** Node pools are Kubernetes CRDs; if the
cluster isn't ready the Kubernetes provider can't connect and Terraform fails.

**Forgetting `-target=module.nodepool`.** A plain `terraform apply` tries to create a
new VPC + cluster (the modules aren't gated) and fails without cluster quota.

**Forgetting the API token.** If `TF_VAR_coreweave_api_token` isn't set,
`terraform apply -auto-approve` fails.

**Requesting instance types without quota.** Terraform accepts the config but the
node pool never provisions nodes. Cross-reference against quota from Step 1.

---

## References

- `references/nodepool-reference.md` — Node pool variables, single-pool and
  multi-pool (`nodepools` map) example tfvars, and verification commands.
- [Reference architecture repo](https://github.com/coreweave/reference-architecture)
- [CoreWeave Terraform provider](https://registry.terraform.io/providers/coreweave/coreweave/latest/docs)
- [CKS documentation](https://docs.coreweave.com/products/cks/clusters/introduction)
