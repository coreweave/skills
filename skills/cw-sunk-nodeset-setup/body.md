
# Activate provisioned Node Pool capacity in a SUNK Slurm cluster

You are helping a CoreWeave customer make **newly-provisioned CKS Node Pool
capacity usable inside their SUNK (Slurm on Kubernetes) cluster**. This is the
step that surprises most customers: when GPUs or CPUs are provisioned into a CKS
**Node Pool**, the machines are live and healthy in Kubernetes — but Slurm can't
schedule on them until they are picked up by a SUNK **NodeSet**. If the NodeSet's
`nodeAffinity` and `tolerations` don't match the Node Pool's labels and taints,
the new Nodes are healthy and idle but **not feasible** for the NodeSet, so they
never join Slurm and never appear in `sinfo`. The customer's paid-for capacity
sits idle, and this is frequently mistaken for a quota reduction.

Your job: confirm the capacity exists in Kubernetes, confirm whether it is being
picked up by a NodeSet, close any gap, and verify the nodes are actually
schedulable in Slurm.

> **Terminology — this matters and it is easy to get wrong.** In SUNK,
> Kubernetes **Nodes** (capitalized) are the physical worker machines. Slurm
> **nodes** (lowercase) are Kubernetes Pods running `slurmd`, one per Kubernetes
> Node. A **Node Pool** (`kind: NodePool`, `compute.coreweave.com/v1alpha1`) is
> the CKS resource that provisions physical Nodes. A **NodeSet**
> (`kind: NodeSet`, `sunk.coreweave.com/v1alpha1`) is the SUNK resource that
> turns matching Kubernetes Nodes into Slurm nodes. Capacity flows
> **Node Pool → Kubernetes Nodes → NodeSet → Slurm nodes**. This skill is about
> the **NodeSet → Slurm** link, which is the one that isn't automatic in every
> deployment.

---

## Which deployment model is this? (decide first)

SUNK clusters are managed one of two ways, and the fix differs. Determine which
one the customer has **before** doing anything else:

1. **`SunkCluster` custom resource (operator-managed).** Each entry in
   `spec.nodes` becomes **one NodePool and one NodeSet**, and the operator keeps
   the NodeSet count in sync with the NodePool count automatically. Here you do
   **not** hand-edit NodeSets — you change `count` (or add a `nodes` entry) on
   the `SunkCluster` and let the operator reconcile. Detect with:
   ```bash
   kubectl get sunkcluster -n tenant-slurm
   ```
   If this returns a resource, treat the cluster as operator-managed.

2. **Helm / GitOps-managed NodeSets (`compute.nodes` in a Slurm values file).**
   The customer manages NodeSets directly (often through ArgoCD). Here the
   NodeSet's `affinity`/`tolerations` are authored by the customer and **must**
   match the Node Pool's labels and taints. This is where the activation gap
   most often lives — a new or renamed Node Pool whose labels the NodeSet
   affinity doesn't select.

A quick namespace signal helps distinguish them: managed (operator/"skooby")
clusters typically use a `cw-sunk` namespace, while Helm/unmanaged deployments
use `sunk`. The Slurm workloads themselves run in `tenant-slurm`.

```bash
kubectl get ns | grep -E '\b(cw-sunk|sunk|tenant-slurm)\b'
```

Ask the customer, or infer it: if `kubectl get sunkcluster -n tenant-slurm`
returns something (and/or a `cw-sunk` namespace exists), start with model 1;
otherwise model 2. When unsure, the diagnostics below work for both — the *fix*
is where they diverge.

<!-- TODO(SME): confirm the customer-facing namespace conventions. Public docs
     use `tenant-slurm` for the SunkCluster and Slurm workloads; the internal SA
     runbook distinguishes `cw-sunk` (managed) vs `sunk` (Helm/unmanaged) as an
     operator signal. Confirm which namespace a customer runs `kubectl get
     nodeset` in, and whether it's ever anything other than `tenant-slurm`. -->

---

## Before you start

Confirm these prerequisites:

- The customer has a **running CKS cluster** with **SUNK already deployed** (the
  SUNK operator and a Slurm control plane exist). This skill activates *new*
  capacity into an *existing* Slurm cluster; it does not stand up SUNK from
  scratch. Confirm the operator is present:
  ```bash
  kubectl get crd sunkclusters.sunk.coreweave.com
  kubectl get crd nodesets.sunk.coreweave.com
  ```
  If either CRD is missing, SUNK isn't deployed on this cluster — stop and have
  the customer contact their CoreWeave Solutions Architect.
