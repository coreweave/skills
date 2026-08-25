# Trigger-eval corpus notes

Authoring notes for the 2026-08 corpus growth (26 -> 208 entries). The
schema, labeling rules, and runner are documented in `README.md`; this
file only records composition decisions so future additions stay
consistent.

## Composition

- Entries 1-26 are the original corpus (including the five
  `expected_chain` cases) and were left byte-for-byte unchanged.
- 121 new positives, grouped in blocks: 25 for `cw-create-cluster` and
  24 each for `cw-create-node-pool`, `cw-self-managed-inference`,
  `cw-load-model-to-bucket`, and `verify-coreweave-workload-health`.
  Each block spans terse, verbose/rambling, misspelled/lowercase,
  jargon, plain-English/indirect, and boundary phrasings that separate
  the skill from its nearest neighbor (node pool vs cluster, bucket vs
  inference, verify vs deploy).
- 61 new negatives (`expected_skill: null`): bare-credential asks
  (API token / kubeconfig — see below), adjacent-but-out-of-scope infra
  (delete/resize/upgrade/billing/quota), other clouds (GKE/EKS/AKS/GCS),
  generic Kubernetes debugging, non-CoreWeave Terraform, general
  programming, and off-topic chit-chat.

## Label rules applied

- **No `create-api-token` or `get-coreweave-kubeconfig` positives.**
  Neither is installable as a skill a customer can trigger by name. The
  API-token content is the `create-api-token` snippet in
  `_snippets/coreweave-platform.md` (the name `evals/README.md` uses);
  it has no `standalone-skills.yaml` entry, so it is only ever inlined
  and never reaches `dist/`. `get-coreweave-kubeconfig` is the
  standalone emitted from the `generate-kubeconfig` snippet and is
  include-only per `standalone-skills.yaml` — built into `dist/`, shipped
  in no plugin. Under the no-broader-skill rule in `README.md`, a query
  that mentions the token or the kubeconfig alone is labeled `null` —
  these queries are deliberately present as hard negatives.
- New entries use exactly the two keys `{"query", "expected_skill"}`;
  only the pre-existing chain cases carry `expected_chain`.

### Bare-credential nulls added under the no-broader-skill rule

Eleven new queries mention the API token or kubeconfig alone and are
labeled `null` per the rule (scampbell, 2026-08-03) in `README.md`:

- "make me a coreweave api token"
- "rotate my coreweave api token"
- "where in the cloud console do I generate an api access token?"
- "I lost my api token, how do I create a new one?"
- "kubeconfig for my cluster pls"
- "how do I point kubectl at my coreweave cluster?"
- "my kubeconfig has multiple contexts - which one is the CKS cluster?"
- "kubectl can't reach my cluster, I think my kubeconfig is stale"
- "download a kubeconfig from the cloud console"
- "can I create a coreweave api token from the CLI?"
- "I just need an api key for coreweave, nothing else"

### A credential query the rule does NOT cover

"what environment variable does the coreweave terraform provider read
the api token from?" is labeled `cw-create-cluster`, not `null`. The
rule forces `null` only when the credential is mentioned *alone*; here
the customer has signalled the larger job the credential is for —
running CoreWeave Terraform, which `cw-create-cluster`'s description
explicitly claims ("wants help with CoreWeave Terraform") and whose
body answers this exact question. It doubles as a hard positive: a
naive router pattern-matches "api token" and picks nothing.

## Sanitization

All new entries are synthetic. No customer, company, or personal names;
no emails, ticket IDs, IPs, account/org IDs, or real cluster names —
placeholders like "my cluster" / "the staging cluster" only. Zone names
(`US-EAST-04A`) and instance types (`gd-8xh100ib-i128`) appear in public
CoreWeave docs and are allowed.
