# Helm chart values reference

Full variable reference for the `vllm-basic` Helm chart from the [CoreWeave reference architecture](https://github.com/coreweave/reference-architecture/tree/main/inference/basic).

---

## Table of contents

1. [vLLM container settings](#vllm-container-settings)
2. [Workload type and scaling](#workload-type-and-scaling)
3. [Service and networking](#service-and-networking)
4. [Ingress and TLS](#ingress-and-tls)
5. [Model cache](#model-cache)
6. [HuggingFace token](#huggingface-token)
7. [Monitoring](#monitoring)
8. [Example: minimal single-GPU deployment](#example-minimal-single-gpu-deployment)
9. [Example: Llama 3.1 8B](#example-llama-31-8b)
10. [Example: larger model with multiple GPUs](#example-larger-model-with-multiple-gpus)
11. [GPU sizing guide](#gpu-sizing-guide)

---

## vLLM container settings

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `vllm.image.repository` | string | `ghcr.io/coreweave/ml-containers/vllm-tensorizer` | Container image repository |
| `vllm.image.tag` | string | `8552fbc-v0.10.0` | Image tag |
| `vllm.image.pullPolicy` | string | `IfNotPresent` | Image pull policy |
| `vllm.command` | list | `["vllm"]` | Container command |
| `vllm.model` | string | `mistralai/Mistral-7B-Instruct-v0.3` | HuggingFace model ID |
| `vllm.extraArgs` | list | `[]` | Additional vLLM serve arguments |
| `vllm.port.containerPort` | int | `8000` | vLLM serving port |
| `vllm.resources.limits.memory` | string | `64Gi` | Memory limit |
| `vllm.resources.limits.nvidia.com/gpu` | string | `"1"` | GPU limit |
| `vllm.resources.requests.cpu` | string | `"8"` | CPU request |
| `vllm.resources.requests.memory` | string | `32Gi` | Memory request |
| `vllm.resources.requests.nvidia.com/gpu` | string | `"1"` | GPU request |
| `vllm.tolerations` | list | `[]` | Pod tolerations |
| `vllm.affinity` | object | `{}` | Pod affinity rules |

### Probes

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `vllm.readinessProbe.httpGet.path` | string | `/health` | Readiness probe path |
| `vllm.readinessProbe.initialDelaySeconds` | int | `10` | Initial delay before probing |
| `vllm.readinessProbe.periodSeconds` | int | `5` | Probe interval |
| `vllm.livenessProbe.httpGet.path` | string | `/health` | Liveness probe path |
| `vllm.livenessProbe.periodSeconds` | int | `10` | Probe interval |
| `vllm.livenessProbe.failureThreshold` | int | `30` | Failures before restart (30 x 10s = 5 min startup) |

For larger models that take longer to load, increase `failureThreshold`. The Llama-small example uses `3600` (10 hours) to handle slow downloads.

---

## Workload type and scaling

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `vllm.workload.type` | string | `deployment` | `"deployment"` or `"leaderWorkerSet"` |
| `vllm.workload.deployment.replicaCount` | int | `1` | Number of replicas |
| `vllm.workload.deployment.autoScale.enabled` | bool | `true` | Enable KEDA autoscaling |
| `vllm.workload.deployment.autoScale.minReplicas` | int | `1` | Minimum replicas |
| `vllm.workload.deployment.autoScale.maxReplicas` | int | `10` | Maximum replicas |
| `vllm.workload.deployment.autoScale.pollingInterval` | int | `15` | KEDA polling interval (seconds) |
| `vllm.workload.deployment.autoScale.cooldownPeriod` | int | `60` | Cooldown after scale event (seconds) |
| `vllm.workload.deployment.autoScale.cacheUtilizationThreshold` | int | `40` | GPU cache % threshold to trigger scale-up |

Autoscaling requires KEDA and Prometheus to be installed in the cluster. For initial deployments, set `autoScale.enabled: false` to skip these dependencies.

### LeaderWorkerSet (multi-node inference)

For models too large for a single node, use LeaderWorkerSet with Ray:

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `vllm.workload.leaderWorkerSet.groups` | int | `1` | Number of LWS groups |
| `vllm.workload.leaderWorkerSet.groupSize` | int | `2` | Nodes per group (leader + workers) |
| `vllm.workload.leaderWorkerSet.restartPolicy` | string | `RecreateGroupOnPodRestart` | Restart policy |

Requires the LeaderWorkerSet CRD to be installed in the cluster.

---

## Service and networking

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `service.type` | string | `LoadBalancer` | Kubernetes service type |
| `service.public` | bool | `false` | Add CoreWeave public LB annotation |
| `service.hostnameOverride` | string | `""` | Override the external hostname |
| `service.port.port` | int | `80` | Service port |
| `service.port.protocol` | string | `TCP` | Protocol |

When `service.public` is `true`, these annotations are added:
- `service.beta.kubernetes.io/coreweave-load-balancer-type: public` — assigns a public IPv4
- `service.beta.kubernetes.io/external-hostname: <name>` — creates DNS under `.coreweave.app`

Public IPs are billed by the minute.

> **WARNING: Do not use `service.type: LoadBalancer` with `service.public: true` when `ingress.enabled: true`.** The public LoadBalancer creates a specific DNS record (e.g., `inference.{orgID}-{cluster}.coreweave.app`) that overrides Traefik's wildcard DNS. This causes HTTPS traffic to bypass Traefik and hit the inference pod directly, which has no TLS listener on port 443 — resulting in hanging connections. When using ingress for TLS, set `service.type: ClusterIP` and `service.public: false`.

---

## Ingress and TLS

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `ingress.enabled` | bool | `true` | Create an Ingress resource |
| `ingress.clusterName` | string | `""` | CKS cluster name (required) |
| `ingress.orgID` | string | `""` | CoreWeave org ID (required) |

The ingress creates a TLS-terminated endpoint at:
```
https://{release-name}.{orgID}-{clusterName}.coreweave.app
```

Uses `ingressClassName: traefik` and `cert-manager.io/cluster-issuer: letsencrypt-prod` for automatic Let's Encrypt certificates via DNS01 validation.

Prerequisites: Traefik and cert-manager must be installed in the cluster (see SKILL.md Step 3).

---

## Model cache

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `modelCache.enabled` | bool | `true` | Mount a model cache volume |
| `modelCache.create` | bool | `true` | Create the PVC (set false if PVC already exists) |
| `modelCache.name` | string | `huggingface-model-cache` | PVC name |
| `modelCache.size` | string | `10Ti` | PVC size (only used when creating) |
| `modelCache.mountPath` | string | `/root/.cache/huggingface` | Mount path in container |

The PVC uses `storageClassName: shared-vast` (CoreWeave's distributed filesystem). Creating the PVC outside the Helm chart is recommended so it persists across `helm uninstall` and can be shared by multiple deployments.

---

## HuggingFace token

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `hfToken.secretName` | string | `""` | Existing K8s secret name containing the token |
| `hfToken.token` | string | `""` | Token value (not recommended — use secretName instead) |

The secret must have a key named `token`. Create it with:
```bash
KCFG=<path-to-the-kubeconfig-for-your-cluster>
kubectl --kubeconfig "$KCFG" --context <your-cluster-name> \
  create secret generic hf-token -n inference --from-literal=token="<TOKEN>"
```

---

## Monitoring

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `prometheus.enabled` | bool | `true` | Create a ServiceMonitor for Prometheus scraping |
| `prometheus.serverURL` | string | `http://prometheus-operated.monitoring:9090` | Prometheus server URL (used by KEDA) |
| `pdb.enabled` | bool | `true` | Create a PodDisruptionBudget |
| `pdb.minAvailable` | int | `1` | Minimum available pods |

---

## Example: minimal single-GPU deployment

For a quick test with Mistral 7B:

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
  clusterName: "my-cluster"
  orgID: "cw0000"

prometheus:
  enabled: false
```

---

## Example: Llama 3.1 8B

```yaml
hfToken:
  secretName: "hf-token"

vllm:
  model: "meta-llama/Llama-3.1-8B-Instruct"
  resources:
    limits:
      memory: "200Gi"
      nvidia.com/gpu: "1"
    requests:
      cpu: "10"
      memory: "200Gi"
      nvidia.com/gpu: "1"
  readinessProbe:
    httpGet:
      path: /health
    initialDelaySeconds: 10
    periodSeconds: 5
  livenessProbe:
    httpGet:
      path: /health
    periodSeconds: 10
    failureThreshold: 3600
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
  clusterName: "my-cluster"
  orgID: "cw0000"
```

---

## Example: larger model with multiple GPUs

For a 70B model requiring 4 GPUs:

```yaml
hfToken:
  secretName: "hf-token"

vllm:
  model: "meta-llama/Llama-3.1-70B-Instruct"
  extraArgs:
    - "--tensor-parallel-size=4"
  resources:
    limits:
      memory: "400Gi"
      nvidia.com/gpu: "4"
    requests:
      cpu: "16"
      memory: "400Gi"
      nvidia.com/gpu: "4"
  livenessProbe:
    httpGet:
      path: /health
    periodSeconds: 10
    failureThreshold: 3600
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
  clusterName: "my-cluster"
  orgID: "cw0000"
```

For tensor parallelism, add `--tensor-parallel-size=N` matching the GPU count.

---

## GPU sizing guide

Rough guidelines for GPU memory requirements:

| Model size | Min GPU memory | Typical GPU count | Example instance types |
|------------|---------------|-------------------|----------------------|
| 125M–1B | 16 GB | 1 | Any GPU node |
| 7-8B | 16-24 GB | 1 | `rtxp6000-8x`, `gd-8xh100ib-i128` |
| 13B | 32 GB | 1 | H100 (80GB) |
| 34B | 80 GB | 1-2 | H100 |
| 70B | 160 GB+ | 2-4 | H100 with tensor parallelism |
| 405B+ | 640 GB+ | 8+ | Multi-node with LeaderWorkerSet |

These are approximate — actual requirements depend on quantization, context length, and batch size. When in doubt, start with more GPUs and scale down.

---

## Links

- [Reference architecture repo](https://github.com/coreweave/reference-architecture)
- [CoreWeave vLLM tutorial](https://docs.coreweave.com/products/cks/tutorials/deploy-vllm-inference)
- [vLLM documentation](https://docs.vllm.ai)
- [KEDA autoscaling](https://keda.sh/docs/latest/)
