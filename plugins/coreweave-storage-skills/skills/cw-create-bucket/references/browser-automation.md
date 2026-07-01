# Drive the CoreWeave Cloud Console with browser tools

This reference explains *how* to execute the bucket-creation workflow automatically when browser tools are available. The **what** (verify permissions → create access key → create org policy → create bucket) is in the main `SKILL.md`; this file covers the mechanics.

## When to use this reference

Read this reference when all of the following conditions apply:

- The customer confirms they want browser-assisted setup.
- You've detected browser automation tools in your environment (Claude in Chrome, Playwright MCP, Control Chrome, computer use, or a `browser_*` toolset).
- The customer has `console.coreweave.com` open and is signed in.

If none of those are true, use the manual workflow in `SKILL.md` instead.

---

## Core principles

### 1. Tool-agnostic

Don't hardcode tool names. Use whatever browser tools are available. Prefer semantic tools (`find`, `form_input`) over pixel-based ones (click at X,Y coordinates) — semantic tools survive UI changes.

Run `/mcp` (in Claude Code) or check the tool list to see what's connected before starting.

### 2. Plan → approve → execute → verify

1. **State the plan**: "I'll check for an existing org access policy, create one if needed, then create bucket `<name>` in zone `<zone>`. Does that look right?"
2. **Get explicit approval** for the plan.
3. **Execute one step at a time**, narrating what you're about to do.
4. **Verify** after each step with a screenshot or page-text read.
5. **Pause before each consequential action** — submitting a policy, creating a bucket.

### 3. Text anchors, not selectors

Find elements by their visible label: the **Create Bucket** button, the **Policy name** field, the **Zone** dropdown. Button labels survive refactors that break CSS selectors.

### 4. Fail loud, fail safe

If you can't find an element, take a screenshot, describe what's visible on screen, and ask the customer to help. Never click blindly.

### 5. Hand off at login and 2FA

If you reach a login page, a 2FA prompt, or a CAPTCHA, stop and tell the customer to sign in and let you know when they're through.

---

## Step-by-step playbook

### Step 0 — Orient yourself

1. Take a screenshot. Confirm you're on `console.coreweave.com` and signed in.
2. Read the page text to confirm which organization is active.

**Don't navigate to pages by URL.** Use the sidebar. The reliable entry URL is `console.coreweave.com/clusters`.

### Step 1 — Check for existing org access policy

**Prepare.** Click the sidebar: **Policies** → look for the **Object Storage access** tab.

**Act.** Read the policy list. If any policy exists that grants appropriate access, note it and move to bucket creation.

**Verify.** Take a screenshot of the policy list.

### Step 2 — Create org access policy (if needed)

**Prepare.** On the **Object Storage access** tab, click **Create policy**.

**Act.**
1. Fill the **Name** field using `form_input`.
2. Add a statement with Name, Access (Allow), Principals (`role/Admin`), Actions (`*`), Resources (`*`).

**Checkpoint before submitting.** Read back the policy to the customer and confirm.

3. Click **Submit**.

**Verify.** The new policy appears in the list.

### Step 3 — Create the bucket

**Prepare.** Navigate to **Object Storage** → **Buckets**.

**Act.**
1. Click **Create Bucket**.
2. Fill the **Name** field using `form_input`.
3. Select the **Availability Zone** from the dropdown.
4. If versioning is needed, enable it.

**Checkpoint before creating.** Confirm the bucket name and zone with the customer.

5. Click **Create**.

**Verify.** The bucket appears in the bucket list with the correct zone.

---

## Tactics

- **Use `form_input` for text fields.** The `type` action can detach mid-word in Console modals. `form_input` sets the full value atomically.
- **Wait for async state.** After a click, the page re-renders. Take a screenshot to confirm before proceeding.
- **Close modals cleanly.** Use Cancel or X — don't navigate away.
- **One tab, one context.** Don't open extra tabs.

## If things go wrong

- **Element not found**: Screenshot, describe, ask the customer to click manually.
- **Session dropped**: Hand back to the customer to sign in.
- **Action completed but unverifiable**: Ask the customer what they see.

## What not to automate

- **Deleting buckets or policies** — this skill only creates resources.
- **Access key secret retrieval** — the customer must copy this themselves for security.
