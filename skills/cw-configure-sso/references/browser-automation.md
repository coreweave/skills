# Drive the CoreWeave Cloud Console for SSO setup with browser tools

This reference explains *how* to execute the SSO workflow when browser tools are available. The **what** (configure SAML SSO → optionally enable SCIM/AUP) is in the main `SKILL.md`; this file covers the mechanics.

The single most important difference from other Console skills: **this workflow spans two admin consoles.** You drive only the CoreWeave side. The identity-provider side (Okta, Entra) is the administrator's job — you cannot and must not sign into or automate it.

## When to use this reference

Read this reference when all of the following apply:

- The administrator confirmed they want browser-assisted setup.
- You've detected browser automation tools (Claude in Chrome, Playwright MCP, Control Chrome, computer use, or a `browser_*` toolset).
- The administrator has `console.coreweave.com` open and is signed in. (The hostname is `console.coreweave.com`, not `cloud.coreweave.com`.)

If none of those are true, use the manual workflow in `SKILL.md`.

---

## Core principles

### 1. Two windows, one driver

You drive the CoreWeave Console. The administrator drives their IdP. The workflow is a relay of values between the two:

- CoreWeave shows **ACS URL** and **Entity ID** → administrator pastes into the IdP.
- The IdP produces **SSO URL / Issuer / signing certificate** (or a **metadata URL**) → administrator pastes back into CoreWeave (you can fill the CoreWeave fields once they give you the values).
- For SCIM: CoreWeave shows a **SCIM base URL** and a **SCIM token** → administrator pastes into the IdP.

State clearly, each time, who does what: "I'll read you the ACS URL from the CoreWeave dialog — paste that into Okta's Single sign-on URL field, then tell me the Issuer and certificate Okta gives you and I'll enter them here."

### 2. Tool-agnostic

Don't hardcode tool names. Claude in Chrome exposes `navigate`, `find`, `form_input`, `read_page`, `computer`. Playwright MCP exposes `browser_navigate`, `browser_click`. Prefer semantic tools (`find`, `form_input`) over pixel-based ones — they survive UI changes. Run `/mcp` (in Claude Code) or check the tool list before starting.

### 3. Plan → approve → execute → verify

1. **State the plan**: "I'll open the SAML SSO page and start the Configure SAML dialog. You'll set up the matching app in Okta. Once you paste the Issuer/certificate back to me, I'll deploy the SSO policy — but I'll pause for your explicit yes first. If you also want directory sync, we'll then enable SCIM. Sound right?"
2. **Get explicit approval.**
3. **Execute one step at a time**, narrating.
4. **Verify** after each step with a screenshot or page-text read.
5. **Pause before each consequential action** — deploying the SSO policy, enabling SCIM.

### 4. Text anchors, not selectors

Find elements by visible label: the **Configure SAML** button, the **Metadata URL** tab, the **SSO URL** field, the **Deploy SSO** button, the **Enable SCIM API** toggle. Labels survive refactors that break CSS selectors.

### 5. Never handle secrets carelessly

The **signing certificate** and especially the **SCIM token** are sensitive. When entering them into CoreWeave fields, use `form_input` to set the value directly. **Do not echo the SCIM token back into the chat.** When CoreWeave generates the SCIM token, tell the administrator to copy it straight from the page — don't read it aloud.

### 6. Hand off at login and 2FA

If you hit a CoreWeave login page, 2FA prompt, or CAPTCHA, stop: "I've reached a sign-in screen — please sign in and let me know when you're through." Never attempt authentication.

---

## Step-by-step playbook

Each step: **prepare** (navigate, verify state), **act**, **verify**.

### Step 0 — Orient

1. Take a screenshot. Confirm you're on `console.coreweave.com` and signed in.
2. If it's a login screen or a different hostname, hand control back.
3. Confirm which organization is active (read page text). If the administrator belongs to multiple orgs, confirm the right one — SSO is org-wide.
4. Grab the **Org ID** from **Account Settings** (`console.coreweave.com/account/settings`); you'll need it for the SSO login URL and the SCIM base URL.

**Don't navigate to admin pages by guessing URLs.** Use the known-good paths below or the sidebar. The reliable landing page is `console.coreweave.com/clusters`.

### Step 1 — Open the SAML SSO configuration

**Prepare.** Navigate to `console.coreweave.com/organization/iam/sso`. Screenshot to confirm you're on the SAML SSO page.

**Act.** Click **Configure SAML**. Choose the tab that matches the IdP: **Metadata URL** (Entra typically) or **Manual Configuration** (Okta typically).

