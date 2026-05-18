# Drive the CoreWeave Cloud Console with browser tools

This reference explains *how* to execute the user-add workflow automatically when browser tools are available. The **what** (create group → create policy → invite users) is in the main `SKILL.md`; this file covers the mechanics.

## When to use this reference

Read this reference when all of the following conditions apply:

- The administrator confirms they want browser-assisted setup.
- You've detected browser automation tools in your environment (Claude in Chrome, Playwright MCP, Control Chrome, computer use, or a `browser_*` toolset).
- The administrator has `console.coreweave.com` open and is signed in. (The Console hostname is `console.coreweave.com`, not `cloud.coreweave.com`.)

If none of those are true, use the manual workflow in `SKILL.md` instead.

---

## Core principles

### 1. Tool-agnostic

Don't hardcode tool names. Claude in Chrome exposes `navigate`, `find`, `form_input`, `read_page`, `computer`, and similar tools. Playwright MCP exposes `browser_navigate`, `browser_click`. Computer use exposes screenshot + click at coordinates. Use whatever you have. If multiple tools are available, prefer semantic tools (`find`, `form_input`) over pixel-based ones (click at X,Y coordinates) — semantic tools survive UI changes.

Run `/mcp` (in Claude Code) or check the tool list to see what's connected before starting.

### 2. Plan → approve → execute → verify

Follow these steps before and during any automation:

1. **State the plan**: "I'll create group `engineering`, then a policy `engineering-access` with roles CKS Admin, Object Storage Admin, Access Token Admin, and Observability Viewer, then invite alice@acme.com and bob@acme.com. Does that look right?"
2. **Get explicit approval** for the plan.
3. **Execute one step at a time**, narrating what you're about to do.
4. **Verify** after each step with a screenshot or page-text read.
5. **Pause before each consequential action** — saving a policy, sending an invitation.

### 3. Text anchors, not selectors

Find elements by their visible label: the **Create group** button, the **Groups** field, the **Email** input. CoreWeave's UI can change; button labels survive refactors that break CSS selectors. Use semantic find where your tool supports it.

### 4. Fail loud, fail safe

If you can't find an element, take a screenshot, describe what's visible on screen, and ask the administrator to help. Never click blindly — an incorrect action in an IAM console can grant unintended access or send unexpected email to real users.

### 5. Hand off at login and 2FA

If you reach a login page, a 2FA prompt, or a CAPTCHA, stop and tell the administrator: "I've reached a sign-in screen — please sign in and let me know when you're through."

---

## Step-by-step playbook

Each step has three parts: **prepare** (navigate and verify state), **act** (perform the actions), **verify** (confirm success). One screenshot per step is usually enough; take more if something looks wrong.

### Step 0 — Orient yourself

1. Take a screenshot. Confirm you're on `console.coreweave.com` and signed in.
2. If the page shows a login screen or a different hostname, hand control back to the administrator.
3. Read the page text to confirm which organization is active. If the administrator has multiple organizations, confirm which one before proceeding.

**Don't navigate to admin pages by URL.** Paths like `/organizations`, `/users`, and `/admin` redirect silently or return 404 depending on the Console version. Always use the sidebar. The reliable entry URL is `console.coreweave.com/clusters` (the default landing page).

### Step 1 — Create a group

**Prepare.** Click the sidebar: **Administration** → **Users and Groups** → **Groups** tab.

**Act.**
1. Click **Create group**.
2. In the modal that appears, find the **Name** field. Use `form_input` rather than click-then-type — it's more reliable for text fields in this Console.
3. Enter the agreed-upon group name.
4. Click **Create**.

**Verify.** The new group appears in the group list. If an error toast appears, read it verbatim to the administrator and stop.

### Step 2 — Create the Platform Access policy

**Prepare.** Click **Policies** in the sidebar. The page opens on the **Object Storage access** tab by default — click the **Platform access** tab to switch. The correct URL is `/organization/iam/access-policies`. Take a screenshot to confirm you're on the right tab before proceeding.

