<!--
  EXAMPLE SNIPPET FILE — not production content.

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
## Create a CoreWeave API token

1. Sign in to the CoreWeave Cloud Console at <https://console.coreweave.com>.
2. Open **Access → API tokens** and click **Create token**.
3. Name the token `{{ TOKEN_NAME }}` and scope it to the
   `{{ TOKEN_SCOPE }}` role.
4. Copy the token value once — it is not retrievable later. Store it in
   `{{ SECRET_STORE_HINT }}` and export it as `CW_API_TOKEN` in your shell.

> If you do not see the **API tokens** tab, your org admin has not
> granted you the **IAM Admin** role. Ask them to run the user-add
> workflow before continuing.
<!-- /snippet:create-api-token -->

<!-- snippet:generate-kubeconfig -->
## Generate a kubeconfig for cluster `{{ CLUSTER_NAME }}`

1. With `CW_API_TOKEN` exported, run:

   ```bash
   coreweave kubeconfig get \
     --cluster {{ CLUSTER_NAME }} \
     --output ~/.kube/coreweave-{{ CLUSTER_NAME }}.yaml
   ```

2. Merge it into your active kubeconfig:

   ```bash
   export KUBECONFIG=$HOME/.kube/config:$HOME/.kube/coreweave-{{ CLUSTER_NAME }}.yaml
   kubectl config use-context coreweave-{{ CLUSTER_NAME }}
   ```

3. Verify connectivity:

   ```bash
   kubectl get nodes
   ```

   You should see at least one node in `Ready` state. If not, jump to
   the troubleshooting include and re-check the token scope from
   `create-api-token`.
<!-- /snippet:generate-kubeconfig -->
