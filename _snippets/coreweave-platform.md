<!--
  This file holds atomic procedures shared across CoreWeave platform
  workflows. Each procedure is bracketed by HTML-comment markers:

      <!-- snippet:NAME -->
      ...body...
      <!-- /snippet:NAME -->

  The build resolves `{{include:NAME}}` markers in skill bodies by
  copying everything between the matching open/close tags, substituting
  `{{ PARAM_NAME }}` placeholders with values declared in the skill's
  skill.yaml.

  When to extract a region into this file:
    - Three or more workflow skills will inline the same procedure, OR
    - The procedure is canonical enough that drift between copies would
      be a correctness bug (e.g., the official kubeconfig generation).

  Conventions used here (a maintainer can change these — they are not
  enforced by the build):
    - Snippet names are kebab-case verbs ("create-api-token"), not nouns.
    - Parameter placeholders use Jinja2 double-brace syntax with spaces:
      `{{ TOKEN_NAME }}`. The build runs values through Jinja2.
    - Each region opens with a one-line `## Heading` so the inlined
      result reads naturally as a sub-section of the workflow.
-->

<!-- snippet:create-api-token -->
## Create a CoreWeave API access token

CoreWeave API access tokens are user-scoped and gate the ability to deploy
CKS clusters and VPCs, access cluster metrics, and authenticate `kubectl`
against the managed-auth endpoint.

This workflow requires an authenticated web browser. If the customer has not
approved browser access, walk them through the Console steps below. If they
have approved browser access, attempt the steps yourself and pause for
authentication or one-time credential handling when needed.

