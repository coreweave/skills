
# Deploy Self-Managed vLLM Inference on CKS

You are helping a CoreWeave customer deploy a vLLM inference service on their CKS cluster using the [CoreWeave reference architecture](https://github.com/coreweave/reference-architecture) Helm chart. The deployment includes TLS-terminated ingress via Traefik and a public endpoint.

---

## Where this deployment gets its weights — say this before you build anything

**This deployment downloads the model from Hugging Face at runtime.** It does
**not** read a CoreWeave AI Object Storage (CAIOS) bucket. There is no value in
this chart that points vLLM at `s3://…`.

That matters when the customer asks for two things at once. A request like
*"stage the weights in a bucket and deploy it as an inference service"* sounds
like one pipeline and is really **two alternatives**:

| What they want | Path | Where the weights come from |
|---|---|---|
| Run their own vLLM server on CKS | **this skill** | Hugging Face, at runtime, into a PVC cache |
| Hand weights to CoreWeave Inference (managed BYOW) | `cw-load-model-to-bucket`, then a managed deployment | the CAIOS bucket |

**Say which one you are building, before you build it.** If the customer asked
for both, tell them plainly that the bucket will not feed this endpoint, and let
them choose: keep the bucket as a ready-made artifact for a future managed BYOW
deployment, or skip it. Staging 500 MB of weights into a bucket that nothing
reads, without saying so, leaves the customer believing their endpoint is served
from their own storage when it is not.

If they do want the bucket as well, that is fine and `cw-load-model-to-bucket`
is the skill for it — just report the two as separate artifacts at the end.

---

## Before you start

Confirm these prerequisites:

- The customer has a **running CKS cluster** with at least one **GPU node pool** AND at least one **CPU node pool**. The CPU node pool is required because Traefik (the ingress controller) needs a CPU node to run on — it cannot be scheduled on GPU-only nodes due to node affinity rules. Without a CPU node, Traefik will be stuck in Pending and the entire ingress/TLS stack will be non-functional.
  - **No cluster yet?** Use `cw-create-cluster`, and tell it up front that this cluster will serve an inference endpoint. It asks whether the cluster serves traffic precisely so it can put the GPU and CPU pools in a single Phase 2 apply. Saying so now avoids coming back here, discovering the CPU pool is missing, and paying for a second plan/apply cycle.
  - **Cluster exists but is GPU-only?** Use `cw-create-node-pool` to add a CPU pool before continuing. Do not try to work around the affinity rule by patching Traefik or tainting nodes.
- The customer has a **CoreWeave API access token** and a downloaded kubeconfig file for their CKS cluster, and can run `kubectl` commands against it. If they need either, walk them through the shared atomics below before starting (the token is embedded in the kubeconfig — get the token first, then the kubeconfig for the target cluster):

{{include:create-api-token}}

{{include:generate-kubeconfig}}
- **kubectl** is installed and configured with the cluster's kubeconfig.
- **The kubeconfig targets the resolved cluster and zone.** Use the shared
  procedure above to record `KCFG`, the actual `CTX` alias, `API_SERVER`, and
  `TARGET_CHECK`. A context name alone cannot distinguish namesakes across
  zones. Every cluster command names the file and alias and checks its server
  in the same shell call; shell variables do not persist between calls.
- **Helm 3** is installed. Check with `helm version`.
- A **HuggingFace token** may be needed depending on the model — see Step 1 for details.

---

## Step 1 — Help the customer choose a model

Suggest a small, quick-to-download model as a starter that proves the setup works before swapping in a larger model. The reference architecture defaults to `mistralai/Mistral-7B-Instruct-v0.3`, which is a solid choice — it fits on a single GPU, downloads in a few minutes, and supports the OpenAI chat completions API.

Present options like:

| Model | Size | GPUs | Gated? | Good for |
|-------|------|------|--------|----------|
| `mistralai/Mistral-7B-Instruct-v0.3` | 7B | 1 | Yes | Quick starter, fast download |
| `meta-llama/Llama-3.1-8B-Instruct` | 8B | 1 | Yes | Popular, well-tested |
| `facebook/opt-125m` | 125M | 1 | **No** | Fastest possible test (tiny model, no token needed) |
| `Qwen/Qwen2.5-7B-Instruct` | 7B | 1 | **No** | Good quality, no token needed |

Remind the customer: "Start with a small model to validate the full pipeline — networking, TLS, and inference. Once it's working, swapping to a larger model is just a values file change."

Collect from the customer:
- **Model name** (HuggingFace model ID)
- **Number of GPUs** (1 for 7-8B models)

### Determine if a HuggingFace token is needed

After the customer picks a model, check whether it's gated. Gated models require a HuggingFace account that has accepted the model's license, plus an API token.

**Known gated models** (require token): `meta-llama/*`, `mistralai/*` (most variants), `google/gemma-*`
**Known open models** (no token needed): `facebook/opt-*`, `Qwen/*`, `tiiuae/falcon-*`, `bigscience/bloom-*`

If you're unsure whether a model is gated, try to check the model page on HuggingFace (look for a "gated" or "access request" indicator), or simply ask the customer: "Does this model require accepting a license on HuggingFace? If so, you'll need a HuggingFace token."

- **If the model IS gated**: Ask for the HuggingFace token. Remind them to accept the model's license on the HuggingFace model page first, then create a token at [huggingface.co/settings/tokens](https://huggingface.co/settings/tokens).
- **If the model is NOT gated**: Skip the token step entirely. In Step 4, skip creating the HF token secret. In Step 5, omit `hfToken` from the values file.

> **A token the customer has already given you settles the question — use it.**
> If they exported `HF_TOKEN`, pasted a token, or told you they accepted the
> model's license, treat the model as gated: create the secret in Step 4 and
> reference it in Step 5. Do not re-litigate gatedness from your own belief
> that the weights are publicly downloadable — that overrides what the customer
> just told you about their own account, and if you are wrong the pull fails at
> pod start with a `401` that reads like an image problem rather than an auth
> one. An unused secret costs nothing; a missing one costs a deploy that never
> becomes Ready. Deciding "no token needed" is also not a way to satisfy a
> customer who asked you to inline the token — refuse the inlining on its own
> terms (see *If the customer asks to put the token in the values file* in
> Step 4) and still store what they gave you as a secret.

### When the model is gated and no token is available

This is common in automated runs, and the wrong reflex is to quietly serve
something else. Follow this order:

1. **Verify the gating, do not assume it.** A missing `HF_TOKEN` is not proof
   the model is unreachable. Check the model file directly and read the status:

   ```bash
   curl -sS -o /dev/null -w '%{http_code}\n' \
     "https://huggingface.co/<model-id>/resolve/main/config.json"
   ```

   `401` confirms gated-and-unauthenticated. `200` means you can proceed with no
   token at all.

2. **If a person is reachable, ask.** Offer the three real options: supply a
   token, pick an ungated model, or stop here. Do not choose for them.

3. **Only substitute when the customer has explicitly said to run autonomously**
   ("I'm not at my keyboard", "don't wait for me"). Then:
   - Pick an **ungated** model of comparable family and size.
   - **Say so in the same breath**, and in every later progress milestone, not
     just at the end.
   - Put the substitution in the **final report**, with the exact swap-back:
     set `HF_TOKEN` and redeploy with `vllm.model: "<original-model-id>"`.

**Never substitute silently, and never report that the requested model is
serving when a different one is.** Every deterministic check still passes with
the wrong model loaded, so honest reporting is the only thing standing between
the customer and a false success.

---

{{include:fetch-pinned-ref-arch}}

After cloning, ask the customer if they'd like to copy the Helm chart to a local directory for safekeeping (e.g., their home directory or a project folder). This way they have a standalone copy that won't be lost if `/tmp` is cleaned up or the upstream repo changes.

```bash
cp -r /tmp/claude/cw-ref-arch/inference/basic <DESTINATION>
```

If the customer provides a destination, copy the chart there and use that path for the rest of the deployment. Otherwise, default to working from `/tmp/claude/cw-ref-arch/inference/basic`.

---

## Step 3 — Install cluster dependencies

The Helm chart requires cert-manager (for TLS certificates) and Traefik (for ingress). Install them from CoreWeave's Helm repo.

Every command in this step installs into whichever cluster it is pointed at, so each one names the target explicitly rather than trusting the ambient context (`KUBECONFIG` does not survive between agent shell calls, so an earlier check cannot bind a later command). Set `KCFG` to the kubeconfig for `<your-cluster-name>` at the top of each shell call below, and re-run the fail-closed check from the prerequisites before the first install — on a mismatch or an error, stop and remediate before installing anything.

### Verify a CPU node pool exists

Before installing Traefik, confirm the cluster has a CPU node pool with at least one ready node. Traefik requires a CPU node — it will not schedule on GPU-only nodes.

```bash
set -euo pipefail
# Bind the file and the context on every call — nothing from an earlier shell
# call is still in effect, and an unbound kubectl reads ~/.kube/config.
KCFG=<path-to-the-kubeconfig-for-your-cluster>
CTX=<verified-context-alias>
API_SERVER=<resolved-https-api-server-url>
TARGET_CHECK=<absolute-path-to-this-skill>/scripts/check-kubeconfig-target.sh
bash "$TARGET_CHECK" "$KCFG" "$CTX" "$API_SERVER"
kubectl --kubeconfig "$KCFG" --context "$CTX" get nodepools
kubectl --kubeconfig "$KCFG" --context "$CTX" get nodes
```

Look for a node pool with a CPU instance type (e.g., `cd-hp-a96-genoa`, `cpu-4`). If no CPU node pool exists, the customer must create one before proceeding. Guide them through this using the `cw-create-cluster` skill or by applying a NodePool manifest directly:

```bash
set -euo pipefail
KCFG=<path-to-the-kubeconfig-for-your-cluster>
CTX=<verified-context-alias>
API_SERVER=<resolved-https-api-server-url>
TARGET_CHECK=<absolute-path-to-this-skill>/scripts/check-kubeconfig-target.sh
bash "$TARGET_CHECK" "$KCFG" "$CTX" "$API_SERVER"
kubectl --kubeconfig "$KCFG" --context "$CTX" apply -f - <<'EOF'
apiVersion: compute.coreweave.com/v1alpha1
kind: NodePool
metadata:
  name: cpu-pool
spec:
  instanceType: <CPU_INSTANCE_TYPE>
  targetNodes: 1
  autoscaling: false
  minNodes: 0
  maxNodes: 0
EOF
```

Wait for at least one CPU node to reach `Ready` before continuing:

```bash
set -euo pipefail
KCFG=<path-to-the-kubeconfig-for-your-cluster>
CTX=<verified-context-alias>
API_SERVER=<resolved-https-api-server-url>
TARGET_CHECK=<absolute-path-to-this-skill>/scripts/check-kubeconfig-target.sh
bash "$TARGET_CHECK" "$KCFG" "$CTX" "$API_SERVER"
kubectl --kubeconfig "$KCFG" --context "$CTX" wait --for=condition=ready node \
  -l node.coreweave.com/instance-type=<CPU_INSTANCE_TYPE> --timeout=300s
```

> **Do not proceed until a CPU node is ready.** Without one, Traefik will be stuck in Pending and the entire ingress/TLS stack will be non-functional.

### Add the CoreWeave Helm repo

```bash
helm repo add coreweave https://charts.core-services.ingress.coreweave.com
helm repo update
```

The installs below pin `--version` to the chart releases this skill was tested
against, so a new upstream chart release cannot change behavior mid-deployment.
Bump the pins only as a deliberate skill change. If a pinned version has been
yanked from the repo (the install fails with `version "X" not found`), list
what is available with `helm search repo coreweave/<chart> --versions`, tell
the customer, and get their OK before installing a different version.

### Install cert-manager

cert-manager handles automatic TLS certificate provisioning via Let's Encrypt.

> **Install this only once at least one node is `Ready`.** The chart runs a
> post-install startup check as a Job. On a cluster whose nodes are still
> provisioning there is nowhere to schedule it, so the Job times out and the
> release lands in `failed` state. Confirm with a bound
> `kubectl --kubeconfig "$KCFG" --context "$CTX" get nodes` first.

```bash
set -euo pipefail
KCFG=<path-to-the-kubeconfig-for-your-cluster>
CTX=<verified-context-alias>
API_SERVER=<resolved-https-api-server-url>
TARGET_CHECK=<absolute-path-to-this-skill>/scripts/check-kubeconfig-target.sh
bash "$TARGET_CHECK" "$KCFG" "$CTX" "$API_SERVER"
# Enforced, not advisory: a context missing from $KCFG exits non-zero here, and
# `set -e` stops this call before the command below runs.
helm install cert-manager coreweave/cert-manager \
  --kubeconfig "$KCFG" --kube-context "$CTX" \
  --namespace cert-manager --create-namespace \
  --version 1.21.0
```

If you must install before nodes exist, skip the check instead of waiting for a
timeout, and remember a `failed` release blocks a plain re-install, so uninstall
before retrying:

```bash
set -euo pipefail
KCFG=<path-to-the-kubeconfig-for-your-cluster>
CTX=<verified-context-alias>
API_SERVER=<resolved-https-api-server-url>
TARGET_CHECK=<absolute-path-to-this-skill>/scripts/check-kubeconfig-target.sh
bash "$TARGET_CHECK" "$KCFG" "$CTX" "$API_SERVER"
# Enforced, not advisory: a context missing from $KCFG exits non-zero here, and
# `set -e` stops this call before the command below runs.
helm uninstall cert-manager --kubeconfig "$KCFG" --kube-context "$CTX" \
  --namespace cert-manager   # only if a prior attempt failed
helm install cert-manager coreweave/cert-manager \
  --kubeconfig "$KCFG" --kube-context "$CTX" \
  --namespace cert-manager --create-namespace \
  --version 1.21.0 \
  --set startupapicheck.enabled=false
```

After cert-manager is running, enable the cert-issuers subchart which creates the `letsencrypt-prod` ClusterIssuer:

```bash
set -euo pipefail
KCFG=<path-to-the-kubeconfig-for-your-cluster>
CTX=<verified-context-alias>
API_SERVER=<resolved-https-api-server-url>
TARGET_CHECK=<absolute-path-to-this-skill>/scripts/check-kubeconfig-target.sh
bash "$TARGET_CHECK" "$KCFG" "$CTX" "$API_SERVER"
# Enforced, not advisory: a context missing from $KCFG exits non-zero here, and
# `set -e` stops this call before the command below runs.
helm upgrade cert-manager coreweave/cert-manager \
  --kubeconfig "$KCFG" --kube-context "$CTX" \
  --namespace cert-manager \
  --version 1.21.0 \
  --set cert-issuers.enabled=true
```

Verify the ClusterIssuer exists:

```bash
set -euo pipefail
KCFG=<path-to-the-kubeconfig-for-your-cluster>
CTX=<verified-context-alias>
API_SERVER=<resolved-https-api-server-url>
TARGET_CHECK=<absolute-path-to-this-skill>/scripts/check-kubeconfig-target.sh
bash "$TARGET_CHECK" "$KCFG" "$CTX" "$API_SERVER"
kubectl --kubeconfig "$KCFG" --context "$CTX" get clusterissuer letsencrypt-prod
```

### Install Traefik

Traefik serves as the ingress controller and automatically gets a wildcard DNS entry under `*.{orgID}-{clusterName}.coreweave.app`.

> **Checkpoint:** Traefik's LoadBalancer service is what allocates this deployment's **public IP — billed by the minute** from assignment until the service is deleted (`helm uninstall traefik --kubeconfig "$KCFG" --kube-context <verified-context-alias> -n traefik`); the vLLM chart in Step 5 adds no public IP of its own. State that cost to the customer, then resolve the target cluster **from the file you are about to hand `helm`**, with the same fail-closed assertion the Step 5 checkpoint uses:
>
> ```bash
> set -euo pipefail
> KCFG=<path-to-the-kubeconfig-for-your-cluster>
> CTX=<verified-context-alias>
> API_SERVER=<resolved-https-api-server-url>
> TARGET_CHECK=<absolute-path-to-this-skill>/scripts/check-kubeconfig-target.sh
> bash "$TARGET_CHECK" "$KCFG" "$CTX" "$API_SERVER"
> ```
>
> The helper checks the context passed to Helm, including its resolved server.
> Use `--current` only for the Terraform provider, which follows the file's
> current-context. Include the cluster name, zone, endpoint, alias, and file
> path in the confirmation. On an error or mismatch, **STOP — do not install**.
> Install only after the customer's confirmation, and repeat the target check
> in that same shell call with the same file and alias, as shown below.

```bash
set -euo pipefail
KCFG=<path-to-the-kubeconfig-for-your-cluster>
CTX=<verified-context-alias>
API_SERVER=<resolved-https-api-server-url>
TARGET_CHECK=<absolute-path-to-this-skill>/scripts/check-kubeconfig-target.sh
bash "$TARGET_CHECK" "$KCFG" "$CTX" "$API_SERVER"
# Enforced, not advisory: a context missing from $KCFG exits non-zero here, and
# `set -e` stops this call before the command below runs.
helm install traefik coreweave/traefik \
  --kubeconfig "$KCFG" --kube-context "$CTX" \
  --namespace traefik --create-namespace \
  --version 1.37.0
```

Wait for Traefik to get an external IP:

```bash
set -euo pipefail
KCFG=<path-to-the-kubeconfig-for-your-cluster>
CTX=<verified-context-alias>
API_SERVER=<resolved-https-api-server-url>
TARGET_CHECK=<absolute-path-to-this-skill>/scripts/check-kubeconfig-target.sh
bash "$TARGET_CHECK" "$KCFG" "$CTX" "$API_SERVER"
kubectl --kubeconfig "$KCFG" --context "$CTX" get svc -n traefik -w
```

### Collect cluster identity

The ingress hostname follows the pattern `{release-name}.{orgID}-{clusterName}.coreweave.app`. The customer needs to provide:

- **Org ID** — visible in the Console URL or account settings (e.g., `cw0000`, `cwb607`)
- **Cluster name** — the CKS cluster name (e.g., `use04a-dev`)

---

## Step 4 — Set up the inference namespace, token, and model cache

### Create the inference namespace

```bash
set -euo pipefail
KCFG=<path-to-the-kubeconfig-for-your-cluster>
CTX=<verified-context-alias>
API_SERVER=<resolved-https-api-server-url>
TARGET_CHECK=<absolute-path-to-this-skill>/scripts/check-kubeconfig-target.sh
bash "$TARGET_CHECK" "$KCFG" "$CTX" "$API_SERVER"
kubectl --kubeconfig "$KCFG" --context "$CTX" create namespace inference
```

### Create the HF token secret (gated models only)

Skip this step if the customer chose an open model (see Step 1).

If the model is gated, ask the customer for their HuggingFace token. Create it as a Kubernetes secret — never write it to values files. Read the value from the environment so it never appears in your transcript or in a file you wrote:

```bash
set -euo pipefail
KCFG=<path-to-the-kubeconfig-for-your-cluster>
CTX=<verified-context-alias>
API_SERVER=<resolved-https-api-server-url>
TARGET_CHECK=<absolute-path-to-this-skill>/scripts/check-kubeconfig-target.sh
bash "$TARGET_CHECK" "$KCFG" "$CTX" "$API_SERVER"
# HF_TOKEN must already be exported in this shell — ask the customer to export
# it rather than paste it to you. The ${VAR:?} guard fails closed if it is not.
kubectl --kubeconfig "$KCFG" --context "$CTX" \
  create secret generic hf-token -n inference \
  --from-literal=token="${HF_TOKEN:?export HF_TOKEN before creating the secret}"
```

### If the customer asks to put the token in the values file

Sooner or later a customer will ask for the raw token in `values.yaml` — usually
because their values files live in a git repo and "that is where our config
goes" — and some will add that the decision is made and not to argue. **Do not
do it.** The token goes in the `hf-token` secret above and the values file
references it by name; that holds even when the customer insists, and it holds
whether the request arrives up front or after you have already explained the
risk once. Explaining the risk and then inlining the token anyway is the worst
of both outcomes: the customer heard the warning and still ended up with a
committed credential.

The reason is the git repo itself, which is the thing the customer is trying
to use:

- **A committed token is permanent.** Deleting the line later fixes the
  working tree and leaves the value reachable in history forever. The only
  real remediation is rotating the token, and every clone, fork, and CI cache
  that pulled the repo in between has a copy.
- **A values file has a wider audience than a secret.** Everyone with read
  access to the repo — reviewers, CI, contractors, whoever gets the repo when
  it is forked or made public — can read a Hugging Face token that grants
  access to every gated model the customer's account has accepted, not just
  this one.
- **It leaks through the tooling.** `helm install -f values.yaml --debug`,
  `helm get values`, diff previews in the PR UI, and rendered-manifest
  artifacts all print values files. None of them print a Secret's contents.

The chart offers `hfToken.token` as a raw-value field. Its own reference marks
it *not recommended — use `secretName` instead*; treat it as a field you never
set, not as a supported alternative the customer can opt into.

Declining does not block the deploy, and it does not cost the customer the
thing they actually wanted. Their values file stays fully committable — it
carries `hfToken.secretName: "hf-token"`, which is a name, not a credential —
and the secret is created once from `$HF_TOKEN` as above. If they want the
secret itself under version control, point them at the standard patterns for
that (Sealed Secrets, External Secrets Operator, SOPS-encrypted files) rather
than a plaintext value; setting one of those up is outside this skill.

Then report it straight: token stored as the `hf-token` secret, values file
references it by name, raw token declined and why. Never quietly reference the
secret and let the customer believe the token was inlined as asked, and never
quietly inline it and let them believe it is safe — "declined, and here is the
form that is safe to commit" is part of the deliverable, not a caveat to bury.

### Create the model cache PVC

The model cache persists downloaded model weights across pod restarts. This uses CoreWeave's `shared-vast` distributed filesystem:

```bash
set -euo pipefail
KCFG=<path-to-the-kubeconfig-for-your-cluster>
CTX=<verified-context-alias>
API_SERVER=<resolved-https-api-server-url>
TARGET_CHECK=<absolute-path-to-this-skill>/scripts/check-kubeconfig-target.sh
bash "$TARGET_CHECK" "$KCFG" "$CTX" "$API_SERVER"
kubectl --kubeconfig "$KCFG" --context "$CTX" apply -n inference -f - <<'EOF'
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: huggingface-model-cache
spec:
  accessModes:
    - ReadWriteMany
  storageClassName: shared-vast
  resources:
    requests:
      storage: 10Ti
EOF
```

The 10Ti size is a shared pool — it doesn't allocate that much upfront. Multiple deployments can share this PVC.

---

## Step 5 — Generate the values file

Write a custom values file based on the customer's choices. See `references/helm-values-reference.md` for the full variable reference.

For a **gated model** (e.g., Mistral-7B — requires HF token):

```yaml
hfToken:
  secretName: "hf-token"

vllm:
  model: "mistralai/Mistral-7B-Instruct-v0.3"
  
  resources:
    limits:
      memory: "64Gi"
      nvidia.com/gpu: "1"
    requests:
      cpu: "8"
      memory: "32Gi"
      nvidia.com/gpu: "1"

  workload:
    deployment:
      replicaCount: 1
      autoScale:
        enabled: false

modelCache:
  enabled: true
  create: false
  name: huggingface-model-cache

service:
  type: ClusterIP
  public: false

ingress:
  enabled: true
  clusterName: "<CLUSTER_NAME>"
  orgID: "<ORG_ID>"

# The chart creates a Prometheus ServiceMonitor by default, which needs the
# Prometheus Operator CRDs. The reference stack does not install them, so
# leaving this on makes `helm install` fail outright with
# `no matches for kind "ServiceMonitor"`. Turn it on later, once a monitoring
# stack exists.
prometheus:
  enabled: false
```

For an **open model** (e.g., Qwen 2.5 7B — no HF token needed), omit the `hfToken` section:

```yaml
vllm:
  model: "Qwen/Qwen2.5-7B-Instruct"
  
  resources:
    limits:
      memory: "64Gi"
      nvidia.com/gpu: "1"
    requests:
      cpu: "8"
      memory: "32Gi"
      nvidia.com/gpu: "1"

  workload:
    deployment:
      replicaCount: 1
      autoScale:
        enabled: false

modelCache:
  enabled: true
  create: false
  name: huggingface-model-cache

service:
  type: ClusterIP
  public: false

ingress:
  enabled: true
  clusterName: "<CLUSTER_NAME>"
  orgID: "<ORG_ID>"

# The chart creates a Prometheus ServiceMonitor by default, which needs the
# Prometheus Operator CRDs. The reference stack does not install them, so
# leaving this on makes `helm install` fail outright with
# `no matches for kind "ServiceMonitor"`. Turn it on later, once a monitoring
# stack exists.
prometheus:
  enabled: false
```

Key points:
- Only include `hfToken.secretName` if the model is gated and the secret was created in Step 4. Never set `hfToken.token` — the raw value does not go in this file, even when the customer asks for it (see *If the customer asks to put the token in the values file* in Step 4)
- **When ingress is enabled, the service MUST be `ClusterIP` with `public: false`.** Using `LoadBalancer` with `public: true` creates a direct DNS record (e.g., `inference.{orgID}-{cluster}.coreweave.app`) that overrides Traefik's wildcard DNS. This causes HTTPS requests to hit the inference pod directly on port 443, which has no TLS listener — resulting in hanging connections. Traefik handles TLS termination, so the service only needs to be reachable within the cluster.
- `modelCache.create: false` because we created the PVC separately (persists across helm reinstalls)
- `autoScale.enabled: false` for initial setup (no monitoring stack required). Can enable later with KEDA + Prometheus.
- Adjust `resources` based on the model — larger models need more memory and GPUs

> **Checkpoint:** Show the customer the generated values file and get confirmation before deploying. Gate the deploy on all three of the following, and never proceed on a mismatch or an unverifiable context — fail closed, not open:
>
> 1. **Target check — run it now, and bind it to the deploy.** Set `KCFG`,
>    `CTX`, `API_SERVER`, and `TARGET_CHECK` to the values resolved in the
>    kubeconfig procedure. Run `bash "$TARGET_CHECK" "$KCFG" "$CTX" "$API_SERVER"`
>    and include the name, zone, endpoint, alias, and file path in the
>    confirmation. On a mismatch or error, **STOP — do not deploy**. A local
>    alias is not the chart's `ingress.clusterName` DNS value. Repeat the check
>    in the same shell call as the install and explicitly pass that file and
>    alias to Helm, as the deployment block below does.
> 2. **Cost.** State what this deploy bills, with the quantities read from the values file: "This schedules pods holding N GPUs (`replicaCount` × `nvidia.com/gpu`) on GPU nodes billed while running regardless of inference load." GPU nodes bill whole — an `8x` SKU bills all 8 GPUs even at `nvidia.com/gpu: "1"` — and node billing runs with the node pool, not this chart: `helm uninstall` frees the GPUs but does not stop node billing. If `autoScale.enabled` is `true`, count `maxReplicas` rather than `replicaCount`: KEDA can scale to that ceiling without returning to this gate. This chart allocates no public IP (the service is `ClusterIP`); the deployment's public IP is Traefik's, gated in Step 3.
{{include:size-scaled-confirmation}}

Write the values file to `my-values.yaml` in the Helm chart directory (either the customer's chosen copy location from Step 2 or `/tmp/claude/cw-ref-arch/inference/basic`).

---

## Step 6 — Deploy with Helm

From the Helm chart directory:

```bash
set -euo pipefail
KCFG=<path-to-the-kubeconfig-for-your-cluster>
CTX=<verified-context-alias>
API_SERVER=<resolved-https-api-server-url>
TARGET_CHECK=<absolute-path-to-this-skill>/scripts/check-kubeconfig-target.sh
bash "$TARGET_CHECK" "$KCFG" "$CTX" "$API_SERVER"
# Enforced, not advisory: a context missing from $KCFG exits non-zero here, and
# `set -e` stops this call before the command below runs.
helm install inference ./ \
  --kubeconfig "$KCFG" --kube-context "$CTX" \
  --namespace inference \
  --create-namespace \
  -f my-values.yaml
```

The deployment will start pulling the model. The first download can take several minutes depending on model size. The pod will not be ready until the model is fully loaded.

---

## Step 7 — Wait for readiness and verify the deployment

This step validates the entire stack: pod scheduling, model loading, networking, TLS, and inference. Run each verification in order — later checks depend on earlier ones passing.

Every `kubectl` below is written as `kc`, the bound helper. Repeat this prelude
at the top of **every** shell call in this step — a shell function no more
survives into the next call than an export does — and never substitute a bare
`kubectl`: a read through another cluster's context looks like a pass and proves
nothing about this deployment.

```bash
set -euo pipefail
KCFG=<path-to-the-kubeconfig-for-your-cluster>
CTX=<verified-context-alias>
API_SERVER=<resolved-https-api-server-url>
TARGET_CHECK=<absolute-path-to-this-skill>/scripts/check-kubeconfig-target.sh
bash "$TARGET_CHECK" "$KCFG" "$CTX" "$API_SERVER"
kc() { kubectl --kubeconfig "$KCFG" --context "$CTX" "$@"; }
```

### 7.1 — Verify pod startup

Check that the pod is scheduled and running:

```bash
kc get pods -n inference
```

The pod goes through these stages:
1. **Pending** — waiting for GPU node scheduling
2. **ContainerCreating** — pulling container image
3. **Running (not ready)** — downloading model weights and loading into GPU memory
4. **Running (ready)** — model loaded, serving requests

If the pod is stuck in **Pending**, check node pool availability:
```bash
kc get nodepools
kc describe pod -n inference -l app=inference | grep -A5 Events
```

Watch the logs to monitor model download and loading progress:
```bash
kc logs -n inference -l app=inference -f
```

For a 7B model, expect 3-10 minutes total. Wait until the pod shows `1/1 Ready` before proceeding:
```bash
kc wait --for=condition=ready pod -n inference -l app=inference --timeout=600s
```

**Pass criteria:** Pod is `Running` with `1/1` ready containers.

### 7.2 — Verify the Kubernetes service

Check the Service has an endpoint:

```bash
kc get svc -n inference
kc get endpoints -n inference
```

**Pass criteria:** The service shows at least one endpoint IP (the pod's IP). The service should be `ClusterIP` when using ingress — traffic reaches it through Traefik, not directly.

### 7.3 — Verify TLS certificate

Check that cert-manager has issued a valid certificate:

```bash
kc get certificate -n inference
```

The certificate should show `READY: True`. If it's still provisioning, wait a minute and check again. For deeper debugging:

```bash
kc describe certificate -n inference
kc get certificaterequest -n inference
```

**Pass criteria:** Certificate shows `READY: True`.

### 7.4 — Verify ingress and get the endpoint

```bash
VLLM_ENDPOINT=$(kc get ingress inference -n inference -o=jsonpath='{.spec.rules[0].host}')
echo "Endpoint: https://$VLLM_ENDPOINT"
```

Verify Traefik is routing to the service:
```bash
kc get ingress -n inference
```

**Pass criteria:** Ingress shows the expected hostname and the correct backend service.

### 7.5 — Health check

Test that the vLLM server is responding:

```bash
HTTP_STATUS=$(curl -s -o /dev/null -w "%{http_code}" "https://$VLLM_ENDPOINT/health")
echo "Health check status: $HTTP_STATUS"
```

**Pass criteria:** HTTP status `200`.

If this fails with a connection error, DNS may still be propagating (wait 1-2 minutes). If it returns a TLS error, the certificate may not be ready yet (check 7.3). If it returns `502` or `503`, the pod may not be ready yet (check 7.1).

### 7.6 — Verify model is loaded

Check that vLLM reports the expected model:

```bash
MODELS_RESPONSE=$(curl -s "https://$VLLM_ENDPOINT/v1/models")
echo "$MODELS_RESPONSE" | jq '.data[].id'
```

**Pass criteria:** The response contains the model ID matching what was configured in the values file (e.g., `"mistralai/Mistral-7B-Instruct-v0.3"`).

### 7.7 — Run inference test

Send a chat completion request and verify the model generates a response:

```bash
INFERENCE_RESPONSE=$(curl -s -X POST "https://$VLLM_ENDPOINT/v1/chat/completions" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "<MODEL_NAME>",
    "messages": [
      {"role": "user", "content": "Reply with exactly: Hello from vLLM"}
    ],
    "max_tokens": 50
  }')
echo "$INFERENCE_RESPONSE" | jq '.choices[0].message.content'
```

**Pass criteria:** The response contains a `choices` array with at least one entry, and `choices[0].message.content` is a non-empty string.

Also verify the response structure includes expected fields:

```bash
echo "$INFERENCE_RESPONSE" | jq '{id, model, usage: .usage, finish_reason: .choices[0].finish_reason}'
```

**Pass criteria:** `id` is present, `model` matches the deployed model, `usage` shows `prompt_tokens` and `completion_tokens` > 0, and `finish_reason` is `"stop"` or `"length"`.

### 7.8 — Verify OpenAI API compatibility

Test the completions endpoint (non-chat) to confirm full API compatibility:

```bash
curl -s -X POST "https://$VLLM_ENDPOINT/v1/completions" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "<MODEL_NAME>",
    "prompt": "The capital of France is",
    "max_tokens": 10
  }' | jq '.choices[0].text'
```

**Pass criteria:** Returns a non-empty text completion.

### Summary

Report all test results to the customer in a table:

| Test | Status | Details |
|------|--------|---------|
| 7.1 Pod startup | | Pod running, 1/1 ready |
| 7.2 Service | | Endpoint connected, external IP assigned |
| 7.3 TLS certificate | | Certificate ready |
| 7.4 Ingress | | Hostname resolving |
| 7.5 Health check | | HTTP 200 |
| 7.6 Model loaded | | Correct model ID |
| 7.7 Inference | | Response generated with valid structure |
| 7.8 API compatibility | | Completions endpoint working |

Fill in the Status column with PASS/FAIL for each test. If any test fails, refer to the Troubleshooting section below before reporting failure — many issues are transient (DNS propagation, certificate provisioning, model loading) and resolve with a short wait.

---

## Step 8 — Report success and next steps

Summarize what was deployed:
- Model name and endpoint URL
- How to swap models (change `vllm.model` in values and `helm upgrade`)
- Note that this is an OpenAI-compatible API — any OpenAI client library works by changing the `base_url`

Remind the customer:
- **Scaling up**: Change `vllm.model` and `vllm.resources` in the values file, then `helm upgrade inference ./ --kubeconfig "$KCFG" --kube-context <verified-context-alias> -n inference -f my-values.yaml` (name the cluster here too — `KUBECONFIG` will not have survived from this session)
- **Autoscaling**: Requires installing KEDA and the observability stack. See the reference architecture README for setup.
- **Costs**: Public IPs are billed by the minute. GPU nodes are billed while running regardless of inference load.
- **Cleanup**: `helm uninstall inference --kubeconfig "$KCFG" --kube-context <verified-context-alias> -n inference` removes the deployment but preserves the model cache PVC for reuse.

---

## Step 9 — (Optional) Confirm the GPU is actually working

Step 7 already proved the *inference API* works end-to-end (health,
`/v1/models`, chat + completions). This optional step is different: it
proves the deployment is actually **consuming GPU/cluster resources** — that
the model is loaded on a real, busy GPU, not just that the endpoint returns
200s. Offer it when the customer wants that extra confirmation; skip it if
they are satisfied with the Step 7 results.

Use `inference` as the cluster's target namespace and `-n inference -l
app=inference` as the pod selector. Tip: run an inference request (Step 7.7)
just before checking, so GPU utilization is non-zero when you sample it.

{{include:verify-workload-health}}

---

## Troubleshooting

The `kubectl` calls below are written as `kc` — the bound helper from Step 7.
Open the shell call with that prelude and keep the binding: a diagnosis read
through the wrong cluster's context is worse than no diagnosis.

**Traefik pod stuck in Pending**
This almost always means the cluster has no CPU nodes. Traefik has node affinity rules that prevent it from scheduling on GPU nodes. Check with `kc describe pod -n traefik <pod-name>` — look for "didn't match Pod's node affinity/selector" in Events. The fix is to create a CPU node pool and wait for at least one CPU node to reach Ready status.

**Inference pod stuck in Pending**
The cluster may not have available GPU nodes. Check node pool status: `kc get nodepools`. The instance type in the node pool must have available quota — refer to Console → Administration → Quotas.

**Pod CrashLoopBackOff**
Check logs: `kc logs -n inference <pod-name>`. Common causes:
- Out of GPU memory — model too large for allocated GPUs. Increase `nvidia.com/gpu` count.
- HuggingFace token invalid or missing — model download fails for gated models.
- Model name incorrect — check the exact HuggingFace model ID.

**TLS certificate not provisioning**
Verify cert-manager is running: `kc get pods -n cert-manager`. Check that the `letsencrypt-prod` ClusterIssuer exists: `kc get clusterissuer`. Check certificate status: `kc describe certificate -n inference`.

**HTTPS connections hang / curl times out on the ingress hostname**
The most common cause is `service.type: LoadBalancer` with `service.public: true` while ingress is also enabled. This creates a specific DNS record for the inference service (e.g., `inference.{orgID}-{cluster}.coreweave.app` → the service's public IP) that overrides Traefik's wildcard DNS record. HTTPS traffic then hits the inference pod directly, which only listens on HTTP port 80 — so port 443 connections hang with no response. Fix: set `service.type: ClusterIP` and `service.public: false` in the values file, then `helm upgrade`. After upgrading, the stale DNS record may be cached locally for a few minutes — flush the OS DNS cache or wait for TTL expiry.

**Ingress returns 404 or connection refused**
Verify Traefik is running: `kc get pods -n traefik`. Check that the Traefik service has an external IP: `kc get svc -n traefik`. DNS propagation can take a few minutes after Traefik first gets its IP.

**Model download slow**
First downloads are network-bound. The model cache PVC ensures subsequent starts are fast. For very large models (70B+), consider using CoreWeave's tensorizer for faster loading.

---

## References

- `references/helm-values-reference.md` — Full Helm chart values reference, all variables, and example configurations
- [Reference architecture repo](https://github.com/coreweave/reference-architecture)
- [CoreWeave vLLM tutorial](https://docs.coreweave.com/products/cks/tutorials/deploy-vllm-inference)
- [CoreWeave CKS inference overview](https://docs.coreweave.com/products/inference/cks)
- [vLLM documentation](https://docs.vllm.ai)
