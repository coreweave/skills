---
name: cw-add-users
description: >
  Guides CoreWeave Cloud Console administrators through inviting new non-OIDC
  users to their organization — creating a group, attaching a Platform Access
  policy with the right roles, and then sending invitations in the correct
  order. Use this skill whenever a customer mentions adding users, inviting
  teammates, setting up permissions, creating groups or policies, or onboarding
  new people to their CoreWeave account — even if they don't say "add users"
  explicitly. Also use it when someone asks what IAM roles they should assign
  or which permissions a new team member should have. Probes for browser access
  at the start and drives the Console automatically if available; otherwise
  walks the administrator through it manually.
---

# Add users to CoreWeave (non-OIDC)

You are helping a CoreWeave Cloud Console administrator invite new users from their organization, set up appropriate permissions, and avoid a common gotcha that trips up first-timers.

**The correct order matters.** Read through the whole workflow before starting — **inviting users before creating the group and policy means you can't assign those users to the group until they accept the invitation**. Do the setup first, then send invitations.

**Do as much as possible before asking.** Probe the local environment for organization context and try to drive the Console via browser automation. Only fall back to manual walkthrough when browser tools are unavailable.

---

## Before you start — silent environment probe

Run these checks silently before saying anything to the administrator.

### 1. Check kubeconfig for organization context

```bash
kubectl config current-context 2>/dev/null
kubectl config get-contexts 2>/dev/null
```

Note the organization name — use it in all messages so the administrator can confirm you're operating in the right account.

### 2. Probe for browser access

Attempt to list browser tabs silently. There are three outcomes:

1. **Connected** — browser tools are live. Tell the administrator:
   > "I can drive the Console for you — I'll pause before saving the policy and before sending any invitation. Want me to proceed?"
   If yes, read `references/browser-automation.md` and follow the patterns there. **This is the preferred path** — it's faster and less error-prone than manual navigation.

2. **Not connected, but tools exist** — browser tools are loaded but the extension isn't reachable. Tell the administrator:
   > "I have browser tools available but they're not connected to Chrome yet. I can walk you through a quick setup (takes about 2 minutes), or I can walk you through this manually instead. Which do you prefer?"
   If they want setup, use the `cw-console-browser-access` skill. After that, retry and proceed with browser automation.

3. **No browser tools** — no browser MCP in scope. Proceed with the manual walkthrough below.

### 3. Present findings

> "I see you're in the `<org-name>` organization (from kubeconfig). I'll walk you through adding users to this org.
>
> What I need from you:
> - Email addresses of the users to invite
> - What these users should be able to do (or their role — e.g., engineer, contractor, admin)"

**The Cloud Console is at `console.coreweave.com`** (not `cloud.coreweave.com`).

---

## Step 1 — Create a group

Groups are the right level of abstraction for permissions. You assign a policy to the group and manage membership separately. This way you never have to edit policies when someone joins or leaves.

### Via browser automation (preferred)

If browser tools are connected, navigate to and automate the group creation. See `references/browser-automation.md` for patterns. Pause before clicking Create to confirm the group name with the administrator.

### Manual walkthrough (fallback)

**Navigation:** Left sidebar → **Administration** → **Users and Groups** → **Groups** tab

1. Click **Create group**.
2. Give it a meaningful name (for example, `engineering`, `devops-team`, or `contractors-readonly`).
3. Leave membership empty for now — you'll add people in Step 4.
4. Click **Create**.

---

## Step 2 — Create a policy and attach the group

Policies connect groups to roles. You must create the policy before inviting users so the group already has permissions when new members join.

### Via browser automation (preferred)

If browser tools are connected, navigate to the Platform access tab and automate policy creation. Pause before clicking Save to confirm the policy details with the administrator.

### Manual walkthrough (fallback)

**Navigation:** Left sidebar → **Policies** → click the **Platform access** tab (the Policies page opens on the Object Storage access tab by default — click Platform access to switch) → **Create policy**

The create policy form has these fields in Rule 1:

- **Name** — give the policy a name that matches the group (for example, `engineering-access`).
- **Users** — leave this empty. Don't add individual users here.
- **Groups** — add the group you just created. Adding the group rather than individual users means you only need to manage group membership going forward — not the policy itself.
- **Roles** — assign the appropriate roles (see Step 3).

Click **Save policy**.

> **Checkpoint:** Before saving a policy that grants **IAM Admin**, confirm with the administrator. IAM Admin lets a user invite anyone to the organization and assign any permission, including to themselves.

After you save the policy, the group has permissions and is ready to receive members.

---

## Step 3 — Choose roles

If you haven't already selected roles while filling out the policy form, choose them now before saving. The roles you assign here determine what every member of this group can do.

Think about what people actually need. Practical starting points are listed below by persona.

For most engineers (Kubernetes + storage access, no administrator access):

- CKS Admin
- Object Storage Admin
- Access Token Admin
- Observability Viewer

For read-only / contractors:

- CKS Viewer
- IAM Viewer
- Access Token Viewer
- Observability Viewer
- Note: **no Object Storage Viewer role exists**. If contractors need to inspect object storage, either grant Object Storage Admin and restrict access at the bucket-policy level, or share data out-of-band.

For a team lead who also handles billing:

- Engineer set + Billing Viewer

To make someone a full administrator (rare — use carefully):

- IAM Admin + CKS Admin + Object Storage Admin + Access Token Admin + Access Request Approver

Not sure what a role does? Ask the administrator to clarify their intent, or consult `references/roles.md` for a plain-English breakdown of every role.

After you choose roles and save the policy, the group is fully configured. You can now invite users.

---

## Step 4 — Invite users into the group

Now that the group has a policy attached, you can invite users and they'll join the group immediately upon invitation.

### Via browser automation (preferred)

If browser tools are connected, navigate to the Users tab and automate the invitation process. **Pause before clicking Invite for each user** to confirm the email address is correct — invitations are sent immediately and create a pending account.

### Manual walkthrough (fallback)

**Navigation:** Left sidebar → **Administration** → **Users and Groups** → **Users** tab

1. Click **Invite user**.
2. Enter the user's email address.
3. In the **Groups** dropdown, select the group you created in Step 1.
4. Click **Invite**.
5. Repeat for each user.

> **Checkpoint:** Clicking **Invite** immediately creates a pending account for the specified address and sends an invitation email. The recipient must accept the invitation to activate their account. You must confirm the email address is correct before proceeding.

The invited user receives an email with a link to create their account. They can sign in with email/password, GitHub, or Gmail (if the email address matches).

---

## Common mistakes

**Inviting users before creating the group and policy**
A pending user (one who hasn't accepted their invitation yet) can't be assigned to a group. If this has already happened, wait for the user to accept the invitation, then add them to the group from the Users and Groups page — or cancel the invitation and re-invite after the group is set up.

**Adding individual users to a policy instead of a group**
This works but requires editing the policy every time someone joins or leaves. Using groups scales better.

**Granting IAM Admin broadly**
IAM Admin is equivalent to full administrative access. Reserve it for people who actually need to manage the organization's identity and access settings.

---

## References

The following resources support this workflow:

- `references/roles.md` — Plain-English descriptions of every CoreWeave IAM role, with examples of who should get each one.
- `references/browser-automation.md` — How to drive the Cloud Console with browser tools: permission model, element-finding tactics, verification after each step, and where to pause for administrator approval.
- CoreWeave docs: https://docs.coreweave.com/security/iam/access-policies
- Manage users: https://docs.coreweave.com/security/authn-authz/manage-users
