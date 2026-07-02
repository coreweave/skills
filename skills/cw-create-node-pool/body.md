
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
- They have a **downloaded kubeconfig** for the target cluster (Step 2 — this is the
  one step an agent cannot do autonomously today).
- They have available **node-type quota** for the instance types they want.

**The Cloud Console is at `console.coreweave.com`** (not `cloud.coreweave.com`).

---

## Step 1 — Confirm the target cluster and check node-type quota

Node pools are created as Kubernetes CRDs *on* a cluster, so the cluster must be
**Running** first. Confirm the cluster name and that it is healthy (Console →
Compute → Clusters → [cluster name]).

CoreWeave has no quota API or Terraform data source, so node-type quota must be
checked via the Console UI — otherwise the node pool is accepted but never
provisions nodes.

### With browser tools

Probe for browser access silently. If connected, navigate to **Administration →
Quotas** and extract, for the cluster's zone:

- **Node type availability** — which GPU/CPU instance types have quota.
- **How many of each** the customer can still provision.

### Without browser tools

Ask the customer to check manually:

> "Before we add the node pool, can you confirm your quota? Go to
> **console.coreweave.com → Administration → Quotas** and tell me which GPU/CPU
> instance types you have quota for in the cluster's zone, and how many."

---

## Step 2 — Get the cluster's kubeconfig (manual step)

Node pools are applied through the Terraform **Kubernetes provider**, which needs a
kubeconfig for the target cluster. There is **no programmatic way** to fetch an
existing cluster's kubeconfig — no `data "coreweave_cks_cluster"` source, no
`coreweave` CLI command, no Terraform output. It must be downloaded by hand:

**Console → Compute → Clusters → [cluster name] → Download kubeconfig**

Ask the customer for the path where they saved it:

```bash
export KUBECONFIG=/path/to/downloaded/kubeconfig
```

> This is the current limitation of the node-pool-on-existing-cluster path: an
> autonomous agent cannot complete it without the customer performing this download.
> Pause here and have the customer provide the kubeconfig path before continuing.

---

## Step 3 — Select the correct kubectl context

A CoreWeave kubeconfig often contains contexts for **multiple clusters**. You must
switch to the context for the target cluster before applying, or the node pool will
be created on the wrong cluster.

```bash
kubectl config get-contexts
kubectl config use-context <CLUSTER_NAME>
kubectl config current-context        # verify it matches the target cluster
```

The Terraform Kubernetes provider uses this same kubeconfig, so the active context
determines where the node pool is created.

---

## Step 4 — Gather node pool configuration

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

## Step 5 — Configure Terraform for a node-pool-only apply

### Clone the reference architecture

```bash
git clone https://github.com/coreweave/reference-architecture.git /tmp/claude/cw-ref-arch
cd /tmp/claude/cw-ref-arch/terraform
```

If it is already cloned from a previous run, pull the latest instead.

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
> before proceeding.

---

## Step 6 — Apply the node pool (targeted)

Because the cluster and VPC already exist (and you have no spare cluster quota to
recreate them), apply **only** the node pool module so Terraform does not try to
create a new VPC + cluster:

```bash
terraform init
terraform plan -target=module.nodepool
```

> **Checkpoint:** Show the plan. It should show only node pool creation (as
> `kubernetes_manifest` resources) — **no** `coreweave_networking_vpc` or
> `coreweave_cks_cluster`. Re-confirm `kubectl config current-context` points at the
> target cluster, then apply.

```bash
terraform apply -target=module.nodepool -auto-approve
```

> A `-target`ed apply does **not** import the existing cluster into Terraform state.
> Keep using `-target=module.nodepool` for follow-up changes against this cluster — a
> plain `terraform apply` would try to create a new VPC + cluster.

---

## Step 7 — Verify

```bash
kubectl config current-context     # confirm the right cluster
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
common. Always `kubectl config use-context <CLUSTER_NAME>` and verify with
`kubectl config current-context` before applying.

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