- The Node Pool the customer expects to use has **already been provisioned** and
  its Nodes are up. If the Node Pool doesn't exist yet, or hasn't reached its
  target Node count, use the `cw-create-node-pool` skill first, then come back
  here.
- **kubectl** is installed and configured with a kubeconfig for the target
  cluster.
- **The correct kubectl context is active.** CoreWeave kubeconfig files often
  contain contexts for **multiple clusters**. Every command below targets
  whichever context is active — running them against the wrong cluster is a
  common and confusing mistake. Verify first:
  ```bash
  kubectl config get-contexts
  kubectl config use-context <TARGET_CLUSTER_NAME>
  kubectl config current-context
  ```
- Access to a **Slurm login pod** for the Slurm-side verification in Step 6
  (`sinfo`). Slurm commands must run *inside* the login pod, not from the
  customer's laptop. See Step 6 for how to get a shell.

**The Cloud Console is at `console.coreweave.com`** (not `cloud.coreweave.com`).

---

## Step 1 — Confirm the capacity actually exists in Kubernetes

Before touching Slurm, confirm the physical capacity is real and healthy. If the
Node Pool hasn't delivered its Nodes, the problem is upstream (capacity/quota),
not the NodeSet, and the rest of this skill won't help.

```bash
kubectl get nodepool
```

Read the columns. A healthy, fully-delivered Node Pool has `CURRENT` equal to
`TARGET`, with `QUEUED` and `INPROGRESS` at `0`, `CAPACITY` `Sufficient`, and
`QUOTA` `Under`:

```text
NAME               INSTANCE TYPE      TARGET   QUEUED   INPROGRESS   CURRENT   ...   CAPACITY                 QUOTA
example-nodepool   gd-8xh100ib-i128   4        0        0            4         ...   Sufficient               Under
```

- If `CURRENT` < `TARGET` with Nodes `QUEUED` / `CAPACITY: QueuedAwaitingCapacity`,
  the Node Pool is still waiting on capacity or quota — that is a provisioning
  issue, **not** a NodeSet issue. A newly-added Node can take **up to ~20 minutes**
  to boot before it counts toward `CURRENT`. Wait, or investigate quota, before
  proceeding.
- If `CURRENT` == `TARGET`, the physical capacity is live. Continue.

Now confirm the Kubernetes Nodes are actually present and `Ready`, and note the
label CKS puts on them identifying their Node Pool:

```bash
kubectl get nodes -L node.coreweave.cloud/type -L compute.coreweave.com/node-pool -L node.coreweave.cloud/state
```

**What to look for:**

- The new Nodes appear and are `Ready`.
- `node.coreweave.cloud/state` is `production`. Nodes that are in `triage`,
  `cordoned`, or another non-production state are typically **not eligible** to
  join a NodeSet until they return to `production`. If new Nodes are stuck in a
  non-production state, that is a Node health issue to resolve first, not a
  NodeSet-config issue.
- Note the **`compute.coreweave.com/node-pool`** label value — this is the Node
  Pool name, and it is the key hook the NodeSet affinity uses to select these
  Nodes. Also note `node.coreweave.cloud/type` (the instance type).

<!-- TODO(SME): verify `compute.coreweave.com/node-pool` is the canonical,
     current label key CKS applies to Node Pool members on customer clusters.
     Internal SA notes and docs reference it, but some older clusters may use a
     different or absent label (see Step 5, "older clusters"). Also confirm the
     exact set of values `node.coreweave.cloud/state` can take and which ones
     block NodeSet feasibility. -->

> **Checkpoint.** Do not continue until `kubectl get nodepool` shows
> `CURRENT == TARGET` and the corresponding Kubernetes Nodes are `Ready` and in
> `production` state. If they aren't, the capacity isn't ready yet and no NodeSet
> change will surface it.

---

## Step 2 — See what the NodeSet thinks it can use

This is the heart of the diagnosis. List the NodeSets and compare what they
*want* against what they *can actually use*:

```bash
kubectl get nodeset -n tenant-slurm
```

The NodeSet status exposes these fields (also available as columns / metrics):

