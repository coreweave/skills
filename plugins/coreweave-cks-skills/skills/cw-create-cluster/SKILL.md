---
name: cw-create-cluster
description: >
  Programmatically creates a CKS (CoreWeave Kubernetes Service) cluster and VPC
  using the CoreWeave Terraform reference architecture. Covers quota checking,
  VPC creation, cluster provisioning, waiting for readiness, and node pool setup.
  Use this skill whenever a customer mentions creating a cluster, setting up CKS,
  provisioning Kubernetes infrastructure, deploying a new environment, or wants
  help with CoreWeave Terraform. Use when asked to create a cluster, create a CKS cluster, create a coreweave Kubernetes cluster.
  Also use it when someone asks about VPCs, node pools, Kubernetes
  versions, or cluster networking on CoreWeave.
---

# Create a CKS cluster with Terraform

You are helping a CoreWeave customer create a new CKS cluster programmatically using the [CoreWeave reference architecture](https://github.com/coreweave/reference-architecture). You will clone the repo, configure Terraform, run applies, and manage the two-phase workflow (Phase 1: VPC + cluster, Phase 2: node pools).

**Do as much as possible before asking.** Probe the local environment silently for Terraform, API tokens, existing clones, kubeconfig, and cluster state. Only ask the customer for information you cannot determine from local state.

---

## Before you start — silent environment probe

Run all of these checks silently before saying anything to the customer. Do not ask for permission to probe.

### 1. Check for Terraform

```bash
terraform version 2>/dev/null
```

If not installed, check for OpenTofu as an alternative:

```bash
tofu version 2>/dev/null
```

If neither is found, provide install instructions before proceeding:
- Terraform: https://developer.hashicorp.com/terraform/install
- OpenTofu: https://opentofu.org/docs/intro/install/

### 2. Check for API token

Look for an existing CoreWeave API token in common locations:

```bash
# Environment variables
echo "CW_TOKEN=${CW_TOKEN:-(not set)}"
echo "CW_TOKEN_PROD=${CW_TOKEN_PROD:-(not set)}"
echo "CW_CKS_TOKEN=${CW_CKS_TOKEN:-(not set)}"
echo "TF_VAR_coreweave_api_token=${TF_VAR_coreweave_api_token:-(not set)}"

# Token from kubeconfig
kubectl config view --minify -o jsonpath='{.users[0].user.token}' 2>/dev/null
```

### 3. Check kubeconfig for organization context

```bash
kubectl config current-context 2>/dev/null
kubectl config get-contexts 2>/dev/null
```

Note the organization and existing clusters — this helps suggest names and zones, and avoids duplicates.

### 4. Check for existing reference architecture clone

```bash
ls /tmp/claude/cw-ref-arch/terraform 2>/dev/null && echo "EXISTING_CLONE=true" || echo "EXISTING_CLONE=false"
```

### 5. Check for Helm (needed later for node pool verification)

```bash
helm version 2>/dev/null
```

### 6. Present findings and fill gaps

After all probes, present a brief summary:

> "Here's what I found:
> - **Terraform**: v1.9.x installed
> - **API token**: found in `CW_TOKEN` env var
> - **Organization**: `acme-corp` (from kubeconfig, with clusters: `use04a-dev`, `use04a-prod`)
> - **Reference architecture**: not yet cloned
>
> I need to check your quota before we proceed. Do you have access to the Console's Quotas page, or can you tell me your cluster and instance type limits?"

**The Cloud Console is at `console.coreweave.com`** (not `cloud.coreweave.com`).

---

## Step 1 — Check quota

CoreWeave has no quota API or Terraform data source. Quota must be checked before attempting cluster creation — otherwise Terraform will fail at apply time with a quota error.

### With browser tools

Probe for browser access silently. If connected, read `references/quota-check.md` and follow it to navigate to the Quotas page and extract:

- **Cluster quota** — how many clusters are allowed vs. how many exist.
- **Node type availability** — which GPU/CPU instance types have quota, and in which zones.
- **Zone availability** — which zones have capacity for the desired instance types.

Report findings to the customer so they can make informed choices in Step 2. If there is insufficient quota, advise them to request a quota increase from CoreWeave support before proceeding. Do not suggest pressing the button.

### Without browser tools

Ask the customer to check manually:

> "Before we create the cluster, I need your quota info. Go to **console.coreweave.com** → **Administration** → **Quotas**. I need:
> 1. How many clusters you're allowed (and how many you already have)
> 2. Which GPU/CPU instance types you have quota for
> 3. Which zones those instance types are available in"

---

## Step 2 — Gather configuration

Collect these details from the customer. Use what you learned from the environment probe to suggest sensible defaults.

| Field | Required | Default | Notes |
|-------|----------|---------|-------|
| **Cluster name** | Yes | — | Max 30 chars. Lowercase letters, numbers, hyphens. Suggest location-first naming like `use04a-prod`. Do not suggest names of clusters that already exist. |
| **Zone** | Yes | — | e.g., `US-EAST-04A`. Base this on quota findings from Step 1. |
| **Kubernetes version** | Yes | `v1.35` | Latest supported. Use this unless they need an older version. |
| **VPC name** | Yes | `<cluster_name>-vpc` | Derived from cluster name by default. |
| **API access** | Yes | Public | Private clusters require contacting CoreWeave support. |
| **VPC CIDRs** | No | Reference architecture defaults | Only ask if the customer has specific networking requirements. The defaults work for most deployments. |
| **OIDC / Auth webhooks** | No | None | Only ask if the customer mentions SSO, OIDC, or webhook auth. |

Ask all required questions at once — don't drip-feed them. For VPC CIDR defaults and sizing guidance, see `references/terraform-reference.md`.

---

## Step 3 — Set up Terraform

### Clone the reference architecture

```bash
git clone https://github.com/coreweave/reference-architecture.git /tmp/claude/cw-ref-arch
cd /tmp/claude/cw-ref-arch/terraform
```

If the repo is already cloned (detected in the environment probe), pull the latest instead of re-cloning.

### Write terraform.tfvars

Generate `terraform.tfvars` from the customer's inputs. Use the reference architecture defaults for any values the customer didn't specify. See `references/terraform-reference.md` for the full variable reference and example tfvars.

Phase 1 settings — always set these for the first apply:

```hcl
create_nodepool = false
create_dfs_pvc  = false
```

### Set the API token

Use the token found during the environment probe. If a token was found in an env var or kubeconfig, confirm with the customer:

> "I found a CoreWeave API token in `CW_TOKEN`. Should I use this for the `<org-name>` organization?"

If confirmed, set it:

```bash
export TF_VAR_coreweave_api_token="$CW_TOKEN"
```

If no token was found, ask the customer:

> "I need your CoreWeave API token. You can create one at **console.coreweave.com** → **Account Settings** → **API Access Tokens**."

Never write the token to tfvars.

> **Checkpoint:** Show the customer the generated `terraform.tfvars` and get confirmation before proceeding.

---

## Step 4 — Create VPC and cluster (Phase 1)

Run Terraform in sequence, pausing for customer approval after the plan:

```bash
terraform init
terraform plan
```

> **Checkpoint:** Show the plan output to the customer. Confirm they want to proceed before applying. The plan should show creation of a VPC (`coreweave_networking_vpc`) and a CKS cluster (`coreweave_cks_cluster`). No node pools or DFS resources should appear.

```bash
terraform apply -auto-approve
```

Phase 1 creates the VPC and starts cluster provisioning. The apply itself completes in a few minutes, but the cluster takes approximately **45 minutes** to become ready.

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

Check quota again because it may have been updated.

Once the cluster is running, ask the customer what node pools they need:

> "Your cluster is running. Now let's add compute capacity. Based on your quota, you have access to these instance types: [list from Step 1]. How many nodes of each type do you want?"

Collect for each node pool:

| Field | Required | Default | Notes |
|-------|----------|---------|-------|
| **Pool name** | Yes | — | e.g., `gpu-pool`, `cpu-pool` |
| **Instance type** | Yes | — | e.g., `gd-8xh100ib-i128`, `cpu-4`. Must match quota. |
| **Node count** | Yes | — | Target number of nodes |
| **Autoscaling** | No | `false` | If true, also collect min and max nodes |

Cross-reference requested instance types against the quota from Step 1. Warn if the customer is requesting more nodes than their quota allows.

---

## Step 7 — Create node pools (Phase 2)

### Set up kubeconfig

Check if the customer's kubeconfig already has a context for the new cluster (it may have been added automatically). If not, they need to download it from the Console:
**Console → Compute → Clusters → [cluster name] → Download kubeconfig**

```bash
kubectl config get-contexts | grep <CLUSTER_NAME>
```

If the context exists, switch to it. If not, ask for the kubeconfig path:

```bash
export KUBECONFIG=/path/to/downloaded/kubeconfig
```

### Select the correct kubectl context

A CoreWeave kubeconfig file often contains contexts for **multiple clusters**. Before creating node pools, you must switch to the context for the cluster you just created. Failing to do this will create node pools on the wrong cluster.

```bash
kubectl config get-contexts
kubectl config use-context <CLUSTER_NAME>
kubectl config current-context
```

Verify you're on the right cluster. The Terraform Kubernetes provider also uses this kubeconfig, so the active context determines where node pools are created.

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

> **Checkpoint:** Show the plan. It should show node pool creation (as `kubernetes_manifest` resources). Confirm before applying. Double-check that `kubectl config current-context` matches the target cluster.

```bash
terraform apply -auto-approve
```

Node pools are created as Kubernetes CRDs (`compute.coreweave.com/v1alpha1 NodePool`), which is why they require kubeconfig. The active kubectl context determines which cluster receives the node pools.

### After node pools are created

Verify the node pools were created on the correct cluster:

```bash
kubectl config current-context
kubectl get nodepools
```

Remind the customer:

- **Do NOT install the NVIDIA GPU Operator** — CoreWeave manages it. Manual installation causes conflicts.
- Node pools may take a few minutes to provision nodes after creation.
- They can verify with `kubectl get nodepools` and `kubectl get nodes`.

---

## Common mistakes

**Creating node pools on the wrong cluster**
CoreWeave kubeconfig files typically contain contexts for multiple clusters. If you don't switch to the correct context before Phase 2, node pools will be created on whichever cluster was previously active — not the one you just created. Always run `kubectl config use-context <CLUSTER_NAME>` and verify with `kubectl config current-context` before proceeding.

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
- [Reference architecture repo](https://github.com/coreweave/reference-architecture)
- [CoreWeave Terraform provider](https://registry.terraform.io/providers/coreweave/coreweave/latest/docs)
- [CKS documentation](https://docs.coreweave.com/products/cks/clusters/introduction)
- [VPC CIDR sizing](https://docs.coreweave.com/docs/products/networking/vpc/vpc-cidr)
