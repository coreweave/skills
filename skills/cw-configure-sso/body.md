
# Configure single sign-on (SSO) for CoreWeave

You are helping a CoreWeave Cloud Console administrator let their whole team sign in with their existing corporate identity provider (IdP) instead of individual CoreWeave credentials.

On CoreWeave, "SSO" is **SAML SSO**, configured per organization in the Cloud Console. Two related, layered capabilities exist:

1. **SAML SSO** — the foundation. Members authenticate through your IdP (Okta, Microsoft Entra / Azure AD, or any SAML 2.0 IdP). Configure this first; nothing else works without it.
2. **Automated User Provisioning (AUP)** — optional, built on SAML SSO. Uses **SCIM** (one-way, IdP → CoreWeave) to sync entire user directories and group memberships automatically, so you don't invite people one at a time or wait for first login. Supported IdPs for AUP are **Okta** and **Microsoft Entra**.

> **Scope check — read this before promising anything.** CoreWeave supports two other things that customers sometimes call "SSO" or "OIDC" but which this skill does **not** cover, because they are different jobs (or not customer-self-serve):
>
> - **OIDC Workload Identity Federation** and **cluster-level OIDC / auth webhooks** authenticate *workloads and `kubectl`*, not people logging into the Console. If the customer wants machine-to-machine auth (e.g. a CKS workload reaching AI Object Storage) or `kubectl` OIDC login to a cluster's API server, that is a separate workflow — see `references/related-oidc.md` and point them at the right docs; don't configure it here.
> - **Akuity / Argo CD login** for a co-managed GitOps setup is **not self-serve in the Console.** CoreWeave's Solutions Architecture team provisions and wires up that authentication (through CoreWeave's own SSO broker). If that's what they're asking about, see `references/akuity-argocd.md` and route them to their CoreWeave contact rather than attempting Console steps.
>
> If you're unsure which one the customer means, ask: *"Do you want your team to log into the CoreWeave Cloud Console with your company IdP (that's SAML SSO), or is this about workloads / `kubectl` / Argo CD authenticating?"*

The complementary **`cw-add-users`** skill covers inviting individual users who don't authenticate through your IdP, plus creating groups and Platform Access policies. **SSO and manual invites coexist** — configuring SSO does not remove existing users. Use `cw-add-users` for one-off teammates; use this skill for org-wide federation.

---

## Before you start

Confirm the following before proceeding:

- The administrator has the **IAM Admin** role (or is the first/only administrator on the account). Configuring SAML SSO and SCIM requires IAM Admin. `CKS Admin` also grants the ability to configure SAML SSO.
- The administrator also has **admin access to the IdP** (Okta admin, Entra admin, etc.). Half of this workflow happens in the IdP's console, not CoreWeave's — you cannot finish without it.
- They know which IdP they use.
- They have decided whether they want just SAML SSO, or SAML SSO **plus** AUP/SCIM. If they're not sure, default to SAML SSO first; AUP can be added later.

**The Cloud Console is at `console.coreweave.com`** (not `cloud.coreweave.com`). All paths below assume the administrator is signed in there.