| Field                    | Meaning                                                                 |
| ------------------------ | ----------------------------------------------------------------------- |
| `desiredNumberScheduled` | Desired number of Nodes to schedule on (the target).                    |
| `numberFeasible`         | Number of Nodes the NodeSet **could** schedule on — Nodes that match its affinity, tolerations, and resource requests. |
| `currentNumberScheduled` | Number of Nodes the NodeSet is actually scheduled on right now.         |
| `numberReady`            | Number of Nodes whose NodeSet Pod (`slurmd`) is `Ready`.                |

The NodeSet controller "scales up to the desired number of replicas, if
possible. The number of available Kubernetes Nodes in the cluster that match the
NodeSet's affinity, tolerations, and resource requests may limit this." In other
words: **a Node only counts toward `feasible` if it matches the NodeSet.**

If the status fields aren't visible in the default columns, read them directly:

```bash
kubectl get nodeset -n tenant-slurm -o custom-columns=\
'NAME:.metadata.name,DESIRED:.status.desiredNumberScheduled,FEASIBLE:.status.numberFeasible,CURRENT:.status.currentNumberScheduled,READY:.status.numberReady'
```

### Interpreting the numbers — this is the diagnostic

There are **two distinct failure modes**, and they need different fixes. Read
`DESIRED`, `FEASIBLE`, and `CURRENT` together:

