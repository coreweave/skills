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
   `Checkpoint:`. Every markdown emphasis form counts, so
   `__Checkpoint:__`, `*Checkpoint:*`, `_Checkpoint:_`,
   `***Checkpoint:***` and `___Checkpoint:___` are near-misses too —
   covering only `**…**` would let the others ship as inert
   prose that reads like a gate. Near-misses are errors, not warnings, so
   the contract cannot drift silently. The corollary: write the word
   "checkpoint" **unemphasized** in ordinary prose, since an emphasized one
   is indistinguishable from a marker that drifted.

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
- **Only fenced code blocks are scanned.** Inline `code` in prose is
  narrative, not a runnable block. Fence lines whose first non-space
  character is `#` are comments, not invocations. Commands are matched
  anywhere in the line and may carry up to three tokens between the binary
  and its subcommand, so wrapper prefixes (`cwrun aws s3api create-bucket`)
  and global flags (`terraform -chdir=x apply`, `helm -n ns install`)
  still match.
- **The fence tracker is one-directional, not CommonMark-complete.** It is
  a line-at-a-time tracker, not a block parser, so it cannot be
  *equivalent* to CommonMark. What it is built to guarantee is one
  direction of the disagreement:

  > every line CommonMark treats as fenced-code content is a line the
  > tracker treats as code

  and nothing stronger. That is the direction with teeth. Where CommonMark
  says "code" and the tracker says "prose", a destructive command is never
  scanned and a `> **Checkpoint:**` shown as a fenced *example* starts
  counting as a real gate — both silent. The opposite slack (the tracker
  scanning something CommonMark renders as prose) fails the build loudly
  instead, so it is accepted wherever the two cannot be reconciled.

  Concretely the tracker records the open fence's delimiter character, run
  length, block-quote depth, and indentation on both sides of any quote
  prefix, and then:
  - Both `` ``` `` and `~~~` open a fence. A tilde-fenced block used to be
    invisible to the scan entirely.
  - A closer must use the **same** character and block-quote depth, run at
    least as long, and carry nothing but whitespace — so an info-stringed
    ```` ```bash ```` line inside an open block is content, not a closer,
    and a `> ``` ` line inside a plain block is literal code.
  - The three-space indentation allowance is relative to the enclosing
    block container, not the document margin, so an opening fence's
    absolute indentation is **unbounded** — this repo already emits
    four-space list-contained fences. Openers are accepted at any
    indentation, and a delimiter closes only when it closes under *every*
    container indentation still consistent with its opener.
  - A delimiter that has definitely left its container does not merely
    close the fence: CommonMark ends the container, which closes the
    fence, and then the same line opens a fresh block at the outer level.
    So a column-0 ```` ``` ```` after an unclosed list-contained fence
    leaves the following lines inside code, and the tracker keeps scanning
    them.
  - Where the container indentation genuinely cannot be pinned down —
    a delimiter dedented relative to its opener, a tab whose width depends
    on the container column, a fence nested more than two containers deep
    — the tracker stops guessing and reports the rest of the document as
    code. A body that reaches that state fails the build as soon as it
    contains anything command-shaped, which is the signal to rewrite the
    fence unambiguously.

  This is *measured*, not asserted.
  `tests/test_fence_tracker_commonmark.py` adjudicates the tracker against
  markdown-it-py in CommonMark mode over ~15.5k generated opener/closer
  combinations, 40k seeded fuzz documents, and every committed body, and
  fails on any under-scan. `tests/test_checkpoint_validator.py` pins one
  fixture per bypass shape found in review, in both directions: a
  delimiter that must *not* close its block, and one that must.

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
- **Indentation-only code blocks are not scanned.** CommonMark also makes
  a four-space-indented run of lines a code block, with no delimiter at
  all. The tracker only follows fences, so a destructive command written
  that way is invisible to the scan. No committed body uses the form —
  every code block in `dist/` is fenced — but nothing stops one from being
  added, which is why it is written down here.
- **Marker hygiene is scanned in prose only, so the over-scan slack costs
  it something.** In a document where the tracker has resolved an
  indentation ambiguity by treating the remainder as code, a near-miss
  marker after that point is not reported. Missing *gates* still fail the
  build loudly in the same situation; only the hygiene check goes quiet.
- **Emphasis forms are matched by delimiter run, up to three.**
  `*Checkpoint:*`, `**Checkpoint:**` and `***Checkpoint:***` and their
  underscore equivalents are all near-misses. A marker wearing four or
  more delimiters, or HTML (`<strong>Checkpoint:</strong>`), is not
  matched.

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