1. Sign in to the CoreWeave Cloud Console at <https://console.coreweave.com>.
2. Go to the **Tokens** page (<https://console.coreweave.com/tokens>) and
   click **Create Token** in the upper-right corner.
3. In the **Create API Token** dialog, set:
   - **Name** — `{{ TOKEN_NAME }}`
   - **Expiration** — how long the token stays valid
   - **Note** — an optional description for future reference
4. Click **Create**.
5. Choose how to receive the credential:
   - **Token Secret** — the raw token secret (starts with `CW-SECRET-`),
     for scraping metrics/logs, self-hosted Grafana, or adding to an
     existing kubeconfig. This is what you want for API/`curl` use.
   - **Kubeconfig** — a ready-to-use kubeconfig for a specific cluster,
     with the token already embedded (see `generate-kubeconfig`).
6. Copy the value **once** — token secrets and kubeconfig files are shown
   in the Console modal a single time and never again. Store it in
   `{{ SECRET_STORE_HINT }}` and export it as `CW_API_TOKEN` in your shell.

> The token inherits the permissions of your user. If an action later
> fails with `401`/`403`, your user is missing the relevant IAM role for
> that operation (for example, **Observability Viewer** for metrics). Ask
> your org admin to grant it — see the user-add workflow.

> For full details, see
> [Manage API access tokens](https://docs.coreweave.com/security/authn-authz/manage-api-access-tokens).
<!-- /snippet:create-api-token -->

<!-- snippet:generate-kubeconfig -->
## Get a kubeconfig for cluster `{{ CLUSTER_NAME }}`

> **There is no `coreweave` CLI command that fetches a kubeconfig, and no
> Terraform data source or output for it.** CKS uses Managed Auth. What the
> Console's **Download kubeconfig** button produces is a plain kubeconfig
> with the customer's API access token embedded as a **static bearer
> token** — there is no exec plugin and no Console-only credential in it.
>
> So there are two paths, and **path A does not need the Console at all**:
> if the customer already has an API access token and you know the
> cluster's API server endpoint, you can write the same file yourself.
> Reach for the Console download (path B) when the customer has no token
> yet, or when you cannot determine the API server endpoint.

### A. Build it from an API access token (no Console, works headless)

Use this whenever the customer's token is already available (for example
exported in the environment) — which is the common case when a skill has
just created the cluster.

You need two values:

- **The API server endpoint.** After `cw-create-cluster`'s Phase 1 apply it
  is the `cks_api_server_endpoint` Terraform output. Otherwise read it from
  the cluster's Console page or the CoreWeave API.
- **The API access token**, from the environment. Never echo it, and never
  paste it into a heredoc that gets logged — write the file with the shell
  expanding the variable, as below.

```bash
CLUSTER={{ CLUSTER_NAME }}
API_SERVER=<cks_api_server_endpoint>        # e.g. abc123-9c8f070b.k8s.us-east-04a.coreweave.com
KCFG="$HOME/.kube/$CLUSTER-kubeconfig.yaml"

mkdir -p "$(dirname "$KCFG")"
umask 077
cat > "$KCFG" <<EOF
apiVersion: v1
kind: Config
preferences: {}
clusters:
- cluster:
    server: https://$API_SERVER
  name: $CLUSTER
contexts:
- context:
    cluster: $CLUSTER
    user: token
  name: $CLUSTER
current-context: $CLUSTER
users:
- name: token
  user:
    token: $CW_API_ACCESS_TOKEN
EOF
chmod 600 "$KCFG"
export KUBECONFIG="$KCFG"
kubectl config current-context      # must print $CLUSTER exactly
```

This check is fail-closed: if the last line prints anything other than the
cluster name, or errors, stop — run no cluster-touching command (kubectl
reads or applies, helm, Terraform) until it passes. This file was just
written with exactly one context, so any other output means the write above
failed or a different kubeconfig is active — do not `use-context` your way
past it. Re-run the whole block above in a single shell call (it re-sets
`$CLUSTER`, `$KCFG`, and `KUBECONFIG`, none of which persist between agent
shell calls) and proceed only after the re-check matches exactly.

> **Do not add `insecure-skip-tls-verify: true`.** The CKS API server
> presents a valid publicly-trusted certificate, so this kubeconfig
> verifies TLS normally. If `kubectl` reports a certificate error, the
> cause is the endpoint value or a local trust-store problem — fix that,
> rather than disabling verification against a production API server.

Then skip to **Verify connectivity** below (the multi-context selection
step does not apply — this file has exactly one context).

### B. Download it from the Console

Use this when the customer has no API access token yet, or the API server
endpoint is not determinable. An agent cannot click the download button —
pause and have the customer do it. Choose either path in the Console:

**B1. From the Tokens page (creates the token and kubeconfig together):**

1. Go to the **Tokens** page (<https://console.coreweave.com/tokens>) and
   click **Create Token**.
2. Fill in the token details, then in the download step choose
   **Kubeconfig** and set the context to cluster `{{ CLUSTER_NAME }}`.
3. Click **Download** and save the file. It is shown only once.

**B2. From the Clusters page (for a cluster that already exists):**

1. Go to the **Clusters** page (<https://console.coreweave.com/clusters>).
2. Find `{{ CLUSTER_NAME }}`, click the vertical ellipsis
   (**More options**), and click **Download kubeconfig**.
3. Save the file locally.

Then point `kubectl` at it. Ask the customer for the path where they saved
the file. A CoreWeave kubeconfig can carry contexts for **multiple
clusters**, so select the one for `{{ CLUSTER_NAME }}` before doing anything
else, or you may act on the wrong cluster:

```bash
# Run these together in ONE shell call — KUBECONFIG does not persist between
# agent shell calls, and without it use-context silently edits ~/.kube/config.
export KUBECONFIG=/path/to/downloaded/{{ CLUSTER_NAME }}-kubeconfig.yaml
kubectl config get-contexts
kubectl config use-context {{ CLUSTER_NAME }}
kubectl config current-context      # must print {{ CLUSTER_NAME }} exactly
```

This check is fail-closed: if the last line prints anything other than
`{{ CLUSTER_NAME }}`, or cannot be read at all, stop — run no cluster-touching
command (kubectl reads or applies, helm, Terraform) until it passes.
`kubectl config` context commands are the remediation, not the risk: re-run
the block above in a single shell call and proceed only after the re-check
matches exactly.

### Verify connectivity

```bash
kubectl get nodes
```

You should see at least one node in `Ready` state (a freshly created
cluster with no node pools yet may show none — that is expected until a
node pool is added). If the API call is rejected, re-check that the token
embedded in the kubeconfig still has access to the cluster (see
`create-api-token`).

> Private clusters have no public API endpoint, so the Console kubeconfig
> download may not be available for them — those use a private access path
> configured with CoreWeave Support. See
> [Managed Auth kubeconfig](https://docs.coreweave.com/products/cks/auth-access/managed-auth/kubeconfig).
<!-- /snippet:generate-kubeconfig -->
