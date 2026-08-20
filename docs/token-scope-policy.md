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

## Shipped first step: allowed-tools re-enablement

As of this change, `build.py` propagates each skill's `allowed-tools`
declaration into the generated `SKILL.md` frontmatter, and the Skill loader
enforces it at runtime. This bounds what a skill can do with whatever
credentials are in the environment: a skill declaring `Bash, Read` cannot
invoke other tools even if a broadly-scoped token is available.

Per-skill opt-out: a manifest may set the top-level key
`allowed-tools-unrestricted: "<reason>"` when its workflow needs tools that
cannot be statically enumerated (environment-provided browser tools, for
example). The reason string is mandatory and audited in review; the key is
never emitted. `cw-create-cluster` is the only opted-out skill today (its
quota check drives the CoreWeave Console via browser automation — see
`skills/cw-create-cluster/references/quota-check.md`).

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
