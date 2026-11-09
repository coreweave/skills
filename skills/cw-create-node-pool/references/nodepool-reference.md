# Node pool Terraform reference

Variable reference and example tfvars for adding a node pool to an **existing**
CKS cluster with the [CoreWeave reference architecture](https://github.com/coreweave/reference-architecture).

## Provider configuration

The root `providers.tf` configures two providers; the Kubernetes provider is the
one that matters for node pools:

```hcl
provider "coreweave" {
  token = var.coreweave_api_token       # set via env: TF_VAR_coreweave_api_token
}

provider "kubernetes" {
  config_path = var.cks_kubeconfig_path  # the kubeconfig you downloaded from the Console
}
```

Required provider versions: `coreweave/coreweave` >= 0.3.0, `hashicorp/kubernetes`
>= 2.23.0, Terraform >= 1.2.0.

## Variables you must still supply

`main.tf` instantiates the `network` and `cks` modules unconditionally, so even a
node-pool-only (`-target=module.nodepool`) apply evaluates these required variables.
Set them to the **existing** cluster's values:

| Variable | Type | Notes |
|----------|------|-------|
| `zone` | string | The existing cluster's zone, e.g. `"US-EAST-04A"` |
| `vpc_name` | string | The existing VPC name |
| `cluster_name` | string | The existing cluster name (max 30 chars) |
| `kubernetes_version` | string | e.g. `"v1.35"` |
| `cks_kubeconfig_path` | string | Path to the downloaded kubeconfig |
| `create_nodepool` | bool | Must be `true` |
| `create_dfs_pvc` | bool | `false` unless adding DFS storage |
| `vpc_prefixes` | list | Required — the module evaluates it even under `-target` |
| `host_prefixes` | list | Required — the module evaluates it even under `-target` |

> `vpc_prefixes` / `host_prefixes` have no defaults. Reuse the
> reference-architecture defaults if you don't have the cluster's original values;
> they are not applied to the existing VPC, but Terraform still needs them to
> evaluate the `network` module.

## Node pool variables

### Single node pool

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `nodepool_name` | string | `"example-nodepool"` | NodePool name |
| `nodepool_instance_type` | string | `"gd-8xh100ib-i128"` | CKS instance type (must match quota) |
| `nodepool_target_nodes` | number | `2` | Desired node count |
| `nodepool_autoscaling` | bool | `false` | Enable autoscaling |
| `nodepool_min_nodes` | number | `0` | Min nodes (autoscaling) |
| `nodepool_max_nodes` | number | `0` | Max nodes (autoscaling) — the confirmation gate sizes an autoscaling pool at this ceiling, not `nodepool_target_nodes`, because it is what can be billed without passing the gate again |
| `nodepool_node_labels` | map(string) | `{}` | Node labels |
| `nodepool_node_annotations` | map(string) | `{}` | Node annotations |
| `nodepool_node_taints` | list(object) | `[]` | Node taints (key, value, effect) |

### Multiple node pools

Use the `nodepools` map instead of the single-pool variables. When `nodepools` is
non-empty, the single-pool variables are ignored. Each entry requires all 8 fields.

```hcl
nodepools = {
  "gpu-pool" = {
    instance_type    = "gd-8xh100ib-i128"
    target_nodes     = 2
    autoscaling      = false
    min_nodes        = 0
    max_nodes        = 0
    node_labels      = {}
    node_annotations = {}
    node_taints      = []
  },
  "cpu-pool" = {
    instance_type    = "cpu-4"
    target_nodes     = 1
    autoscaling      = true
    min_nodes        = 0
    max_nodes        = 4
    node_labels      = {}
    node_annotations = {}
    node_taints      = []
  }
}
```

## Example tfvars — single GPU pool on an existing cluster

```hcl
# Existing cluster/VPC values (required even for a node-pool-only apply):
zone               = "US-EAST-04A"
vpc_name           = "use04a-prod-vpc"
cluster_name       = "use04a-prod"
kubernetes_version = "v1.35"

# Node pool:
cks_kubeconfig_path    = "/home/user/Downloads/use04a-prod-kubeconfig.yaml"
create_nodepool        = true
create_dfs_pvc         = false
nodepool_name          = "gpu-pool"
nodepool_instance_type = "gd-8xh100ib-i128"
nodepool_target_nodes  = 2
```

## Apply (targeted)

```bash
terraform init
terraform plan  -target=module.nodepool   # should show ONLY node pool resources
```

Before the apply, gate it — fail closed, exactly as the Step 4 checkpoint in
the workflow requires:

1. Check the kubeconfig **Terraform** will use — `config_path =
   var.cks_kubeconfig_path` above — not the ambient context, which is a
   different file and does not bind the apply:

   ```bash
   CKS_KCFG=$(awk -F'"' '/^[[:space:]]*cks_kubeconfig_path[[:space:]]*=/{print $2}' terraform.tfvars)
   kubectl --kubeconfig "${CKS_KCFG:?cks_kubeconfig_path is not set}" config current-context
   ```

   It must print the target cluster exactly. On any other output, or an error,
   stop — do not apply. Fix `cks_kubeconfig_path`, or run
   `kubectl --kubeconfig "$CKS_KCFG" config use-context <cluster>`, then
   re-check.
2. State the cost with the quantities from the plan (the tfvars above create
   2 × `gd-8xh100ib-i128` GPU nodes — billed while running regardless of load,
   sold whole — and/or M × CPU nodes) and get a fresh confirmation to that
   message. Apply the Step 4 checkpoint's size-scaled rule: above the
   thresholds it states, the customer must re-state the quantity (e.g. "yes, 2
   nodes of gd-8xh100ib-i128") rather than a bare "yes". Read the thresholds
   off that checkpoint — the tfvars above exceed them.

<!-- Maintainer note: the numbers are deliberately NOT repeated here. They live
     once in _snippets/cost-gates.md, which the workflow gates render; a skill's
     references/ directory is copied into dist/ verbatim and never templated, so
     a copy written here could not track the snippet and would silently drift. -->

Then apply — assertion and apply in **one** shell call, because the gate above
ran in a call of its own:

```bash
set -euo pipefail
CKS_KCFG=$(awk -F'"' '/^[[:space:]]*cks_kubeconfig_path[[:space:]]*=/{print $2}' terraform.tfvars)
EXPECT=<cluster>
# Enforced re-assertion, in the SAME call as the apply: the gate above ran in an
# earlier call, and anything could have re-pointed that file since. `set -e`
# stops here on a mismatch, so the apply cannot run unguarded.
test -f "${CKS_KCFG:?cks_kubeconfig_path is not set in terraform.tfvars}"
test "$(kubectl --kubeconfig "$CKS_KCFG" config current-context)" = "$EXPECT"
terraform apply -target=module.nodepool -auto-approve
```

## Verify

Context first, on its own — output from a mismatched or unverifiable context
describes the wrong cluster; never report it as evidence:

```bash
CKS_KCFG=$(awk -F'"' '/^[[:space:]]*cks_kubeconfig_path[[:space:]]*=/{print $2}' terraform.tfvars)
kubectl --kubeconfig "${CKS_KCFG:?cks_kubeconfig_path is not set}" config current-context
# Must print the target cluster exactly. On anything else, stop — do not run
# the proof commands below and do not report their output as evidence.
kubectl --kubeconfig "$CKS_KCFG" get nodepools
kubectl --kubeconfig "$CKS_KCFG" get nodes
```
