# Reference: SUNK NodeSet ↔ CKS Node Pool activation

Consolidated reference for making provisioned CKS Node Pool capacity usable in a
SUNK Slurm cluster. Read this alongside the main skill body when you need the
full mapping, the exact fields, or the diagnostic decision tree.

---

## The capacity chain

```
SunkCluster.spec.nodes[i]  (managed clusters only)
        │  each entry → one NodePool + one NodeSet
        ▼
NodePool  (kind: NodePool, compute.coreweave.com/v1alpha1)   ← CKS infra resource
        │  CKS provisions physical machines → Kubernetes Node objects, labeled:
        │     node.coreweave.cloud/type=<instance-type>
        │     compute.coreweave.com/node-pool=<pool-name>
        │     node.coreweave.cloud/state=production
        ▼
NodeSet   (kind: NodeSet, sunk.coreweave.com/v1alpha1)        ← SUNK compute group
        │  required nodeAffinity selects matching Kubernetes Nodes
        │  (typically on compute.coreweave.com/node-pool), up to replicas
        ▼
slurmd Pods  (one per matching Kubernetes Node, 1:1)         ← the Slurm nodes
        │
        ▼
Slurm nodes visible in `sinfo`, in a partition (one per NodeSet by default)
```

Two links are **not** automatic and are where capacity goes missing:

1. **NodeSet `replicas` must be high enough** to cover the Nodes. Scaling the
   Node Pool alone doesn't add Slurm nodes. (`DESIRED < FEASIBLE`.)
2. **NodeSet required affinity/tolerations must match** the Node Pool's
   labels/taints. New/renamed pools whose labels the affinity doesn't select are
   feasible=0 for that NodeSet. (`FEASIBLE < DESIRED`.)

---

## Confirmed resource / label / taint names

