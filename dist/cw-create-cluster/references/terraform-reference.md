# Terraform configuration reference

This is the variable and module reference for the [CoreWeave reference architecture](https://github.com/coreweave/reference-architecture/tree/main/terraform). The SKILL.md file references this document for detailed configuration — read it when you need variable specifics, CIDR guidance, or example tfvars.

---

## Table of contents

1. [Provider configuration](#provider-configuration)
2. [Required variables](#required-variables)
3. [Optional variables](#optional-variables)
4. [VPC CIDR defaults](#vpc-cidr-defaults)
5. [Node pool variables](#node-pool-variables)
6. [DFS variables](#dfs-variables)
7. [Object storage variables](#object-storage-variables)
8. [Example tfvars — minimal](#example-tfvars--minimal)
9. [Example tfvars — multiple node pools](#example-tfvars--multiple-node-pools)
10. [Outputs](#outputs)
11. [Repository structure](#repository-structure)

---

## Provider configuration

The root `providers.tf` configures two providers:

```hcl
provider "coreweave" {
  token       = var.coreweave_api_token
  s3_endpoint = var.coreweave_s3_endpoint  # defaults to https://cwobject.com
}

provider "kubernetes" {
  config_path = var.cks_kubeconfig_path    # set in Phase 2
}
```

Required provider versions:
- `coreweave/coreweave` >= 0.3.0
- `hashicorp/kubernetes` >= 2.23.0
- Terraform >= 1.2.0

The API token should be set via environment variable, not in tfvars:

```bash
export TF_VAR_coreweave_api_token="<YOUR_TOKEN>"
```

---

## Required variables

| Variable | Type | Description | Example |
|----------|------|-------------|---------|
| `zone` | string | CoreWeave zone | `"US-EAST-04A"` |
| `vpc_name` | string | VPC name | `"my-vpc"` |
| `vpc_prefixes` | list(object) | Named CIDR prefixes. Order: [0]=pod, [1]=service, [2+]=internal LB. At least 3 entries. | See CIDR defaults below |
| `host_prefixes` | set(object) | Host prefix definitions for compute | See CIDR defaults below |
| `cluster_name` | string | CKS cluster name (max 30 chars) | `"my-cks-cluster"` |
| `kubernetes_version` | string | Kubernetes minor version | `"v1.35"` |

---

## Optional variables

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `cks_public` | bool | `true` | Whether the cluster API is publicly accessible |
| `cks_kubeconfig_path` | string | `null` | Path to kubeconfig (set in Phase 2) |
| `oidc` | object | `null` | OIDC config: issuer_url, client_id, ca, admin_group_binding, groups_claim, groups_prefix, required_claim, signing_algs, username_claim, username_prefix |
| `authn_webhook` | object | `null` | Authentication webhook: server, ca |
| `authz_webhook` | object | `null` | Authorization webhook: server, ca |
| `node_port_range` | object | `null` | Custom NodePort range: start, end |
| `audit_policy` | string | `null` | Base64-encoded audit policy YAML |
| `coreweave_s3_endpoint` | string | `null` | S3 endpoint override (defaults to https://cwobject.com) |

---

## VPC CIDR defaults

These are valid defaults from the reference architecture and work for most deployments:

```hcl
vpc_prefixes = [
  { name = "pod cidr",         value = "10.0.0.0/13" },
  { name = "service cidr",     value = "10.16.0.0/22" },
  { name = "internal lb cidr", value = "10.32.4.0/22" },
]

host_prefixes = [
  { name = "primary", type = "PRIMARY", prefixes = ["10.16.192.0/18"] }
]
```

The order of `vpc_prefixes` matters — the reference architecture's `main.tf` derives CKS prefix names positionally:
- Index 0 → pod CIDR name
- Index 1 → service CIDR name
- Index 2+ → internal LB CIDR name(s)

See [CoreWeave VPC CIDR docs](https://docs.coreweave.com/docs/products/networking/vpc/vpc-cidr) for sizing guidance.

---

## Node pool variables

Node pools require `create_nodepool = true` and a valid `cks_kubeconfig_path` (Phase 2 only).

### Single node pool

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `nodepool_name` | string | `"example-nodepool"` | NodePool name |
| `nodepool_instance_type` | string | `"gd-8xh100ib-i128"` | CKS instance type |
| `nodepool_target_nodes` | number | `2` | Desired node count |
| `nodepool_autoscaling` | bool | `false` | Enable autoscaling |
| `nodepool_min_nodes` | number | `0` | Min nodes (autoscaling) |
| `nodepool_max_nodes` | number | `0` | Max nodes (autoscaling) |
| `nodepool_node_labels` | map(string) | `{}` | Node labels |
| `nodepool_node_annotations` | map(string) | `{}` | Node annotations |
| `nodepool_node_taints` | list(object) | `[]` | Node taints (key, value, effect) |

### Multiple node pools

Use the `nodepools` map instead of single-pool variables. When `nodepools` is non-empty, the single-pool variables are ignored.

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

Each entry requires all 8 fields (instance_type, target_nodes, autoscaling, min_nodes, max_nodes, node_labels, node_annotations, node_taints).

---

## DFS variables

DFS PVCs require `create_dfs_pvc = true` and a valid `cks_kubeconfig_path` (Phase 2 only).

### Single PVC

| Variable | Type | Default |
|----------|------|---------|
| `dfs_pvc_name` | string | `"dfs-shared"` |
| `dfs_pvc_namespace` | string | `"default"` |
| `dfs_pvc_size` | string | `"100Gi"` |

### Multiple PVCs

```hcl
dfs_pvcs = {
  "dfs-shared" = { namespace = "default", size = "100Gi" },
  "dfs-ml"     = { namespace = "ml",      size = "500Gi" }
}
```

---

## Object storage variables

Object storage is independent of the cluster but commonly provisioned alongside it.

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `object_storage_bucket_name` | string | `null` | Bucket name (globally unique; must not start with cw- or vip-) |
| `object_storage_bucket_zone` | string | `null` | Bucket zone (defaults to var.zone) |
| `object_storage_bucket_tags` | map(string) | `{}` | Bucket tags |
| `object_storage_org_access_policies` | map(object) | `{}` | Organization access policies (at least one required before creating a bucket) |
| `object_storage_bucket_policy_statements` | list(object) | `null` | Per-bucket access policy statements |

For OIDC Workload Identity Federation setup with object storage, see the [reference architecture README](https://github.com/coreweave/reference-architecture/blob/main/terraform/README.md).

---

## Example tfvars — minimal

A minimal Phase 1 configuration using all defaults:

```hcl
zone     = "US-EAST-04A"
vpc_name = "my-vpc"

vpc_prefixes = [
  { name = "pod cidr",         value = "10.0.0.0/13" },
  { name = "service cidr",     value = "10.16.0.0/22" },
  { name = "internal lb cidr", value = "10.32.4.0/22" },
]

host_prefixes = [
  { name = "primary", type = "PRIMARY", prefixes = ["10.16.192.0/18"] }
]

cluster_name       = "my-cks-cluster"
kubernetes_version = "v1.35"
cks_public         = true

create_nodepool = false
create_dfs_pvc  = false
```

---

## Example tfvars — multiple node pools

Phase 2 additions (after cluster is Running and kubeconfig is downloaded):

```hcl
cks_kubeconfig_path = "~/.kube/cks-config"
create_nodepool     = true

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

---

## Outputs

| Output | Description |
|--------|-------------|
| `vpc_id` | Created VPC ID |
| `vpc_zone` | VPC zone |
| `cks_cluster_id` | CKS cluster ID |
| `cks_cluster_name` | CKS cluster name |
| `cks_api_server_endpoint` | Kubernetes API server endpoint |
| `cks_service_account_oidc_issuer_url` | OIDC issuer URL for K8s service account tokens (for WIF) |
| `cks_status` | Cluster status |
| `nodepools` | Map of created node pool names |
| `dfs_pvcs` | Map of created DFS PVCs |
| `object_storage_bucket_name` | Bucket name (if created) |

---

## Repository structure

```
terraform/
├── providers.tf              # CoreWeave + Kubernetes providers
├── main.tf                   # Wires all modules together
├── variables.tf              # Root variables
├── outputs.tf                # Outputs from each module
├── terraform.tfvars.example  # Copy to terraform.tfvars
└── modules/
    ├── network/              # VPC (coreweave_networking_vpc)
    ├── cks/                  # CKS cluster (coreweave_cks_cluster)
    ├── object_storage/       # Bucket + access policies
    ├── nodepool/             # CKS NodePool (kubernetes_manifest)
    └── dfs/                  # DFS PVC (shared-vast)
```

---

## Links

- [Reference architecture repo](https://github.com/coreweave/reference-architecture)
- [CoreWeave Terraform provider](https://registry.terraform.io/providers/coreweave/coreweave/latest/docs)
- [CKS documentation](https://docs.coreweave.com/products/cks/clusters/introduction)
- [VPC CIDR sizing](https://docs.coreweave.com/docs/products/networking/vpc/vpc-cidr)
