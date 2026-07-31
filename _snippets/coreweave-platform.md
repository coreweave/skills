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
> Terraform data source or output for it.** CKS uses Managed Auth: the
> kubeconfig is **generated in the Cloud Console** with the API access
> token already embedded, and downloaded manually. An autonomous agent
> cannot perform the download — pause and have the customer do it.

Choose either path in the Console:

**A. From the Tokens page (creates the token and kubeconfig together):**

1. Go to the **Tokens** page (<https://console.coreweave.com/tokens>) and
   click **Create Token**.
2. Fill in the token details, then in the download step choose
   **Kubeconfig** and set the context to cluster `{{ CLUSTER_NAME }}`.
3. Click **Download** and save the file. It is shown only once.

**B. From the Clusters page (for a cluster that already exists):**

1. Go to the **Clusters** page (<https://console.coreweave.com/clusters>).
2. Find `{{ CLUSTER_NAME }}`, click the vertical ellipsis
   (**More options**), and click **Download kubeconfig**.
3. Save the file locally.

Then point `kubectl` at it. Ask the customer for the path where they saved
the file:

```bash
export KUBECONFIG=/path/to/downloaded/{{ CLUSTER_NAME }}-kubeconfig.yaml
```

A CoreWeave kubeconfig can carry contexts for **multiple clusters**. Select
the one for `{{ CLUSTER_NAME }}` before doing anything else, or you may act
on the wrong cluster:

```bash
kubectl config get-contexts
kubectl config use-context {{ CLUSTER_NAME }}
kubectl config current-context      # confirm it matches {{ CLUSTER_NAME }}
```

Verify connectivity:

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
