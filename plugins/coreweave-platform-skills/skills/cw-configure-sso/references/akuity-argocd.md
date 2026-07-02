# Akuity / Argo CD authentication (managed by CoreWeave — not self-serve)

The onboarding CUJ mentions "Akuity authentication on a per-customer basis." This reference explains what that is and, critically, **why the customer cannot self-configure it in the Cloud Console** — so you route them correctly instead of trying to apply the SAML SSO workflow.

> **Honesty note.** The details below come from CoreWeave's internal Solutions Architecture (SA) knowledge base, not from the public docs site. Treat them as background for *routing the customer*, not as a customer-facing procedure. Do not walk a customer through configuring Akuity auth yourself — confirm specifics with their CoreWeave contact.

## What it is

Some CoreWeave customers run a **co-managed GitOps** setup: a managed **Argo CD** instance hosted on **Akuity** (a commercial Argo CD SaaS), reachable at a host like `https://[ORG-ID].cd.akuity.cloud`. CoreWeave's **Solutions Architecture team provisions and manages** these instances — they are part of an SA offering, not a self-service Console feature.

## How its authentication works (background)

- Login to the customer's Akuity/Argo CD instance is brokered by **CoreWeave's own SSO provider (Authentik)** acting as an identity broker.
- The customer authenticates via **OIDC**, typically federated from the customer's own IdP (Azure Entra/AD, Okta, Google Workspace / gsuite, or generic OIDC).
- Setup involves the customer creating an OAuth application in their IdP with a CoreWeave-provided callback/redirect URL, and CoreWeave SA wiring the Authentik OIDC provider for that instance. Argo CD login is then done with the Argo CD CLI's `--sso` flow (`argocd login [ORG-ID].cd.akuity.cloud --sso --grpc-web`).

None of this is exposed on the CoreWeave Cloud Console SSO/SCIM pages. It is a distinct system from the SAML SSO that governs Console/CKS login.

## What to tell the customer

If the customer is asking about logging into their Argo CD / Akuity instance, or "Akuity authentication":

1. Clarify that this is **separate from Cloud Console SSO**. Configuring SAML SSO (the main `SKILL.md`) will not set up Argo CD login, and vice versa.
2. Explain that the Akuity/Argo CD instance and its authentication are **provisioned and managed by CoreWeave's Solutions Architecture team**, not self-configured in the Console.
3. **Route them to their CoreWeave contact** (their SA / account team, or CoreWeave Support) to set up or change Argo CD authentication. The customer's part is usually creating an OAuth app in their IdP and providing the client details to CoreWeave; CoreWeave completes the brokering.
4. If they want to *use* an already-provisioned instance, the login is via the Argo CD CLI SSO flow against their `*.cd.akuity.cloud` host.

<!-- TODO(SME): This routing is based on internal SA runbooks (Authentik OIDC broker, [ORG-ID].cd.akuity.cloud, OAuth-app-in-customer-IdP pattern). There is no public CoreWeave docs page for customer-facing Akuity/Argo CD auth setup as of authoring. Before this skill gives a customer any concrete Akuity setup steps, confirm with the SA team what (if anything) is customer-self-serve and whether a public runbook exists. Until then, keep the guidance to "this is SA-managed; contact your CoreWeave team." -->

## Do not

- Do not attempt to configure Akuity/Argo CD auth through the Cloud Console SSO or SCIM pages — those govern a different system.
- Do not invent Akuity Console steps or Authentik configuration for the customer.
- Do not conflate this with Cloud Console SAML SSO in your explanation; keep them clearly separate.
