# Security

## Threat model (in brief)

The skills in this repository are instructions for an AI agent that operates
on **live customer infrastructure** — creating CKS clusters and node pools,
deploying Helm releases, and creating object-storage buckets on the
customer's account. A skill body is therefore not just documentation: every
command in a fenced code block is something the agent may execute with the
customer's credentials. The risks this repo's build controls address are:

- a skill instructing the agent to run a destructive command without first
  obtaining the customer's explicit confirmation, and
- the confirmation gates that do exist silently rotting as bodies are edited
  (a marker typo turns a gate into inert prose that still *looks* gated).

## The Checkpoint contract

A human-confirmation gate is a blockquote line that begins with the literal
marker:

```markdown
> **Checkpoint:** <what to show the customer and what to confirm>
```

The marker is **exact** — `> **Checkpoint:**`, canonical case, colon inside
the bold, opening a blockquote line (up to three leading spaces, per
CommonMark). Surrounding wording may change freely; the marker may not.
These gates are customer-facing: several bodies deliberately pair a
Checkpoint with `-auto-approve`/non-interactive flags, because the
Checkpoint *replaces* the tool's own interactive prompt. Do not remove
those flags — and do not rely on them either: the Checkpoint is the gate.

### What the build enforces (`validate_rendered_bodies` in `build.py`)

After rendering, the build scans every emitted `dist/<name>/SKILL.md`
(workflow skills and standalones; the `plugins/` trees are byte-for-byte
mirrors of `dist/`, so they are covered) and **fails with a non-zero exit**
when:

1. **An ungated destructive command exists.** A line in a fenced code block
   matching one of the enforced command classes —

   - `terraform apply`
   - `helm install`
   - `helm upgrade`
   - `aws s3api create-bucket`

   — with no `> **Checkpoint:**` line **anywhere earlier in the same
   document**, and no remaining `CHECKPOINT_BASELINE` allowance (see
   below).

2. **A near-miss marker exists.** A line outside fenced code that looks
   like an attempted Checkpoint but isn't the canonical marker — e.g.
   `> **Checkpoint**:` (colon outside the bold), `**Checkpoint:**` without
   the blockquote, wrong case, or a blockquote opening with a bare
   `Checkpoint:`. Near-misses are errors, not warnings, so the contract
   cannot drift silently.

3. **A baseline entry goes stale.** `CHECKPOINT_BASELINE` in `build.py`
   grandfathers the pre-existing ungated occurrences listed under
   "Documented follow-ups" below. It is a ratchet: new ungated occurrences
   fail the build, and when a grandfathered occurrence is gated, changed,
   or removed, the build fails until its baseline entry is deleted or
   decremented. The list can only shrink. Do **not** add entries to ship
   new ungated commands — add a Checkpoint instead.

Failure output names the file, line, and command, and points back here.

### Design decisions

- **Precedence scope is "anywhere earlier in the document", not "same
  markdown section".** The `cw-self-managed-inference` deploy gate
  legitimately spans a section boundary: the Checkpoint closes the
  values-file step ("get confirmation before deploying") and the
  `helm install` it gates opens the next step. A same-section rule would
  reject that correctly-gated body. Anywhere-earlier is the strongest
  scope every currently-gated occurrence satisfies without body edits.
  Tightening the scope (e.g. "since the previous destructive command" or
  per-section) is future work that requires body changes.
- **Only fenced code blocks are scanned** (including fences nested inside
  blockquotes; per CommonMark, a closing fence is backticks-only, so an
  info-stringed ```` ```bash ```` line inside an open block is content and
  does not flip the tracker). Inline `code` in prose is narrative, not a
  runnable block. Fence lines whose first non-space character is `#` are
  comments, not invocations. Commands are matched anywhere in the line
  and may carry up to three tokens between the binary and its subcommand,
  so wrapper prefixes (`cwrun aws s3api create-bucket`) and global flags
  (`terraform -chdir=x apply`, `helm -n ns install`) still match.

### Limits — what this control does *not* do

- **It is a build-time control on advisory prose.** At runtime the agent
  can still ignore a Checkpoint: validation ensures gates **exist** in the
  shipped skill text, not that they are **obeyed**. Runtime enforcement
  (e.g. a harness-level confirmation hook keyed on the marker) is future
  work under APPSEC-3963.
- The anywhere-earlier scope means one early Checkpoint satisfies the rule
  for every later command in that document. Current bodies use per-step
  checkpoints in practice, but the validator does not require that.
- The command list is a deny-list of known-destructive classes, not an
  allow-list of safe ones. A destructive command outside the list is not
  caught, and neither is one obscured beyond the matcher's reach: more
  than three tokens between binary and subcommand, the binary and
  subcommand split across `\`-continuation lines, or invocation through a
  variable, alias, or script indirection.

## Documented follow-ups (require body changes)

These were found ungated in the current rendered bodies. Per the rule
"include a command class only if all current occurrences already pass",
they are either excluded from enforcement or grandfathered via
`CHECKPOINT_BASELINE` until the bodies gain gates:

| Command class | Status | Ungated occurrences today |
| --- | --- | --- |
| `kubectl apply` | **Not enforced.** | `cw-self-managed-inference` (CPU NodePool manifest; model-cache PVC) — both precede that document's only Checkpoint. |
| `terraform destroy` | **Not enforced.** | `cw-create-node-pool` (the cheap quota-probe destroy of an empty pool) — precedes both of that document's Checkpoints. |
| `helm install` / `helm upgrade` | **Enforced, with a ratchet baseline.** | Four cluster-dependency installs in `cw-self-managed-inference` (cert-manager ×2, cert-issuers upgrade, Traefik) precede that document's only Checkpoint and are grandfathered in `CHECKPOINT_BASELINE`. All other helm occurrences — notably the deploy-step `helm install inference ./` — are enforced. |

Closing any row means editing the body to add a `> **Checkpoint:**` gate
(a content decision for the skill's owners), then enabling the class in
`DESTRUCTIVE_COMMAND_RE` / deleting the baseline entries in `build.py`.

## Reporting

Security issues with the pipeline or the shipped skills: file an APPSEC
Jira ticket (this control landed under APPSEC-3963) or contact the
CoreWeave application-security team.