**Verify.** The dialog shows the CoreWeave-side values (ACS URL, Entity ID) the administrator needs. Read those to the administrator.

### Step 2 — Relay values with the administrator (IdP side is theirs)

Hand off: have the administrator paste ACS URL + Entity ID into the IdP and set the attribute claims (`email`, `first_name`, `last_name`) and response signing. Wait for them to give you back either the **metadata URL** or the **SSO URL / Issuer / signing certificate**.

**Act.** Fill the CoreWeave dialog fields with the values the administrator provides, using `form_input`. If the IdP signs the assertion, expand **Advanced settings** and select **Require signed assertion** (confirm with the administrator whether their IdP signs the assertion).

**Verify.** Click **Next** and read back the confirmation screen to the administrator.

### Step 3 — Deploy the SSO policy

**🛑 Checkpoint before deploying.** Deploying changes org-wide sign-in. Read back: which IdP, metadata-vs-manual, and whether "Require signed assertion" is on. Get an explicit "yes, deploy."

**Act.** Click **Deploy SSO**.

**Verify.** The page shows the policy as active (Enable/Disable/Edit controls appear). Give the administrator their SSO login URL: `https://console.coreweave.com/accounts/saml/[ORG-ID]/login`. Suggest they test it in a private window before rolling it out to the team.

**If they only wanted SSO, stop here.** Only continue to SCIM if they asked for directory sync.

### Step 4 — Enable SCIM (optional, only after SSO is deployed)

**Prepare.** Navigate to `console.coreweave.com/organization/iam/scim`. Screenshot to confirm.

**🛑 Checkpoint before enabling SCIM.** Confirm the administrator wants org-wide directory sync turned on.

**Act.**
1. Toggle **Enable SCIM API** and **Enable Automated User Provisioning**.
2. Note the **SCIM Base URL** for the administrator.
3. Create a **SCIM Token**. **Tell the administrator to copy it from the page directly** — do not read it into chat.

**Verify.** SCIM shows as enabled. Hand off to the administrator to complete the IdP provisioning connection (base URL + token + push actions) per `references/idp-setup.md`.

### Step 5 — Verify provisioning

After the administrator assigns users/groups in the IdP, navigate to `console.coreweave.com/organization/users` and refresh. Confirm the synced users appear. Remind the administrator that roles still come from a Platform Access policy attached to the group (the `cw-add-users` workflow).

---

## Tactics that save you pain

- **Use `form_input` for text fields** (SSO URL, Entity ID, certificate, metadata URL). The `type` action can detach mid-string in Console modals.
- **Certificates are multi-line.** Set the whole PEM block atomically with `form_input`; don't type it character by character.
- **Wait for async state.** After a click the page re-renders; screenshot or re-find before acting.
- **Close modals cleanly.** Use Cancel/X to abandon a dialog; some Console modals treat navigation-away as a failed write.
- **One tab, one context.** Don't open extra CoreWeave tabs — org/session context can differ across tabs.

## If things go wrong

- **Element not found**: screenshot, describe what's visible, ask the administrator to click it manually.
- **SAML test fails after deploy**: usual causes are the SAML response not being signed, or missing `email`/`first_name`/`last_name` attributes in the IdP. Send the administrator back to the IdP's attribute + signing settings (`references/idp-setup.md`).
- **SCIM "test connection" fails in the IdP**: re-check the base URL and that the token was pasted intact and as an HTTP-header bearer token.
- **Session dropped**: hand back to the administrator to sign in.

## What not to automate

- **The identity provider side.** Never sign into or drive Okta / Entra / Google Workspace. That's the administrator's job — always.
- **Deploying SSO or enabling SCIM without an explicit yes.** Both are org-wide changes. Get spoken confirmation at the checkpoints.
- **Disabling SAML or deleting an existing policy.** This skill only *creates* SSO configuration. Disabling/removing is a separate, deliberate action — hand it to the administrator.
- **Enforcing SSO-only / disabling passwords.** There is no self-serve toggle; that's a CoreWeave Support request. Don't hunt for a hidden setting.

## Checklist before you call it done

- The SAML SSO policy shows as active on the SSO page.
- The administrator has tested the SSO login URL and can sign in through the IdP.
- (If SCIM) SCIM API and AUP are enabled, and at least one assigned user has synced to the Users page.
- You've reminded the administrator that group members still need a Platform Access policy for roles (`cw-add-users`).
- You've summarized: which IdP, whether SCIM is on, the SSO login URL, and any follow-ups (e.g., enforce-SSO via Support).
