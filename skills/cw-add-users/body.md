
# Add users to CoreWeave (non-OIDC)

You are helping a CoreWeave Cloud Console administrator invite new users from their organization, set up appropriate permissions, and avoid a common gotcha that trips up first-timers.

**The correct order matters.** Read through the whole workflow before starting — **inviting users before creating the group and policy means you can't assign those users to the group until they accept the invitation**. Do the setup first, then send invitations.

---

## Before you start

Confirm the following before proceeding:

- The administrator has the **IAM Admin** role (or is the first administrator on the account).
- They know which email addresses to invite.
- They have a rough idea of what these users should be able to do. If not, offer to walk through the roles (see `references/roles.md`).

**The Cloud Console is at `console.coreweave.com`** (not `cloud.coreweave.com`). All the paths below assume the administrator is signed in there.

**Probe for browser access before asking.** Attempt `tabs_context_mcp` (or your environment's equivalent) silently. There are three outcomes:

1. **Connected** — browser tools are live. Tell the administrator:
   > "I can drive the Console for you — I'll pause before saving the policy and before sending any invitation. Want me to proceed?"
   If yes, read `references/browser-automation.md` and follow the patterns there.

2. **Not connected, but tools exist** — browser tools are loaded but the extension isn't reachable. Tell the administrator:
   > "I have browser tools available but they're not connected to Chrome yet. I can walk you through a quick setup (takes about 2 minutes), or I can walk you through this manually instead. Which do you prefer?"
   If they want setup, use the `cw-console-browser-access` skill. After that, retry and proceed with browser automation.

3. **No browser tools** — no browser MCP in scope. Skip the offer and go straight to the manual walkthrough below.

---

## Step 1 — Create a group

Groups are the right level of abstraction for permissions. You assign a policy to the group and manage membership separately. This way you never have to edit policies when someone joins or leaves.

**Navigation:** Left sidebar → **Administration** → **Users and Groups** → **Groups** tab

1. Click **Create group**.
2. Give it a meaningful name (for example, `engineering`, `devops-team`, or `contractors-readonly`).
3. Leave membership empty for now — you'll add people in Step 4.
4. Click **Create**.

---

## Step 2 — Create a policy and attach the group

Policies connect groups to roles. You must create the policy before inviting users so the group already has permissions when new members join.

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

**❌ Inviting users before creating the group and policy**
A pending user (one who hasn't accepted their invitation yet) can't be assigned to a group. If this has already happened, wait for the user to accept the invitation, then add them to the group from the Users and Groups page — or cancel the invitation and re-invite after the group is set up.

**❌ Adding individual users to a policy instead of a group**
This works but requires editing the policy every time someone joins or leaves. Using groups scales better.

**❌ Granting IAM Admin broadly**
IAM Admin is equivalent to full administrative access. Reserve it for people who actually need to manage the organization's identity and access settings.

---

## References

The following resources support this workflow:

- `references/roles.md` — Plain-English descriptions of every CoreWeave IAM role, with examples of who should get each one.
- `references/browser-automation.md` — How to drive the Cloud Console with browser tools: permission model, element-finding tactics, verification after each step, and where to pause for administrator approval.
- CoreWeave docs: https://docs.coreweave.com/security/iam/access-policies
- Manage users: https://docs.coreweave.com/security/authn-authz/manage-users
