# Token scope policy (APPSEC-3961)

Skills in this repository walk customers through workflows that require a
CoreWeave Cloud API access token. That token is the product's single hard
credential, and it cannot be scoped. APPSEC-3961 (TM-002) tracks reducing
its blast radius before GA.

## What the Console actually offers

Verified in the live Console (`console.coreweave.com/tokens`, 2026-08-26),
cross-checked against
[Manage API access tokens](https://docs.coreweave.com/security/authn-authz/manage-api-access-tokens):

- The **Create API token** dialog has **three** fields — **Token name**,
  **Expiration**, **Comment**. There is no scope, role, permission, or
  per-resource selector. (The docs call the third field "Note"; the UI says
  "Comment.")
- **Expiration** is a dropdown with exactly six options: **1 hour**,
  **8 hours**, **One month**, **90 days**, **One year**, **Never**. It
  **defaults to One month**, and **Never** is offered — a non-expiring
  credential carrying the creator's full account authority.
- The token **inherits every permission its creating user holds**, across the
  whole organization, until it expires. A token minted by an org admin is an
  org-admin credential regardless of the workflow name it was created under.
- After creation you choose how to receive it (raw **Token Secret**, or a
  **Kubeconfig** with the token embedded as a static bearer token).

The defaults matter as much as the missing selector. A customer who accepts
what the dialog proposes gets a full-authority credential valid for a month;
the option set makes a year, or forever, one click away. Anecdotally this is
what happens in practice — the tokens on the maintainer's own dashboard while
verifying the above carried expirations in 2027.

There is therefore no such thing as a read-only CoreWeave API token, and no
such thing as a token limited to one workflow. Any skill content that tells a
customer to "pick a scope" is telling them to do something the Console does
not let them do.

## Policy

1. **Never imply a scope selector exists.** Skills state plainly that the
   token carries the creating user's full authority.
2. **Name the minimum instead.** Every workflow that mints a token declares
   the minimal IAM roles it needs, and the rendered token step names them.
   Because the token inherits its creating user's roles, this is the only
   lever that genuinely narrows the credential: a customer can mint it as a
   least-privilege user whose access policy grants only those roles rather
   than as an admin. The `cw-add-users` workflow creates exactly such an
   identity (group + Platform Access policy with chosen roles + invitation).
3. **Recommend a short expiry, and name a real option.** The recommendation is
   **8 hours** — the shortest option that comfortably covers a run (1 hour is
   offered but tight for cluster provisioning). Skills say explicitly that the
   dialog defaults to *One month* and that it must be changed, and that
   **Never** must not be chosen. *One month* and longer are called out as
   legitimate only for unattended CI and standing kubeconfigs.
4. **Delete after the run.** The token step ends by telling the customer to
   delete the token, with the one exception that matters: a token embedded in
   a kubeconfig they still need must survive until they're done with it.
5. **Never silently reuse a found token.** A token discovered in the
   environment gets an explicit authority warning and an offer to mint a fresh
   short-lived one, not a bare "is that the right one?".
6. **No broad scope without a recorded reason.** Enforced by the build.

## How it is implemented

Rendered into the `create-api-token` step (`_snippets/coreweave-platform.md`),
per workflow, from each `skill.yaml`:

| Param | Rendered | Purpose |
| --- | --- | --- |
| `TOKEN_ROLES` | yes | Minimal IAM roles this workflow needs, plus (where one exists) a narrower alternative — `cw-load-model-to-bucket` names an object-storage org policy granting only `cwobject:CreateAccessKey`, `cwobject:ListBucketInfo`, `s3:CreateBucket`, `s3:PutObject` instead of blanket Object Storage Admin. |
| `TOKEN_EXPIRY` | yes | Recommended expiration, constrained to options the dialog actually offers (currently `8 hours` everywhere). |
| `TOKEN_SCOPE` | **no** | Coarse `read-only` / `read-write`. Lint target only — see below. |

Declared per workflow today:

| Skill | `TOKEN_SCOPE` | Minimal roles named |
| --- | --- | --- |
| `cw-create-cluster` | read-write | CKS Admin + Access Token Admin |
| `cw-create-node-pool` | read-write | CKS Admin + Access Token Admin |
| `cw-self-managed-inference` | read-write | CKS Admin + Access Token Admin (Managed Auth maps CKS Admin to in-cluster `edit`; CKS Viewer maps to `view` and cannot create the namespace, secret, or Helm release) |
| `cw-load-model-to-bucket` | read-write | Object Storage Admin + Access Token Admin, or the four-action org policy above |
| `_example-skill-template` | read-only | CKS Viewer + Access Token Admin |

Build-time enforcement in `build.py`:

- `_validate_token_scope()` — a `TOKEN_SCOPE` other than `read-only` requires
  the top-level `token-scope-justification: "<reason>"`. Same audit-trail shape
  as `disallowed-tools-waived`: mandatory non-empty reason string, rejected
  inside `frontmatter:`, never emitted, and a build error both when it is
  missing and when it lingers after the scope narrows back to `read-only`.
  Invalid `TOKEN_SCOPE` values fail too.
- `_validate_include_params()` — any include param the target snippet never
  references fails the build. This is the backstop for the defect described
  below.

Reuse handling lives in `_snippets/shared-interview.md`'s CoreWeave
Authentication section: on detecting `$CW_API_TOKEN` / `$COREWEAVE_API_TOKEN`
it states that the token carries its creator's full org-wide authority and that
neither its scope nor its expiry is knowable from the variable, then offers a
fresh short-lived token as the recommended path while allowing reuse.

## The defect this corrected

`TOKEN_SCOPE` was **not** working coarse infrastructure, as an earlier revision
of this document claimed. Every skill that mints a token passed it, and the
`create-api-token` snippet body never referenced it. Jinja2 drops an
unreferenced param without complaint, so the value rendered nowhere: no
customer ever saw a scope recommendation, and the `read-write` declarations had
no effect on anything. It was a dead parameter that read like a control.

Two things changed as a result. `TOKEN_SCOPE` is now explicitly source-only
(with the rendered guidance carried by `TOKEN_ROLES` and `TOKEN_EXPIRY`), and
the build rejects unreferenced params outright — the class of bug, not just
this instance.

## Adjacent control: tool scoping (not a token-scope fix)

`build.py` emits each skill's `disallowed-tools` list into the generated
`SKILL.md`, and the Skill loader removes those tools from the model's pool
while the skill is active. Every skill must declare a non-empty list or
record a `disallowed-tools-waived: "<reason>"` waiver, so no skill ships
unrestricted without an audit trail.

`allowed-tools` is deliberately **not** emitted. In a `SKILL.md` the loader
reads it as a permission pre-approval — the listed tools are used without
prompting the customer, and unlisted tools stay callable — so propagating
`allowed-tools: [Bash, Read, Write]` would auto-approve arbitrary shell
execution for workflows that run `terraform apply` and mint API tokens,
weakening the Checkpoint confirmations rather than hardening them.

**This does not close TM-002.** Tool scoping and token scoping are different
controls on different assets. A skill that retains `Bash` — all of them do,
because these workflows are shell-driven — can still reach
`api.coreweave.com` with whatever token is in the environment, at that
token's full user scope.

## What is still open

**The primary control remains MISSING and is not ours to build.** TM-002 asks
for Console support for scoped/limited-purpose token types that skills default
to requesting. Everything above is defense-chain items 2–4 (mint fresh per run,
warn on reuse, short expiry) plus the honest substitute for item 1. None of it
prevents a leaked token from being replayed with the customer's full account
authority — it only reduces how often a broad token is minted, how long one
lives, and how likely a customer is to hand one over without knowing what it
carries. **APPSEC-3961 should stay open with a Console-team dependency
recorded.**

Also unresolved:

- **The Console's own defaults work against this policy.** Expiration defaults
  to *One month* and offers *Never*. Skills can tell a customer to change it;
  they cannot change what one click produces. A shorter default (and dropping
  *Never* for interactive users) is a Console-side ask worth filing alongside
  the scoped-token-type request.
- **No scope verification at runtime.** A skill still cannot check the
  authority of a token it was handed and refuse an over-scoped one; the API
  exposes no introspection endpoint for this that we've found. Closing this
  properly probably needs the same Console-side work as the primary control.
- **Least-privilege minting is advice, not a default.** The token step
  recommends minting as a dedicated least-privilege user, but nothing verifies
  the customer did — and for a single-user org it may not be practical at all.
- **`cw-add-users` is the recommended path to a least-privilege identity** but
  the two workflows are not wired together; the token step describes what that
  skill does rather than handing off to it.
