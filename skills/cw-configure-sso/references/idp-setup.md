# IdP-specific setup: Okta and Microsoft Entra

This reference gives the exact, provider-specific steps for the two identity providers CoreWeave officially supports for SAML SSO **and** Automated User Provisioning (AUP / SCIM): **Okta** and **Microsoft Entra** (formerly Azure AD).

The **why** and the overall flow are in the main `SKILL.md`. This file is the **what** for each IdP.

> **You (Claude) cannot do the IdP half.** These steps happen in the customer's Okta or Entra admin console, which you must not sign into or automate. Read the relevant provider section to the administrator and have them paste values back into the CoreWeave Console (which you may drive if browser tools are connected). Every value marked "copy to" or "copy from" is a manual hand-off.

For any SAML 2.0 IdP that isn't Okta or Entra, SAML SSO still works — the value-exchange pattern (ACS URL + Entity ID into the IdP; SSO URL + Issuer + certificate back into CoreWeave) is identical, only the field names differ. AUP/SCIM, however, is documented and supported only for Okta and Entra.

---

## Okta

### Okta — SAML SSO

Users created through AUP must authenticate via SAML SSO, so configure SAML first regardless of whether you also want SCIM.

1. Open the Cloud Console and the Okta dashboard in separate windows.
   - Cloud Console: [SAML SSO page](https://console.coreweave.com/organization/iam/sso) → **Configure SAML**.
   - Okta: **Applications** → **Create App Integration** → **SAML 2.0**.
2. Copy the **ACS URL** from the Cloud Console into Okta's **Single sign-on URL** field. Leave the checkbox checked (**Use this for Recipient URL and Destination URL**).
3. Copy the **Entity ID** from the Cloud Console into Okta's **Audience URI (SP Entity ID)** field.
4. In Okta's **SAML Attributes / Sign-on** step, add these attribute statements:

   | Name         | Value            |
   | ------------ | ---------------- |
   | `first_name` | `user.firstName` |
   | `last_name`  | `user.lastName`  |
   | `email`      | `user.email`     |

   Click **Next**.
5. For **App Type**, select **This is an internal app we have created**, then **Finish**.
6. Okta now shows the integration details. Under the **Sign-on** tab → **Sign-on methods** → **Metadata details**, expand **More details**, then copy these values back into the Cloud Console's **Identity Provider** (Manual Configuration) section:
   - **Sign-on URL** → CoreWeave **SSO URL** field.
   - **Issuer** → CoreWeave **Entity ID** field.
   - **Signing Certificate** → CoreWeave **Signing certificate** field.
7. In the Cloud Console, click **Next**, then **Deploy SSO**.

### Okta — one-way SCIM (AUP)

Do this only after SAML SSO is deployed.

1. In the Cloud Console [SCIM Configuration page](https://console.coreweave.com/organization/iam/scim), toggle **Enable SCIM API** and **Enable Automated User Provisioning**.
2. Create a new **SCIM Token** (name it, e.g., `Okta ID`) and copy it.
3. In Okta, on the integration's **General** tab → **App Settings** → **Edit**, set **Provisioning** to the **SCIM** radio button and **Save**.
4. Go to Okta's **Provisioning** tab → **SCIM Connection** → **Edit**:
   - **SCIM Connector base URL**: `https://api.coreweave.com/scim/[ORG-USERID]`. Find `[ORG-USERID]` inside the ACS URL from the Cloud Console: `https://console.coreweave.com/accounts/saml/[ORG-USERID]/acs`.
   - **Unique identifier field for users**: `userName`.
   - **Supported provisioning actions**: select **Push New Users**, **Push Profile Updates**, and **Push Groups**. Do **not** enable any import options (one-way sync).
   - **Authentication mode**: **HTTP Header**.
   - **Authorization**: paste the bearer token from the Cloud Console.

   After saving, a **To App** and a **To Okta** tab appear. **To Okta** shows **Import Not Available** — that's expected (one-way).
5. In the **To App** tab → **Provisioning to App** → **Edit**, enable **Create Users**, **Update User Attributes**, and **Deactivate Users**. Do **not** enable **Sync Password** (SAML SSO handles authentication). **Save**.

### Okta — assign users and groups

1. In Okta, **Directory → Groups**, open a group, **People** tab → **Assign people**, add individuals (**include the Org Admin**).
2. In Okta, **Applications → Applications**, open your CoreWeave app, **Assignments** tab → **Assign → Assign to Groups**, find the group, **Assign**, then **Save and go back → Done**.
3. In the Cloud Console [Users page](https://console.coreweave.com/organization/users), refresh — the group's users appear.

### Okta — group-sync gotchas

- **Remove the Department attribute mapping.** In the Okta app's **Provisioning → Attribute Mapping**, remove **Department** before syncing groups — it can block group sync. (This only affects the mapping, not the attribute in Okta.)
- **Nested groups aren't supported.** Push only flat/leaf groups. To avoid errors: push only leaf groups, add a group-membership filter to exclude parents, or use an Okta group rule to flatten memberships first.
- **Recommended pattern:** one regular Okta group holding *all* users to push to CoreWeave, plus push-groups for the subgroups you want represented. Removing a user from a subgroup does not remove them from the "all CoreWeave users" group.
- **Force sync** (Okta-specific) manually pushes attribute updates; it updates attributes but does not activate/deactivate accounts.

---

## Microsoft Entra

### Entra — SAML SSO

1. Open the Cloud Console and the Entra dashboard in separate windows.
   - Cloud Console: [SAML SSO page](https://console.coreweave.com/organization/iam/sso) → **Configure SAML**.
   - Entra ([entra.microsoft.com](https://entra.microsoft.com)): **Enterprise Apps** → **+ New Application** → **Create your own application** → name it → **Integrate any other application you don't find in the gallery (Non-gallery)**. Then open the app → **Single sign-on** (under **Manage**).
2. In Entra, choose **SAML** as the single sign-on method.
3. In Entra, section **1: Basic SAML Configuration** → **Edit**:
   - Copy the **ACS URL** from the Cloud Console into Entra's **Reply URL (Assertion Consumer Service URL)**.
   - Copy the **Entity ID** from the Cloud Console into Entra's **Identifier (Entity ID)**.
4. In Entra, section **2: Attributes & Claims** → **Edit**. For the additional claims, set the **Name** of each to match:

   | Name         | Value            |
   | ------------ | ---------------- |
   | `first_name` | `user.givenname` |
   | `last_name`  | `user.surname`   |
   | `email`      | `user.mail`      |

5. In Entra, section **3: SAML Certificates** → **Edit** → set **Signing Option** to **Sign SAML Response and Assertion** → **Save**.
6. Still in section 3, copy the **App Federation Metadata URL**.
7. In the Cloud Console, select the **Metadata URL** tab and paste that URL. Click **Next**, then **Deploy SSO**.
8. Back in Entra, at the end of the SAML page, click **Test** and complete a Microsoft sign-in. A successful test lands you on the Console **Clusters** page.

### Entra — one-way SCIM (AUP)

Do this only after SAML SSO is deployed.

1. In the Cloud Console [SCIM Configuration page](https://console.coreweave.com/organization/iam/scim), toggle **Enable SCIM API** and **Enable Automated User Provisioning**. Record the **SCIM Base URL** and the **SCIM Token** (create one, e.g., `Entra ID`).
2. In Entra, on your Enterprise App → **Provisioning** (under **Single Sign On**) → **Connect your application** (under **Create configuration**):
   - Paste the **SCIM Base URL** into Entra's **Tenant URL**.
   - Paste the **SCIM Token** into Entra's **Secret token**.
   - Click **Test connection** (a green alert confirms success), then **Create**.

### Entra — assign users and groups

1. In Entra, on the Enterprise App → **Users and groups** (under **Manage**) → **Add user/group**, select the users/groups to sync.
2. In Entra, under **Provisioning**, toggle **Provisioning Status** to **On** (required the first time you enable provisioning).
3. In the Cloud Console [Users page](https://console.coreweave.com/organization/users), refresh — the assigned users appear.

### Entra — SSH keys for SUNK (only if using SUNK)

If the org uses SUNK, SSH public keys can be synced from Entra to CoreWeave by creating a custom extension attribute on the app's backing registration (via the Microsoft Graph PowerShell module) and mapping it to CoreWeave's SCIM SSH-key attribute.

<!-- TODO(SME): The Entra SUNK SSH-key extension procedure (Microsoft.Graph PowerShell: Connect-MgGraph, find app Object ID, create the extension property, map it in the app's provisioning attribute mappings) was truncated in the source docs during authoring. Before relying on the exact PowerShell commands / attribute names, verify against
https://docs.coreweave.com/security/automated-user-provisioning/configure-microsoft-entra (Map SSH keys for SUNK section). The Okta equivalent (SUNK attribute reference + sunkSshKeys mapping under the urn:coreweave:params:scim:schemas:extension:coreweave:2.0:CoreWeaveUser namespace) is fully documented; see the Okta AUP page. Skip this section entirely if the customer is not using SUNK. -->

---

## Common SCIM attributes and constraints (both IdPs)

- **One-way sync only.** The IdP is the source of truth; never enable import-from-CoreWeave.
- **Flat groups only.** Nested groups cause provisioning failures on both IdPs.
- **Legacy default groups.** Older CoreWeave orgs auto-provisioned groups named `admin`, `metrics`, `read`, `write`, `billing_viewer` with default policies attached. If a pushed group collides with one of these, create a renamed group with the equivalent policies and delete the default before pushing. (See `cw-add-users/references/roles.md` for the legacy-group → role mapping — do not edit that file; just read it.)
- **SCIM does not grant permissions.** It provisions identities and memberships only. Attach a Platform Access policy to the synced group (the `cw-add-users` workflow) to grant roles.

## Sources

- Configure AUP with Okta: https://docs.coreweave.com/security/automated-user-provisioning/configure-okta
- Configure AUP with Microsoft Entra: https://docs.coreweave.com/security/automated-user-provisioning/configure-microsoft-entra
- Configure SAML SSO: https://docs.coreweave.com/security/authn-authz/saml-sso/configure-saml-sso
- AUP introduction (SCIM, one-way sync, flat groups): https://docs.coreweave.com/security/automated-user-provisioning
