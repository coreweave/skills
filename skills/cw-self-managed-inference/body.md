
# Deploy Self-Managed vLLM Inference on CKS

You are helping a CoreWeave customer deploy a vLLM inference service on their CKS cluster using the [CoreWeave reference architecture](https://github.com/coreweave/reference-architecture) Helm chart. The deployment includes TLS-terminated ingress via Traefik and a public endpoint.

---

## Before you start

Confirm these prerequisites:

- The customer has a **running CKS cluster** with at least one **GPU node pool** AND at least one **CPU node pool**. If not, offer to guide them through cluster creation using the `cw-create-cluster` skill. The CPU node pool is required because Traefik (the ingress controller) needs a CPU node to run on — it cannot be scheduled on GPU-only nodes due to node affinity rules. Without a CPU node, Traefik will be stuck in Pending and the entire ingress/TLS stack will be non-functional.
- The customer has downloaded a kubeconfig file for their CKS cluster and can run `kubectl` commands against it. If not, guide them through downloading this.
- **kubectl** is installed and configured with the cluster's kubeconfig.
- **The correct kubectl context is active.** CoreWeave kubeconfig files often contain contexts for multiple clusters. Always verify the active context matches the target cluster before running any commands:
  ```bash
  kubectl config get-contexts
  kubectl config use-context <TARGET_CLUSTER_NAME>
  kubectl config current-context
  ```
  All subsequent kubectl and helm commands will target whichever context is active. Getting this wrong means deploying to the wrong cluster.
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

---

## Step 2 — Clone the reference architecture and copy the Helm chart

```bash
git clone https://github.com/coreweave/reference-architecture.git /tmp/claude/cw-ref-arch
```

If the repo is already cloned (from a previous session), pull the latest instead.

After cloning, ask the customer if they'd like to copy the Helm chart to a local directory for safekeeping (e.g., their home directory or a project folder). This way they have a standalone copy that won't be lost if `/tmp` is cleaned up or the upstream repo changes.

```bash
cp -r /tmp/claude/cw-ref-arch/inference/basic <DESTINATION>
```

If the customer provides a destination, copy the chart there and use that path for the rest of the deployment. Otherwise, default to working from `/tmp/claude/cw-ref-arch/inference/basic`.

---

## Step 3 — Install cluster dependencies

The Helm chart requires cert-manager (for TLS certificates) and Traefik (for ingress). Install them from CoreWeave's Helm repo.

### Verify a CPU node pool exists

Before installing Traefik, confirm the cluster has a CPU node pool with at least one ready node. Traefik requires a CPU node — it will not schedule on GPU-only nodes.

```bash
kubectl get nodepools
kubectl get nodes
```

Look for a node pool with a CPU instance type (e.g., `cd-hp-a96-genoa`, `cpu-4`). If no CPU node pool exists, the customer must create one before proceeding. Guide them through this using the `cw-create-cluster` skill or by applying a NodePool manifest directly:

```bash
kubectl apply -f - <<'EOF'
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
kubectl wait --for=condition=ready node -l node.coreweave.com/instance-type=<CPU_INSTANCE_TYPE> --timeout=300s
```

> **Do not proceed until a CPU node is ready.** Without one, Traefik will be stuck in Pending and the entire ingress/TLS stack will be non-functional.

### Add the CoreWeave Helm repo

```bash
helm repo add coreweave https://charts.core-services.ingress.coreweave.com
helm repo update
```

### Install cert-manager

cert-manager handles automatic TLS certificate provisioning via Let's Encrypt.

```bash
helm install cert-manager coreweave/cert-manager \
  --namespace cert-manager --create-namespace
```

After cert-manager is running, enable the cert-issuers subchart which creates the `letsencrypt-prod` ClusterIssuer:

```bash
helm upgrade cert-manager coreweave/cert-manager \
  --namespace cert-manager \
  --set cert-issuers.enabled=true
```

Verify the ClusterIssuer exists:

```bash
kubectl get clusterissuer letsencrypt-prod
```

### Install Traefik

Traefik serves as the ingress controller and automatically gets a wildcard DNS entry under `*.{orgID}-{clusterName}.coreweave.app`.

```bash
helm install traefik coreweave/traefik \
  --namespace traefik --create-namespace
```

Wait for Traefik to get an external IP:

```bash
kubectl get svc -n traefik -w
```

### Collect cluster identity

The ingress hostname follows the pattern `{release-name}.{orgID}-{clusterName}.coreweave.app`. The customer needs to provide:

- **Org ID** — visible in the Console URL or account settings (e.g., `cw0000`, `cwb607`)
- **Cluster name** — the CKS cluster name (e.g., `use04a-dev`)

---

## Step 4 — Set up the inference namespace, token, and model cache

### Create the inference namespace

```bash
kubectl create namespace inference
```

### Create the HF token secret (gated models only)

Skip this step if the customer chose an open model (see Step 1).

If the model is gated, ask the customer for their HuggingFace token. Create it as a Kubernetes secret — never write it to values files:

```bash
kubectl create secret generic hf-token -n inference \
  --from-literal=token="<HF_TOKEN>"
```

### Create the model cache PVC

The model cache persists downloaded model weights across pod restarts. This uses CoreWeave's `shared-vast` distributed filesystem:

```bash
kubectl apply -n inference -f - <<'EOF'
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
```

Key points:
- Only include `hfToken.secretName` if the model is gated and the secret was created in Step 4
- **When ingress is enabled, the service MUST be `ClusterIP` with `public: false`.** Using `LoadBalancer` with `public: true` creates a direct DNS record (e.g., `inference.{orgID}-{cluster}.coreweave.app`) that overrides Traefik's wildcard DNS. This causes HTTPS requests to hit the inference pod directly on port 443, which has no TLS listener — resulting in hanging connections. Traefik handles TLS termination, so the service only needs to be reachable within the cluster.
- `modelCache.create: false` because we created the PVC separately (persists across helm reinstalls)
- `autoScale.enabled: false` for initial setup (no monitoring stack required). Can enable later with KEDA + Prometheus.
- Adjust `resources` based on the model — larger models need more memory and GPUs

> **Checkpoint:** Show the customer the generated values file and get confirmation before deploying.

Write the values file to `my-values.yaml` in the Helm chart directory (either the customer's chosen copy location from Step 2 or `/tmp/claude/cw-ref-arch/inference/basic`).

---

## Step 6 — Deploy with Helm

From the Helm chart directory:

```bash
helm install inference ./ \
  --namespace inference \
  --create-namespace \
  -f my-values.yaml
```

The deployment will start pulling the model. The first download can take several minutes depending on model size. The pod will not be ready until the model is fully loaded.

---

## Step 7 — Wait for readiness and verify the deployment

This step validates the entire stack: pod scheduling, model loading, networking, TLS, and inference. Run each verification in order — later checks depend on earlier ones passing.

### 7.1 — Verify pod startup

Check that the pod is scheduled and running:

```bash
kubectl get pods -n inference
```

The pod goes through these stages:
1. **Pending** — waiting for GPU node scheduling
2. **ContainerCreating** — pulling container image
3. **Running (not ready)** — downloading model weights and loading into GPU memory
4. **Running (ready)** — model loaded, serving requests

If the pod is stuck in **Pending**, check node pool availability:
```bash
kubectl get nodepools
kubectl describe pod -n inference -l app=inference | grep -A5 Events
```

Watch the logs to monitor model download and loading progress:
```bash
kubectl logs -n inference -l app=inference -f
```

For a 7B model, expect 3-10 minutes total. Wait until the pod shows `1/1 Ready` before proceeding:
```bash
kubectl wait --for=condition=ready pod -n inference -l app=inference --timeout=600s
```

**Pass criteria:** Pod is `Running` with `1/1` ready containers.

### 7.2 — Verify the Kubernetes service

Check the Service has an endpoint:

```bash
kubectl get svc -n inference
kubectl get endpoints -n inference
```

**Pass criteria:** The service shows at least one endpoint IP (the pod's IP). The service should be `ClusterIP` when using ingress — traffic reaches it through Traefik, not directly.

### 7.3 — Verify TLS certificate

Check that cert-manager has issued a valid certificate:

```bash
kubectl get certificate -n inference
```

The certificate should show `READY: True`. If it's still provisioning, wait a minute and check again. For deeper debugging:

```bash
kubectl describe certificate -n inference
kubectl get certificaterequest -n inference
```

**Pass criteria:** Certificate shows `READY: True`.

### 7.4 — Verify ingress and get the endpoint

```bash
VLLM_ENDPOINT=$(kubectl get ingress inference -n inference -o=jsonpath='{.spec.rules[0].host}')
echo "Endpoint: https://$VLLM_ENDPOINT"
```

Verify Traefik is routing to the service:
```bash
kubectl get ingress -n inference
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
- **Scaling up**: Change `vllm.model` and `vllm.resources` in the values file, then `helm upgrade inference ./ -n inference -f my-values.yaml`
- **Autoscaling**: Requires installing KEDA and the observability stack. See the reference architecture README for setup.
- **Costs**: Public IPs are billed by the minute. GPU nodes are billed while running regardless of inference load.
- **Cleanup**: `helm uninstall inference -n inference` removes the deployment but preserves the model cache PVC for reuse.

---

## Troubleshooting

**Traefik pod stuck in Pending**
This almost always means the cluster has no CPU nodes. Traefik has node affinity rules that prevent it from scheduling on GPU nodes. Check with `kubectl describe pod -n traefik <pod-name>` — look for "didn't match Pod's node affinity/selector" in Events. The fix is to create a CPU node pool and wait for at least one CPU node to reach Ready status.

**Inference pod stuck in Pending**
The cluster may not have available GPU nodes. Check node pool status: `kubectl get nodepools`. The instance type in the node pool must have available quota — refer to Console → Administration → Quotas.

**Pod CrashLoopBackOff**
Check logs: `kubectl logs -n inference <pod-name>`. Common causes:
- Out of GPU memory — model too large for allocated GPUs. Increase `nvidia.com/gpu` count.
- HuggingFace token invalid or missing — model download fails for gated models.
- Model name incorrect — check the exact HuggingFace model ID.

**TLS certificate not provisioning**
Verify cert-manager is running: `kubectl get pods -n cert-manager`. Check that the `letsencrypt-prod` ClusterIssuer exists: `kubectl get clusterissuer`. Check certificate status: `kubectl describe certificate -n inference`.

**HTTPS connections hang / curl times out on the ingress hostname**
The most common cause is `service.type: LoadBalancer` with `service.public: true` while ingress is also enabled. This creates a specific DNS record for the inference service (e.g., `inference.{orgID}-{cluster}.coreweave.app` → the service's public IP) that overrides Traefik's wildcard DNS record. HTTPS traffic then hits the inference pod directly, which only listens on HTTP port 80 — so port 443 connections hang with no response. Fix: set `service.type: ClusterIP` and `service.public: false` in the values file, then `helm upgrade`. After upgrading, the stale DNS record may be cached locally for a few minutes — flush the OS DNS cache or wait for TTL expiry.

**Ingress returns 404 or connection refused**
Verify Traefik is running: `kubectl get pods -n traefik`. Check that the Traefik service has an external IP: `kubectl get svc -n traefik`. DNS propagation can take a few minutes after Traefik first gets its IP.

**Model download slow**
First downloads are network-bound. The model cache PVC ensures subsequent starts are fast. For very large models (70B+), consider using CoreWeave's tensorizer for faster loading.

---

## References

- `references/helm-values-reference.md` — Full Helm chart values reference, all variables, and example configurations
- [Reference architecture repo](https://github.com/coreweave/reference-architecture)
- [CoreWeave vLLM tutorial](https://docs.coreweave.com/products/cks/tutorials/deploy-vllm-inference)
- [CoreWeave CKS inference overview](https://docs.coreweave.com/products/inference/cks)
- [vLLM documentation](https://docs.vllm.ai)
