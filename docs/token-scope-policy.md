# Token scope policy (APPSEC-3961)

Skills in this repository walk customers through workflows that require a
CoreWeave Cloud API access token. Because API access tokens inherit the
permissions of the user who creates them, a skill that asks for "a token"
without qualification steers customers toward full-scope, long-lived
credentials sitting in an agent's environment. APPSEC-3961 tracks closing
that gap before GA.

## Policy

Skills must instruct customers to mint **minimally-scoped** API tokens for
the workflow at hand rather than full-scope tokens. A skill that needs
read-only access must say so; a skill that writes must ask for no more than
the write scope it uses; no skill should present an unqualified
"create a token" step.

## Current state

- Skills reference the shared `create-api-token` snippet
  (`_snippets/coreweave-platform.md`), which links to the CoreWeave token
  documentation
  ([Manage API access tokens](https://docs.coreweave.com/security/authn-authz/manage-api-access-tokens))
  and takes a `TOKEN_SCOPE` parameter (for example `read-only` or
  `read-write`) supplied by each workflow's manifest. The snippet warns that
  the token inherits the creating user's permissions, but scope selection
  guidance is otherwise delegated to the linked docs.
- Tokens are stored in the customer's secret store (the snippet takes a
  `SECRET_STORE_HINT` parameter); skills instruct customers never to echo
  token secrets.

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
token's full user scope. The work below is what actually addresses this
ticket, and it depends on a Console-side capability that does not yet exist
(the threat model records the primary control as MISSING).

## Open design questions

- **Scoped-token guidance per skill.** Should each workflow's manifest
  declare the exact token scope it needs (beyond today's coarse
  `TOKEN_SCOPE` param), so the rendered token step can name the minimal
  scope explicitly and the build can lint that no skill defaults to
  read-write without justification?
- **Expiry recommendations.** The token creation step surfaces the
  Expiration field but does not recommend a value. Should skills recommend
  a short default expiry (for example, the duration of the workflow) and
  call out when a longer-lived token is genuinely required (CI, standing
  kubeconfigs)?
- **Verification.** Can a skill check the scope of the token it was handed
  (and refuse or warn on over-scoped credentials), rather than relying on
  the customer to have minted the right one?