| Thing | Value | Source |
|-------|-------|--------|
| NodeSet CRD | `kind: NodeSet`, `apiVersion: sunk.coreweave.com/v1alpha1` | Docs: SUNK NodeSet page; SA runbook |
| SunkCluster CRD | `kind: SunkCluster`, `apiVersion: sunk.coreweave.com/v1alpha1` | Docs: create-sunk-cluster |
| Node Pool CRD | `kind: NodePool`, `apiVersion: compute.coreweave.com/v1alpha1` | Docs: Create a Node Pool |
| Pool-binding label (on Nodes) | `compute.coreweave.com/node-pool=<pool-name>` | SA runbook; SA arch notes |
| Instance-type label (on Nodes) | `node.coreweave.cloud/type=<instance-type>` | Docs: Manage Node Pools; SA arch notes |
| Node state label | `node.coreweave.cloud/state=production` | SA arch notes; docs |
| NodeSet lock taint (auto-added to Pods) | `sunk.coreweave.com/lock` | Docs: NodeSet page |
| SUNK-managed Node labels | `sunk.coreweave.com/nodeset`, `.../namespace`, `.../cluster`, `.../pod` | Docs: Node Controller |
| Node Pool label/taint fields | `spec.nodeLabels`, `spec.nodeTaints` | Docs: Create a Node Pool |
| Legacy manual SUNK label/taint | `sunk.coreweave.com/node` (casing inconsistent: `True`/`true`) | SA runbook (older clusters) |
| Custom dedicated-pool label prefix | `sunk.<org>/node` (can't use coreweave.com/coreweave.cloud prefixes) | SA Confluence / runbook |

<!-- TODO(SME): the single most important item to confirm with a SUNK SME before
     this skill ships is the label-matching convention: is `compute.coreweave.com/node-pool`
     the label customers should bind NodeSets to on current clusters, and is
     there a single CoreWeave-standard SUNK label/taint (vs. the org-scoped
     `sunk.<org>/node` seen in SA-authored customer values files)? -->

---

## NodeSet status fields (the diagnostic)

`kubectl get nodeset -n tenant-slurm` columns / `.status` fields:

| Field / column | Metric | Healthy when | Meaning |
|----------------|--------|--------------|---------|
| `desiredNumberScheduled` / DESIRED | `kube_sunk_nodeset_desired` | matches intent | Target count. Equals `spec.replicas` if set, else `numberFeasible`. |
| `numberFeasible` / FEASIBLE | `kube_sunk_nodeset_feasible` | `>= DESIRED` | Nodes the NodeSet *could* schedule on (match affinity + tolerations + resources). |
| `currentNumberScheduled` / CURRENT | `kube_sunk_nodeset_current` | `== DESIRED` | Nodes the NodeSet actually has a Pod on. |
| `numberReady` / READY | `kube_sunk_nodeset_ready` | `== CURRENT` | Nodes whose `slurmd` Pod is Ready. |
| `numberDrain` / DRAIN | `kube_sunk_nodeset_drained` | `0` | Nodes drained in Slurm. |
| `numberMisscheduled` / MISSCHEDULED | `kube_sunk_nodeset_misscheduled` | `0` | Pods on Nodes that no longer match the selector. |
| `numberUnavailable` / UNAVAILABLE | `kube_sunk_nodeset_unavailable` | `0` | Scheduled but not-Ready Pods. |

Read the status directly:

```bash
kubectl get nodeset -n tenant-slurm -o custom-columns=\
'NAME:.metadata.name,DESIRED:.status.desiredNumberScheduled,FEASIBLE:.status.numberFeasible,CURRENT:.status.currentNumberScheduled,READY:.status.numberReady'
```

---

## Diagnostic decision tree

Run these three side by side:

```bash
kubectl get nodepool                                                   # capacity + quota
kubectl get nodeset -n tenant-slurm                                    # feasibility
kubectl get nodes -L compute.coreweave.com/node-pool -L node.coreweave.cloud/state --show-labels
```

Then:

- **`DESIRED < FEASIBLE`** → NodeSet `replicas` capped. Raise `replicas` (body Step 5, fix mode 1).
- **`FEASIBLE < DESIRED`**:
  - sum of same-instance-type pools' `CURRENT` == NodeSet `DESIRED`, and
    `FEASIBLE` == one pool's `CURRENT` → **selector mismatch** on the other pool.
    Add it to the NodeSet affinity, or share a label (body Step 5, fix mode 2).
  - a pool shows `QUOTA=Over` / `CAPACITY=CapacityUnknown` → real capacity/quota
    shortfall, route to CKS/capacity, not a NodeSet fix.
  - a pool's `TARGET < DESIRED`, or Nodes are not `production` → scale the pool
    or resolve Node health first.
- **`FEASIBLE >= DESIRED` but `CURRENT < DESIRED`** → Pods pending. Inspect:
  ```bash
  kubectl describe pod -n tenant-slurm <SLURMD_POD>            # read Events
  kubectl get pods -A -o wide --field-selector spec.nodeName=<NODE>   # non-Slurm squatters?
  ```
- **`CURRENT > target pool node count`** → likely `nodeSelector` used instead of
  `affinity` at the NodeSet spec level; the NodeSet is grabbing unintended Nodes.

---

## Deployment models — where the fix goes

### Managed (`SunkCluster` / operator, often `cw-sunk` namespace)

- Each `spec.nodes` entry generates one NodePool + one NodeSet; per the
  SunkCluster reference, "the count is kept in sync between them."
- Fix by editing the `SunkCluster` (`count`, or add a `nodes` entry), **not** the
  NodeSet — the operator reconciles NodeSets back.
- `name` and `instanceType` are immutable; `count` is mutable.
- Track readiness via `SunkCluster` conditions: `NodePoolsAvailable`,
  `NodeSetsAvailable`, `SlurmClusterAvailable`, aggregate `Ready`.

### Helm / GitOps-managed (`compute.nodes` values, often `sunk` namespace)

- Customer authors NodeSet `replicas`, `affinity`, `tolerations` directly.
- Fix in the values file (raise `replicas`; align `affinity`/`tolerations` to the
  pool labels/taints) and apply via `helm upgrade` or ArgoCD sync.
- **GitOps caveat:** live `kubectl edit`/`scale`/`label` are reverted on sync —
  change the Git source and sync.

---

## Slurm-side verification (from a login pod)

```bash
# Get a shell
kubectl get svc slurm-login -n tenant-slurm            # note EXTERNAL-IP, then ssh
kubectl exec -it -n tenant-slurm <LOGIN_POD> -c sshd -- bash   # or exec in

# Confirm nodes present and available
sinfo -N -l
sinfo -N --states=idle,mix

# Inspect a node that isn't idle/mix
scontrol show node <NODE_NAME>

# Undrain once healthy
scontrol update nodename=<NODE_NAME> state=resume

# Prove schedulable
srun -N 1 -w <NEW_NODE_NAME> hostname
srun -N <N> hostname
```

Slurm node names map 1:1 to `slurmd` Pods, named from the Node's last two IPv4
octets, zero-padded (Node IP `10.174.12.2` → `...-012-002`). By default each
NodeSet maps to its own Slurm partition (name = the `compute.nodes` entry name);
multiple NodeSets can be grouped into one partition via `compute.partitions`.

---

## Sources

- CoreWeave Docs — SUNK NodeSet CRD: https://docs.coreweave.com/products/sunk/discover_sunk/nodeset
- CoreWeave Docs — Node Controller: https://docs.coreweave.com/products/sunk/discover_sunk/node-controller
- CoreWeave Docs — Configure compute nodes: https://docs.coreweave.com/products/sunk/deploy_sunk/configure-compute-nodes
- CoreWeave Docs — Create a SUNK cluster / SunkCluster CR + verify: https://docs.coreweave.com/products/sunk/deploy_sunk/create-sunk-cluster
- CoreWeave Docs — SunkCluster reference: https://docs.coreweave.com/products/sunk/reference/sunkcluster-reference
- CoreWeave Docs — Drain and undrain Slurm nodes: https://docs.coreweave.com/products/sunk/manage_sunk/drain-and-undrain-nodes
- CoreWeave Docs — Slurm node states: https://docs.coreweave.com/products/sunk/manage_sunk/slurm-node-states
- CoreWeave Docs — Create a CKS Node Pool (labels/taints): https://docs.coreweave.com/products/cks/nodes/create
- CoreWeave Docs — Node Pool status: https://docs.coreweave.com/products/cks/nodes/nodepool-status
- CoreWeave Docs — Connect to a Slurm login node: https://docs.coreweave.com/products/sunk/access_sunk/connect-to-slurm-login-node
- Internal SA runbook (grounding for diagnostics/fixes; not customer-visible):
  coreweave/tars-support `docs/knowledge/sunk/sunk-resources.md`, and SA
  Confluence "NodeSets + NodePools" / "sunk arch design notes".