**You'll need the organization's Org ID.** Find it on the [Account Settings page](https://console.coreweave.com/account/settings) (a short hex string like `ab1cd2`). It appears in the SSO login URL and the SCIM base URL.

**Probe for browser access before asking.** Attempt `tabs_context_mcp` (or your environment's equivalent) silently. There are three outcomes:

1. **Connected** — browser tools are live. Tell the administrator:
   > "I can drive the CoreWeave side of this in the Console for you. Note that SAML setup is a two-window dance: some values come from your IdP (Okta/Entra), which I can't sign into for you. I'll pause before deploying the SSO policy and before enabling SCIM. Want me to proceed?"
   If yes, read `references/browser-automation.md` and follow the patterns there.

2. **Not connected, but tools exist** — browser tools are loaded but the extension isn't reachable. Tell the administrator:
   > "I have browser tools available but they're not connected to Chrome yet. I can walk you through a quick setup (about 2 minutes), or walk you through this manually instead. Which do you prefer?"
   If they want setup, use the `cw-console-browser-access` skill, then retry.

3. **No browser tools** — no browser MCP in scope. Skip the offer and go straight to the manual walkthrough below.

> **Even with browser access, expect to hand off repeatedly.** This workflow crosses two admin consoles (CoreWeave and the IdP) and involves copying signing certificates and tokens between them. Drive the CoreWeave side; hand the IdP side to the administrator and ask them to paste values back. Never attempt to sign into or automate the customer's IdP.

---

## Part A — Configure SAML SSO (required)

This is the foundation. When it's done, your team can sign in at your org's dedicated SSO login URL and be redirected to your IdP.

### Step A1 — Open the SAML SSO configuration in the Console

**Navigation:** [SAML SSO configuration page](https://console.coreweave.com/organization/iam/sso) → click **Configure SAML**.

CoreWeave supports two ways to enter your IdP's details. Pick the one that matches what your IdP exposes:

- **Metadata URL** (preferred when available) — you paste a single metadata URL and CoreWeave reads the SSO URL, Entity ID, and certificate automatically. Fewer transcription errors. Microsoft Entra publishes an "App Federation Metadata URL", so Entra users usually take this path.
- **Manual configuration** — you enter each value by hand: the IdP's **SSO URL**, its **Entity ID**, and an **X.509 signing certificate**. Use this when your IdP doesn't publish a metadata URL (common with Okta's per-app setup, where you copy individual values).

> **The SAML *response* must be signed by your IdP, or authentication fails.** Signing the assertion is optional and separate — if your IdP also signs the assertion, expand **Advanced settings** and select **Require signed assertion** during CoreWeave configuration.

### Step A2 — Create the matching SAML app in your IdP (administrator does this)

This half happens in the IdP. The CoreWeave **Configure SAML** dialog displays two values you copy *into* the IdP:

- **ACS URL** → paste into the IdP's Single sign-on URL / Reply URL field.
- **Entity ID** → paste into the IdP's Audience URI / Entity ID field.

Then the IdP generates its own values (Sign-on / SSO URL, Issuer, and a signing certificate) that you copy *back* into CoreWeave (manual config) — or you copy the IdP's metadata URL back into CoreWeave (metadata path).

For exact click-by-click steps for **Okta** and **Microsoft Entra** — including the attribute mappings — see `references/idp-setup.md`. For any other SAML 2.0 IdP, the same value-exchange pattern applies; the field names differ.

### Step A3 — Deploy the SSO policy

Back in the CoreWeave **Configure SAML** dialog:

1. Enter the values (or metadata URL) collected from the IdP.
2. If the IdP signs the assertion, expand **Advanced settings** → **Require signed assertion**.
3. Click **Next**.
4. Confirm the values shown in the dialog are correct.

> **🛑 Checkpoint before deploying.** Read the configuration back to the administrator — which IdP, metadata-vs-manual, and whether "Require signed assertion" is set — and confirm before clicking. Deploying SSO changes org-wide sign-in behavior. Get an explicit yes.

5. Click **Deploy SSO** to activate the policy.

### Step A4 — Add the identity attributes in your IdP (administrator does this)

CoreWeave needs three attributes from the IdP to identify each user. In the IdP's SAML app, add:

| Key          | Description                        |
| ------------ | ---------------------------------- |
| `email`      | User's email (unique identifier)   |
| `first_name` | User's first name                  |
| `last_name`  | User's last name                   |

(The IdP-side source values differ per provider — for example Okta uses `user.firstName`/`user.lastName`/`user.email`; Entra uses `user.givenname`/`user.surname`/`user.mail`. See `references/idp-setup.md`.)

While in the IdP settings, **verify SAML response signing is enabled** — an unsigned response fails authentication.

### Step A5 — Share the SSO login URL

After deploying, direct the team to your org's dedicated SSO login URL. It embeds your Org ID:

```text
# Replace [ORG-ID] with your Org ID from console.coreweave.com/account/settings
https://console.coreweave.com/accounts/saml/[ORG-ID]/login
```

Users who visit this URL are redirected to your IdP, authenticate, and return to the Console. Because SAML SSO supports **Just-In-Time (JIT) provisioning**, a user's CoreWeave account is created on their first successful SSO sign-in even if they were never invited.

> **Managing or disabling the policy later:** returning to the SAML SSO page shows **Enable SAML**, **Disable SAML**, and **Edit** controls.

> **"Can I force SSO and turn off passwords?"** The Console does **not** expose an org-wide setting to disable password / social sign-in. If the administrator needs to *enforce* SSO as the only sign-in path, that requires **contacting CoreWeave Support** — there is no self-serve toggle. Say this plainly; don't imply a switch exists.

**If they only wanted SSO, you're done here.** Continue to Part B only if they want automatic user/group federation.

---

## Part B — Automated User Provisioning with SCIM (optional)

AUP syncs users and groups from your IdP into CoreWeave automatically, in real time, using **one-way SCIM** (IdP is the source of truth; data flows only IdP → CoreWeave). Without AUP, accounts appear only when a user first signs in (JIT) or when you invite them manually. With AUP, assigning a user or group to the app in your IdP creates them in CoreWeave immediately; removing them deactivates them.

**Prerequisites for Part B:**

- **SAML SSO from Part A must already be deployed.** SCIM builds on it — SAML handles authentication, SCIM handles provisioning.
- Your IdP must be **Okta or Microsoft Entra** (the two AUP-supported providers).

### Step B1 — Enable SCIM in the Console

**Navigation:** [SCIM Configuration page](https://console.coreweave.com/organization/iam/scim).

1. Toggle **Enable SCIM API** and **Enable Automated User Provisioning**. (SCIM controls org-wide user data, so it must be explicitly enabled.)
2. Note the **SCIM Base URL** shown on this page — you'll paste it into the IdP.
3. Create a new **SCIM Token** with a name of your choice (for example, `Okta ID` or `Entra ID`). **Copy the token immediately** — it's a bearer credential the IdP uses to authenticate to CoreWeave.

> **🛑 Checkpoint before enabling SCIM.** Confirm with the administrator that they want directory sync turned on, and treat the SCIM token like a password. If browser-driving, do **not** echo the token into chat — tell the administrator to copy it from the page directly.

### Step B2 — Connect provisioning in your IdP (administrator does this)

In the IdP's provisioning section, set the SCIM connector's base URL to the value from Step B1, set the authentication mode to HTTP header / bearer token, paste the SCIM token, and enable the push actions (create users, update attributes, deactivate users, push groups). Exact steps and field names for **Okta** and **Entra** are in `references/idp-setup.md`.

Key gotchas that trip everyone up (details in the reference):

- **One-way only** — do not enable any *import* options; only push/provision-to-app.
- **Flat groups only** — CoreWeave SCIM does **not** support nested groups. Pushing a parent group whose members include other groups causes provisioning errors. Push only leaf groups.
- **Okta:** remove the **Department** attribute mapping (it can block group sync).
- **Legacy CoreWeave orgs:** avoid pushing groups named `admin`, `metrics`, `read`, `write`, or `billing_viewer` (they collide with default groups) — rename or resolve the conflict first.

### Step B3 — Assign users and groups, then verify

In the IdP, assign the users/groups to the CoreWeave app (Okta: assign group to the app; Entra: add users/groups and toggle Provisioning Status **On**). Then in the Console, refresh the [Users page](https://console.coreweave.com/organization/users) — the assigned users appear.

> **Permissions still come from policies.** SCIM syncs *who* exists and *which groups* they're in. It does **not** grant CoreWeave roles. Attach a **Platform Access policy** to the synced group so members can actually do anything — that's the `cw-add-users` workflow (Step 2/3 there). Offer to hand off to `cw-add-users` for the policy step if they haven't set one up.

---

## Common mistakes

**❌ Trying to configure SCIM/AUP before SAML SSO is deployed.** AUP requires an active SAML SSO policy. Do Part A completely first.

**❌ Forgetting the IdP-side attributes.** If `email`, `first_name`, `last_name` aren't mapped in the IdP, or the SAML response isn't signed, sign-in fails even though the CoreWeave policy looks correct.

**❌ Confusing user SSO with workload/OIDC federation.** SAML SSO = people logging into the Console. OIDC Workload Identity Federation and cluster auth webhooks = machines/`kubectl`. Different pages, different job. See `references/related-oidc.md`.

**❌ Assuming Akuity/Argo CD login is self-serve.** It isn't — CoreWeave's SA team provisions it. See `references/akuity-argocd.md`.

**❌ Pushing nested groups over SCIM.** CoreWeave SCIM only supports flat groups. Nested groups error out.

**❌ Expecting SCIM to grant permissions.** It only provisions identities and group membership. Roles come from Platform Access policies (`cw-add-users`).

---

## References

The following resources support this workflow:

- `references/idp-setup.md` — Exact click-by-click SAML + SCIM setup for **Okta** and **Microsoft Entra**, including attribute mappings and the SCIM gotchas.
- `references/browser-automation.md` — How to drive the Console for the CoreWeave side of this workflow: the two-window model, where to hand off to the administrator for IdP steps, verification after each step, and the mandatory pauses before deploying SSO and enabling SCIM.
- `references/related-oidc.md` — What OIDC Workload Identity Federation and cluster-level OIDC/auth webhooks are, and why they're out of scope for user SSO (with the right docs links to redirect the customer).
- `references/akuity-argocd.md` — What CoreWeave's managed Akuity/Argo CD authentication is, why it's not self-serve, and how to route the customer.
- CoreWeave docs — SAML SSO: https://docs.coreweave.com/security/authn-authz/saml-sso/intro-saml-sso
- CoreWeave docs — Configure SAML SSO: https://docs.coreweave.com/security/authn-authz/saml-sso/configure-saml-sso
- CoreWeave docs — Automated User Provisioning: https://docs.coreweave.com/security/automated-user-provisioning
