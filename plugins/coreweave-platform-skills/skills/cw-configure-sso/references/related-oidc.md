# Related but out of scope: OIDC federation and cluster auth

Customers often say "OIDC" or "SSO" when they mean one of several *different* things. User login to the Cloud Console is **SAML SSO** (the main `SKILL.md`). This reference disambiguates the OIDC-flavored features that are **not** user SSO, so you can recognize them and route the customer to the right place instead of misapplying the SAML workflow.

## Quick disambiguation

Ask the customer what is authenticating:

- **A person, logging into the Cloud Console or CKS in a browser** → that's **SAML SSO**. Use the main `SKILL.md`. Stop reading here.
- **A workload / service account reaching another service** (e.g., a CKS Pod calling AI Object Storage, or authenticating to AWS/GCP without static keys) → **OIDC Workload Identity Federation**. Out of scope; see below.
- **`kubectl` authenticating to a specific cluster's API server via an external IdP** → **cluster-level OIDC / auth webhooks** (a.k.a. "unmanaged auth"). Out of scope; see below.
- **Logging into a co-managed Argo CD / Akuity instance** → CoreWeave-managed, not self-serve; see `references/akuity-argocd.md`.

---

## OIDC Workload Identity Federation (WIF)

**What it is:** Turns a CKS cluster into a trusted OIDC identity provider so *workloads* use short-lived Kubernetes-issued tokens instead of long-lived static secrets. It also lets you register an *external* IdP (Okta, Google, etc.) so machine workloads can federate into CoreWeave AI Object Storage. This is machine-to-machine auth, not human login.

**Where it lives in the Console:** the **Workload Federation** pages — `console.coreweave.com/organization/iam/workload-federation/oidc` (and `.../saml` for the SAML variant). Note this is a *different* IAM sub-page from the SAML SSO page used for user login.

**Why it's not this skill:** it grants no Console login, no user identity, no interactive session. Configuring it for a customer who actually wanted team login would be wrong.

**Where to send them:**
- OIDC workload identity for CKS: https://docs.coreweave.com/products/cks/auth-access/workload-identity/introduction
- Workload Identity Federation for Object Storage (OIDC & SAML): https://docs.coreweave.com/products/storage/object-storage/auth-access/workload-identity-federation/about
- CKS → Object Storage tutorial (automatic, via Pod Identity Webhook): https://docs.coreweave.com/security/tutorials/cks-object-storage-authentication/automatic

---

## Cluster-level OIDC / auth webhooks ("unmanaged auth")

**What it is:** When creating a CKS cluster you can optionally point the cluster's Kubernetes API server at your own OIDC provider so `kubectl` users authenticate through it (with `kubelogin`), mapping OIDC group claims to Kubernetes RBAC. This is per-cluster API-server auth, separate from Cloud Console login.

**Where it shows up:** the **`cw-create-cluster`** skill lists an optional "OIDC / Auth webhooks" input at cluster creation, and its Terraform reference exposes an `oidc` object (`issuer_url`, `client_id`, `ca`, `admin_group_binding`, `groups_claim`, `groups_prefix`, `required_claim`, `signing_algs`, `username_claim`, `username_prefix`). Do **not** re-document that here — if the customer wants cluster-level OIDC at creation time, route them to `cw-create-cluster`.

**Why it's not this skill:** it authenticates `kubectl` to one cluster's API, not people to the Console. CoreWeave also flags **Managed Auth** (generated kubeconfigs) as the *recommended* path for cluster access; unmanaged OIDC is an advanced alternative and is only available on already-existing clusters (you must use Managed Auth to *create* clusters/VPCs).

**Where to send them:**
- CKS authentication overview (Managed Auth vs SAML SSO vs OIDC WIF vs private-cluster access): https://docs.coreweave.com/products/cks/auth-access/introduction
- Implement unmanaged auth (register an OIDC app, `kubelogin`): https://docs.coreweave.com/products/cks/auth-access/unmanaged-auth/implement-unmanaged-auth
- The `cw-create-cluster` skill, for wiring OIDC in at cluster creation.

---

## Rule of thumb

If the customer wants "my team to log in with our company accounts," it's **SAML SSO** (main `SKILL.md`), optionally plus **AUP/SCIM** for directory sync. Everything on this page is a *machine* or *cluster-API* concern. When in doubt, ask the disambiguation question at the top before configuring anything.