- **`DESIRED` < `FEASIBLE`** (the NodeSet wants fewer nodes than it *could* use):
  the NodeSet's `replicas` is capped below the available capacity, so `slurmd`
  isn't deployed to the extra Nodes and Slurm never sees them. This is extremely
  common when a customer scales the **Node Pool** up but forgets to scale the
  **NodeSet** — they are independent resources. The Node Pool controls how many
  physical Nodes exist; the NodeSet's `replicas` controls how many of them run
  Slurm. Fix by raising `replicas` (Step 4 for managed clusters, Step 5 "fix
  mode 1" for Helm/GitOps clusters).
- **`FEASIBLE` < `DESIRED`** (and the missing count matches the new Node Pool's
  `CURRENT` from Step 1): the new Nodes exist and are healthy but **do not match
  the NodeSet's affinity/tolerations**, so the NodeSet can't use them. This is
  the classic label/selector activation gap. Go to **Step 4 / Step 5**,
  "affinity mismatch".
- **`FEASIBLE` >= `DESIRED` but `CURRENT` < `DESIRED`**: the Nodes match and are
  feasible, but Pods aren't being created or are failing to schedule — usually
  transient (scale-up in progress), or a resource-request/taint issue, or a
  non-Slurm workload is occupying the Node. Give it a few minutes, then re-check;
  if it persists, inspect a pending `slurmd` Pod (Step 3) and check what else is
  on the Node: `kubectl get pods -A -o wide --field-selector spec.nodeName=<NODE>`.
- **`CURRENT` == `DESIRED` == `FEASIBLE` and `READY` == `CURRENT`**: the NodeSet
  is fully populated. If Slurm still can't see the nodes, the problem is on the
  Slurm side (Step 6 — e.g. nodes drained or `INVAL`), not the K8s side.

> **`CURRENT` > the target pool's node count?** If a NodeSet is scheduled on
> *more* Nodes than the pool you expect (e.g. `FEASIBLE 62` but the pool has 60),
> the NodeSet is almost certainly using a `nodeSelector` at the spec level
> instead of `affinity` — see the Step 3 gotcha. It's grabbing unintended Nodes.

> **A common misread:** customers see `FEASIBLE` stuck at exactly one Node Pool's
> worth of Nodes while a second, same-instance-type Node Pool's Nodes are missing,
> and conclude CoreWeave reduced their quota. It's almost always that the NodeSet
> affinity only lists the original Node Pool, so the second pool's Nodes are
> healthy and in production but not feasible. Confirm with the label check below.

---

## Step 3 — Confirm the mismatch: compare NodeSet selectors to Node labels

You want to see exactly why the new Nodes aren't feasible. Two halves must line
up: the NodeSet's **required** `nodeAffinity` + `tolerations`, and the Node
Pool's **labels** + **taints**.

Dump the NodeSet's selection rules:

```bash
kubectl get nodeset <NODESET_NAME> -n tenant-slurm -o yaml | \
  grep -A40 -E 'affinity:|tolerations:'
```

Note:

- The NodeSet controller evaluates **only the required portion** of the Node
  affinity (`requiredDuringSchedulingIgnoredDuringExecution`) and ignores the
  rest. So a match expressed only as *preferred* affinity will not make a Node
  feasible.
- The Pod's own final affinity is overridden by the controller to pin it to a
  specific Node once selected — so don't be thrown by that. What gates
  feasibility is the NodeSet spec's required affinity.
- **The only affinity a customer needs to configure for pool binding is
  `compute.coreweave.com/node-pool`.** SUNK's base compute definition already
  includes a required affinity for `node.coreweave.cloud/state=production`, which
  every NodeSet inherits automatically. **Do not add a `state=production`
  affinity manually** — it is redundant and misleading. If new Nodes aren't
  `production`, that's a Node-health problem to fix (Step 1), not something to
  paper over with affinity.

Now dump the Node Pool's labels and taints:

```bash
kubectl get nodepool <NODEPOOL_NAME> -o yaml | grep -A20 -E 'nodeLabels:|nodeTaints:'
```

And confirm those labels actually landed on the Nodes:

```bash
kubectl get nodes -l compute.coreweave.com/node-pool=<NODEPOOL_NAME> --show-labels
```

**Diagnose the gap.** The new Nodes are not feasible when any of these is true:

- The NodeSet's required `nodeAffinity` selects a label key/value that the new
  Nodes **do not carry** (e.g. affinity says `compute.coreweave.com/node-pool In
  [old-pool]` but the new Nodes are in `new-pool`).
- The Node Pool applies a **taint** that the NodeSet does **not tolerate** (the
  `slurmd` Pod can't land there).
- The instance type / resource requests on the NodeSet can't be satisfied by the
  Node.

> **Gotcha — bind with `affinity`, not `nodeSelector`.** Using a `nodeSelector`
> in the NodeSet spec to bind it to a pool does **not** propagate the constraint
> to the generated `slurmd` Pods — the YAML looks valid but Pods can land on
> unintended Nodes (a tell-tale sign is `CURRENT`/`FEASIBLE` *higher* than the
> target pool's Node count). Always bind with
> `affinity.requiredDuringSchedulingIgnoredDuringExecution`, not `nodeSelector`.
> If a customer tried to add nodes by adding a `nodeSelector`, that is why it had
> no effect on which pool the NodeSet uses.

> **Gotcha — label value casing is case-sensitive.** Some clusters have the same
> label applied with inconsistent casing (e.g. `sunk.coreweave.com/node=True` on
> some Nodes and `=true` on others). Both values are valid, but `kubectl -l`
> selectors and affinity `matchExpressions` are case-sensitive, so a selector
> written for one casing silently skips Nodes with the other. If a match looks
> correct but Nodes still aren't feasible, check the exact casing on the Nodes.

<!-- TODO(SME): verify the nodeSelector-does-not-propagate behavior and the
     label-casing inconsistency against the current SUNK chart version, and
     confirm they apply to both the SunkCluster-managed and Helm-managed NodeSet
     paths. Sourced from internal SA runbook (coreweave/tars-support). -->

---

## Step 4 — Fix (operator-managed `SunkCluster`)

If Step 1 showed the capacity exists but it belongs to a Node Pool that the
`SunkCluster` doesn't yet declare, the fix is to declare it on the `SunkCluster`
and let the operator create the matching NodeSet. **Do not hand-edit the managed
NodeSet** — the operator will reconcile it back.

- **To grow an existing node group** (same instance type, more Nodes): increase
  `count` on the matching `spec.nodes` entry. The operator scales the NodePool
  and keeps the NodeSet count in sync.
- **To add a new node type**: add a new entry under `spec.nodes` with a unique
  `name`, the `instanceType`, and the `count`. Each entry becomes one NodePool +
  one NodeSet.

```bash
kubectl edit sunkcluster <CLUSTER_NAME> -n tenant-slurm
```

```yaml
spec:
  nodes:
    - name: gpu           # existing group — bump count to absorb new capacity
      instanceType: gd-8xh100ib-i128
      count: 8            # was 4
    - name: cpu           # or add a new group for a new pool/instance type
      instanceType: cd-gp-a192-genoa
      count: 2
```

> `name` and `instanceType` are **immutable** after creation; `count` is
> mutable. To replace a node type, add a new entry and remove the old one.

After applying, watch the operator reconcile:

```bash
kubectl get sunkcluster <CLUSTER_NAME> -n tenant-slurm -o yaml | grep -A30 conditions:
```

`NodePoolsAvailable` goes `True` when the NodePools hit target; `NodeSetsAvailable`
goes `True` when the NodeSets have ready Pods. Then jump to **Step 6** to verify
in Slurm.

> On operator-managed clusters, the NodeSet's `name`, `replicas`, selector, and
> affinity are **generated** from the `SunkCluster.spec.nodes` entry, so the
> `DESIRED < FEASIBLE` (capped-replicas) and affinity-mismatch problems shouldn't
> arise from customer edits — they stay coupled by construction. If you still see
> a gap on a managed cluster, capacity was likely provisioned into a Node Pool
> that **isn't declared** on the `SunkCluster` (add it to `spec.nodes`), or the
> managed NodeSet was hand-edited and the operator hasn't reconciled it back yet.

<!-- TODO(SME): confirm that, on operator-managed clusters, capacity provisioned
     into a Node Pool that ISN'T declared on the SunkCluster is ignored by SUNK
     (i.e. the customer must add it to spec.nodes), and confirm the exact
     generated NodeSet replicas value the operator sets. Also confirm whether
     `kubectl edit sunkcluster` is the supported path vs. GitOps-only for managed
     clusters (some managed clusters are GitOps-driven and would revert a live
     edit). -->

---

## Step 5 — Fix (Helm / GitOps-managed NodeSet)

Apply the fix that matches the failure mode you found in Step 2.

> **GitOps clusters: change the source, not the cluster.** If NodeSets are
> managed by ArgoCD/Flux, a live `kubectl edit` / `kubectl scale` on the cluster
> is **reverted on the next sync**. Make the change in the values file in the
> customer's Git repo and sync the app (e.g. `argocd app sync <APP>`). The same
> applies to `kubectl label` on Nodes — manage labels via the Node Pool spec so
> they survive both sync and Node replacement.

### Fix mode 1 — `DESIRED < FEASIBLE`: raise the NodeSet `replicas`

The Nodes are eligible but the NodeSet isn't asking for them. Raise `replicas`
on the node definition so `slurmd` deploys to every matching Node. A common
CoreWeave pattern is to set `replicas` comfortably **higher** than the current
Node count (for example `1000`) so any Node that later joins the pool is picked
up automatically without another edit — the NodeSet only ever schedules up to
`numberFeasible` anyway.

```yaml
compute:
  nodes:
    my-gpu-nodes:
      enabled: true
      replicas: 1000        # >= the capacity you ever expect in this pool
```

Or, for a quick live change on a **non-GitOps** cluster:

```bash
kubectl scale nodeset <NODESET_NAME> -n tenant-slurm --replicas=<N>
```

<!-- TODO(SME): confirm the recommended `replicas` value/convention for customer
     clusters. The internal SA runbook recommends setting it high (e.g. 1000) so
     new Nodes auto-join; confirm there's no downside (e.g. cost, or a cap) to
     advising customers to over-provision `replicas` this way. -->

### Fix mode 2 — `FEASIBLE < DESIRED`: make the affinity/labels match

The NodeSet wants the Nodes but can't select them. There are two supported
directions — pick whichever side is easier for the customer to change:

#### Option A — make the NodeSet select the new Nodes (usually preferred)

Edit the NodeSet's `compute.nodes.<name>.affinity` (and `tolerations` if the pool
is tainted) so its **required** affinity selects the new Node Pool's label. The
selection key is typically `compute.coreweave.com/node-pool` set to the Node Pool
name, but use whatever label you confirmed on the Nodes in Step 3.

```yaml
compute:
  nodes:
    my-gpu-nodes:
      enabled: true
      affinity:
        nodeAffinity:
          requiredDuringSchedulingIgnoredDuringExecution:
            nodeSelectorTerms:
              - matchExpressions:
                  - key: compute.coreweave.com/node-pool
                    operator: In
                    values:
                      - <OLD_NODEPOOL_NAME>
                      - <NEW_NODEPOOL_NAME>   # add the new pool here
      # If the Node Pool applies a taint, the NodeSet must tolerate it:
      tolerations:
        - key: <NODEPOOL_TAINT_KEY>
          operator: Exists
```

Apply via the customer's normal path:
- **GitOps (ArgoCD):** commit the values change and sync the `slurm` app.
- **Direct Helm:** `helm upgrade <RELEASE> coreweave/slurm -n tenant-slurm -f values.yaml`
  (use the customer's actual release name, chart, and values files).

#### Option B — make the Node Pool carry the label/taint the NodeSet already expects

If the NodeSet already selects, say, `sunk.<org>/node: "true"`, then set that as
a `nodeLabel` (and matching `nodeTaint`) on the Node Pool so its Nodes match:

```yaml
apiVersion: compute.coreweave.com/v1alpha1
kind: NodePool
metadata:
  name: <NODEPOOL_NAME>
spec:
  # ...
  nodeLabels:
    sunk.<org>/node: "true"
  nodeTaints:
    - key: sunk.<org>/node
      value: "true"
      effect: NoSchedule
```

> Custom SUNK labels can't use the `coreweave.com`/`coreweave.cloud` prefixes,
> so dedicated-pool patterns use an org-specific prefix such as
> `sunk.<org>/node`. (The `apiVersion: compute.coreweave.com/v1alpha1`,
> `nodeLabels`, and `nodeTaints` fields on the Node Pool are from the CKS "Create
> a Node Pool" reference.) Whichever key you choose, the NodeSet
> affinity/tolerations and the Node Pool labels/taints must reference the
> **same** key and value — mind the casing (see the Step 3 casing gotcha).

<!-- TODO(SME): confirm the exact recommended label convention for customer
     dedicated SUNK pools. Internal SA usage shows both an org-scoped key
     (`sunk.<org>/node: "true"` + matching NoSchedule taint) and a legacy
     `sunk.coreweave.com/node` key, with inconsistent True/true casing. Confirm
     whether there's a single CoreWeave-standard public key customers should use.
     Sources: SA "NodeSets + NodePools" Confluence page and tars-support runbook. -->

#### Older clusters — manual label/taint may be required

On some **older** SUNK clusters that predate modern CKS Node Pool auto-labeling,
Nodes joining Slurm may need the label/taint applied **manually** because the
Node Pool doesn't propagate it automatically. The legacy pattern uses the
`sunk.coreweave.com/node` key:

```bash
kubectl label node <NODE_NAME> sunk.coreweave.com/node=True
kubectl taint node <NODE_NAME> sunk.coreweave.com/node=True:NoSchedule
```

This is error-prone and operationally painful — Nodes must be cordoned, drained,
labeled, tainted, and uncordoned individually, and a manually-applied label is
**lost when the Node is recycled**. Strongly prefer setting `nodeLabels` /
`nodeTaints` on the Node Pool (Option B) so labels survive Node replacement and
GitOps syncs. Treat direct `kubectl label`/`taint` as a last resort for clusters
that genuinely can't auto-label.

<!-- TODO(SME): confirm which cluster generations require manual labeling (the
     runbook cites an "RNO2 pattern"), the exact legacy key/casing, and whether
     there's a supported migration to auto-labeling Node Pools. Sourced from
     internal SA runbook. -->

After applying either fix mode, re-run the Step 2 check and confirm the gap
closes — `FEASIBLE`/`DESIRED` align to include the new Nodes, then
`CURRENT`/`READY` catch up as `slurmd` Pods schedule.

---

## Step 6 — Verify the nodes are schedulable in Slurm

Kubernetes-side success (`slurmd` Pods Ready) is necessary but not sufficient —
the whole point is that the capacity is usable **in Slurm**. Verify from inside a
Slurm login pod.

### 6.1 — Get a shell on a login pod

Preferred, if the customer has SSH set up:

```bash
kubectl get svc slurm-login -n tenant-slurm      # note EXTERNAL-IP
ssh <username>@<EXTERNAL-IP>
```

If SSH isn't configured, exec in directly (works without a directory service):

```bash
kubectl exec -it -n tenant-slurm <LOGIN_POD_NAME> -c sshd -- bash
```

<!-- TODO(SME): confirm the login container name (`sshd`) and login pod naming on
     customer clusters. Docs show `kubectl exec -it slurm-login-0 -c sshd -- bash`
     but pod names vary by group/user pod configuration. -->

### 6.2 — Confirm the nodes are present and available

```bash
sinfo -N -l
```

Then narrow to nodes that are actually available to run work:

```bash
sinfo -N --states=idle,mix
```

**Pass criteria:** the new nodes appear in `sinfo`, in a partition, in an `idle`
or `mix` state. `sinfo` counts should reflect the added capacity. Slurm node
names map 1:1 to the `slurmd` Pods (named from the Node's last two IP octets, so
e.g. Node IP `10.174.12.2` → `...-012-002`).

### 6.3 — If nodes appear but aren't usable, check their state

Look at any new node that is **not** `idle`/`mix`:

```bash
scontrol show node <NODE_NAME>
```

- **`drain` / `drained`**: the node won't accept new jobs. Check the reason
  (`sn` alias, or `scontrol show node`). If the drain reason contains
  `sunk:verify-undrain`, SUNK auto-undrains it after the next hourly HPC
  Verification health check — no action needed. If it's a `k8s:` reason it's
  usually transient. Otherwise, once the underlying issue is fixed, undrain:
  ```bash
  scontrol update nodename=<NODE_NAME> state=resume
  ```
- **`INVAL`**: the node's reported resources/features don't match `slurm.conf`
  (e.g. a GPU/feature mismatch, or `slurmd` needs a restart). See CoreWeave's
  `INVAL` state guidance; commonly resolved by re-registering the node (restart
  its `slurmd` by deleting the Pod: `kubectl -n tenant-slurm delete pod
  <SLURM_NODE_NAME>`).
- **`down*` / `drain*`** (trailing `*`): the node isn't responding / the Pod
  isn't fully connected yet — give it time.

### 6.4 — Prove it end to end with a tiny job

The definitive test that capacity is usable is running a job on it:

```bash
srun -N 1 -w <NEW_NODE_NAME> hostname
```

If you want to target the whole added capacity, run across N of the new nodes:

```bash
srun -N <N> hostname
```

**Pass criteria:** the job schedules onto the new node(s) and returns their
hostname(s) without a "requested node configuration is not available" or pending
error. For a heavier, still-simple validation the customer can adapt the
`training/slurm/torch-allreduce` example sbatch from the
[reference architecture](https://github.com/coreweave/reference-architecture/tree/main/training/slurm/torch-allreduce),
but the `srun ... hostname` check above is enough to confirm activation.

---

## Verification summary

Report results to the customer as a table:

| Check | Command | Pass criteria | Status |
|-------|---------|---------------|--------|
| Node Pool delivered | `kubectl get nodepool` | `CURRENT == TARGET`, capacity/quota OK | |
| K8s Nodes Ready + production | `kubectl get nodes -L node.coreweave.cloud/state` | New Nodes `Ready`, `state=production` | |
| NodeSet feasibility | Step 2 custom-columns query | `FEASIBLE == DESIRED` (includes new Nodes) | |
| slurmd Pods scheduled | Step 2 (`CURRENT`/`READY`) | `READY == CURRENT` reflects new Nodes | |
| Visible in Slurm | `sinfo -N --states=idle,mix` | New nodes present, `idle`/`mix` | |
| Schedulable in Slurm | `srun -N <N> hostname` | Job runs on new node(s) | |

The capacity is activated when the last two rows pass. Report the new total
schedulable node count so the customer can see the capacity they paid for is now
usable.

---

## Common mistakes

**Assuming capacity auto-joins Slurm.** Provisioning Nodes into a CKS Node Pool
makes them live in Kubernetes, not in Slurm. On Helm/GitOps-managed clusters a
NodeSet must select them; on `SunkCluster`-managed clusters they must be declared
on the `SunkCluster`. Idle, healthy Nodes with no Slurm presence is the norm
until this link is made.

**Scaling the Node Pool but not the NodeSet.** These are independent resources.
Bumping the Node Pool's `targetNodes` adds Kubernetes Nodes; it does not add
Slurm nodes unless the NodeSet's `replicas` is high enough to deploy `slurmd` to
them. If `sinfo` shows fewer nodes than the pool has, check for `DESIRED < FEASIBLE`
(Step 2, fix mode 1) — this is not a Slurm registration bug.

**Reading a NodeSet-affinity gap as a quota cut.** `FEASIBLE` stuck below
`DESIRED` (by exactly a Node Pool's worth of Nodes) is almost always an
affinity/label mismatch, not lost quota. Check the labels before escalating a
capacity ticket. (Only suspect a real capacity/quota shortfall if
`kubectl get nodepool` shows `QUOTA=Over` / `CAPACITY=CapacityUnknown`, or the
pool's `TARGET` itself is below what's needed — then it's a CKS capacity issue,
not a NodeSet issue.)

**Confusing SUNK with SchedMD Slinky.** SchedMD ships a separate, unrelated
Slurm-on-Kubernetes operator called **Slinky** that also has a CRD named
`NodeSet` and uses the `slinky.slurm.net/*` label domain. On CoreWeave, always
use SUNK's `sunk.coreweave.com/*` and `compute.coreweave.com/node-pool` labels —
never Slinky labels.

**Using `nodeSelector` on the NodeSet.** It doesn't propagate to the `slurmd`
Pods. Use `affinity` (required) and `tolerations`.

**Only editing preferred affinity.** The NodeSet controller evaluates only the
*required* portion of node affinity. A preferred rule won't make Nodes feasible.

**Hand-editing a managed NodeSet.** On `SunkCluster`-managed clusters the
operator reconciles NodeSets back to match the `SunkCluster`. Change the
`SunkCluster`, not the NodeSet.

**Forgetting the tolerations when the pool is tainted.** If the Node Pool applies
a `NoSchedule` taint, matching affinity alone isn't enough — the NodeSet must also
tolerate the taint or `slurmd` can't land.

**Manually labeling Nodes on a pool that auto-labels.** The manual label is lost
on Node replacement. Set `nodeLabels`/`nodeTaints` on the Node Pool instead so it
survives recycling; reserve `kubectl label`/`taint` for older clusters that can't
auto-label.

**Wrong kubectl context.** Multi-cluster kubeconfigs mean every command can
silently hit the wrong cluster. Re-verify `kubectl config current-context` before
edits.

---

## Troubleshooting

**`FEASIBLE` won't rise after the fix.** Re-confirm the new Nodes carry the exact
label value the NodeSet's *required* affinity selects
(`kubectl get nodes -l compute.coreweave.com/node-pool=<POOL> --show-labels`), and
that any Node Pool taint is tolerated. Confirm the Nodes are in
`node.coreweave.cloud/state=production`.

**`FEASIBLE` rose but `CURRENT` won't catch up.** A `slurmd` Pod may be Pending —
inspect it: `kubectl describe pod -n tenant-slurm <POD>` and read the Events for
the scheduling mismatch (insufficient resources, untolerated taint, or Node
locked by another NodeSet — only one NodeSet uses a Node at a time).

**Nodes are in Slurm but every job pends.** Try restarting the Slurm Controller
(safe; doesn't cancel running jobs): find it with
`kubectl get deployments -l app.kubernetes.io/component=controller -n tenant-slurm`
then `kubectl rollout restart deployment <NAME> -n tenant-slurm`.

**New nodes show `INVAL` in `sinfo`.** Resource/feature mismatch between
`slurm.conf` and `slurmd`. Restart the node's `slurmd` by deleting its Pod so it
re-registers: `kubectl -n tenant-slurm delete pod <SLURM_NODE_NAME>`. See
CoreWeave's `INVAL` state guidance if it persists.

**New nodes are `drained`.** Check the reason with `scontrol show node <NODE>`.
`sunk:verify-undrain` auto-recovers after the next health check; `k8s:` reasons
are usually transient; other reasons need the underlying issue fixed, then
`scontrol update nodename=<NODE> state=resume`.

---

## References

- `references/nodeset-nodepool-reference.md` — the Node Pool ↔ NodeSet mapping,
  label/taint matching, diagnostic commands, and both deployment models in one
  place.
- [SUNK NodeSet CRD](https://docs.coreweave.com/products/sunk/discover_sunk/nodeset)
- [SUNK Node Controller (labels SUNK manages)](https://docs.coreweave.com/products/sunk/discover_sunk/node-controller)
- [Configure SUNK compute nodes](https://docs.coreweave.com/products/sunk/deploy_sunk/configure-compute-nodes)
- [Create a SUNK cluster (SunkCluster CR + verify)](https://docs.coreweave.com/products/sunk/deploy_sunk/create-sunk-cluster)
- [SunkCluster reference](https://docs.coreweave.com/products/sunk/reference/sunkcluster-reference)
- [Drain and undrain Slurm nodes](https://docs.coreweave.com/products/sunk/manage_sunk/drain-and-undrain-nodes)
- [Slurm node states](https://docs.coreweave.com/products/sunk/manage_sunk/slurm-node-states)
- [Create a CKS Node Pool (labels/taints)](https://docs.coreweave.com/products/cks/nodes/create)
- [Node Pool status](https://docs.coreweave.com/products/cks/nodes/nodepool-status)
- [Connect to a Slurm login node](https://docs.coreweave.com/products/sunk/access_sunk/connect-to-slurm-login-node)