**Act.**
1. Click **Create policy**.
2. Fill the **Name** field using `form_input`.
3. Scroll down to **Rule 1**. The rule has three fields: **Users**, **Groups**, and **Roles**.
   - Leave **Users** empty. Don't add individuals here.
   - Click **Groups** and select the group you just created from the dropdown. (The dropdown lists all groups; `grafana-viewers` and similar will appear by name.)
   - Click **Roles** and search for each role by name. Select each one. Use `form_input` or type in the search box if your tool supports it — typing filters the list immediately.

**🛑 Checkpoint before saving.** Read back the complete policy to the administrator — name, group, and full role list — and ask them to confirm before clicking Save. Example: "About to save policy `engineering-access`: assigns group `engineering` the roles CKS Admin, Object Storage Admin, Access Token Admin, and Observability Viewer. Does this look correct?"

If the policy includes **IAM Admin**, call this out explicitly: "This role gives the group full administrative control over users and permissions in this organization."

4. Click **Save policy** twice. The first click collapses the rule into a read-only summary view but does not submit the form. The second click actually saves the policy. Verify the URL changes from `/create` to `/access-policies` after the second click.

**Verify.** The new policy appears in the Platform access policy list with the correct rule count.

### Step 3 — Invite users

**Prepare.** Navigate to **Administration** → **Users and Groups** → **Users** tab.

**🛑 Checkpoint before the first invitation.** Confirm the full list with the administrator before sending any invitation. Example: "I'm about to send invitations to alice@acme.com and bob@acme.com. Each invitation creates a pending account for that address and sends an email immediately. Should I proceed?"

**Act** (per user):
1. Click **Invite user**.
2. In the **Email** field, use `form_input` to enter the address — this avoids partial-input issues with the `type` action.
3. Open the **Groups** dropdown and select the group from Step 1.
4. Click **Invite**.
5. Confirm the user appears in the Users list with `Invited` status before moving to the next address.

If a send fails (for example, due to an invalid email format), stop the batch, report which addresses succeeded and which failed, and ask before retrying.

When all invitations are sent and verified, the workflow is complete. See the checklist below to confirm everything is in order before closing out.

---

## Tactics that save you pain

Apply the following practices to avoid common automation pitfalls:

- **Use `form_input` for text fields.** The `type` action can detach mid-word in some Console modals, leaving a partial character in the field. `form_input` sets the full value atomically. Always prefer it for the **Name**, **Email**, and search fields.
- **Wait for async state.** After a click, the page re-renders. If your tool has a wait/retry in its find, use it. If not, take a screenshot and confirm the expected state before proceeding.
- **Re-read between steps.** A Save button may shift position, or an error toast may appear. Reading state is cheap; acting on stale state is not.
- **Close modals cleanly.** If you need to abandon a modal, find and click its Cancel or X button — don't navigate away. Some Console modals treat navigation as a failed write.
- **One tab, one context.** Don't open extra tabs during this workflow. Session state and dropdown contents can differ across tabs if the administrator belongs to multiple organizations.

## If things go wrong

Use the following recovery steps when automation encounters an error:

- **Element not found**: Take a screenshot, describe what's visible, and ask the administrator to click the element manually and tell you when done.
- **Modal dialog blocking the browser**: JavaScript dialogs (`alert`, `confirm`) block all browser events. Ask the administrator to dismiss the dialog, then continue.
- **Session dropped or signed out**: Hand back to the administrator to sign in. Don't attempt authentication.
- **Action completed but you can't verify it**: Ask the administrator what they see on screen.

## What not to automate

The following actions must not be automated with browser tools:

- **SSO/SAML configuration** — requires careful manual steps.
- **Deleting users, policies, or groups** — this skill only creates resources. Deletions require a separate, more deliberate workflow.
- **Adding IAM Admin to a group** — you must get explicit typed confirmation from the administrator, not just a verbal nod. Ask them to type the role name or "confirm" in the chat.

---

## Checklist before you call it done

Verify each of the following before closing out the workflow:

- The new group appears in the Groups list.
- The new policy appears in the Platform access list and references the correct group and roles.
- Each invited email address appears in the Users list with `Invited` status.
- You've summarized the result to the administrator: "Created group X, policy Y with roles A, B, C, and sent invitations to Z addresses. They'll receive an email to activate their account."
