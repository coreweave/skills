# Security

## Reporting a vulnerability

Report suspected vulnerabilities in this pipeline or in the shipped skills
privately, through GitHub's private vulnerability reporting on this
repository ("Security" → "Report a vulnerability"). Please do not open a
public issue or pull request for a security problem.

CoreWeave staff: file a ticket in the APPSEC Jira project instead.

## Why this repository has a security posture at all

The skills here are instructions for an AI agent operating on **live
customer infrastructure** — creating CKS clusters and node pools, deploying
Helm releases, creating object-storage buckets on the customer's account. A
skill body is not just documentation: every command in a fenced code block
is something an agent may execute with the customer's credentials.

Two risks follow, and the build has a control for each:

- a skill instructing the agent to run a destructive command without first
  getting the customer's explicit confirmation;
- an existing confirmation gate rotting as bodies are edited, so a marker
  typo leaves inert prose that still *looks* like a gate.

## The Checkpoint contract

A human-confirmation gate is a blockquote line opening with the literal
marker:

```markdown
> **Checkpoint:** <what to show the customer, and what to confirm>
```

The marker is **exact**: that case, colon inside the bold, opening a
blockquote line. The wording after it may change freely; the marker may
not.

Several bodies deliberately pair a Checkpoint with `-auto-approve` or
another non-interactive flag, because the Checkpoint replaces the tool's own
prompt. Do not remove those flags, and do not rely on them either — the
Checkpoint is the gate.

## What the build enforces

`build.py` phase 6 (`validate_rendered_bodies`) scans every emitted
`dist/<name>/SKILL.md` after rendering — workflow skills and standalones;
the `plugins/` trees are byte-for-byte mirrors — and exits non-zero, naming
the file, line, and command, when:

1. **A destructive command is ungated.** A fenced-code line invoking
   `terraform apply`, `helm install`, `helm upgrade`, or
   `aws s3api create-bucket` with no `> **Checkpoint:**` line in scope. A
   gate reaches its own markdown section and the next one — far enough that
   a gate closing one step may open the next, close enough that a
   Checkpoint on page one cannot vouch for an appendix. One gate still
   covers a whole multi-block step: requiring a confirmation per command
   would train exactly the click-through habit this control exists to
   prevent.
2. **A marker is a near-miss.** Something checkpoint-shaped outside fenced
   code that is not the canonical marker — wrong case, colon outside the
   bold, no blockquote, a different emphasis form. These are errors, not
   warnings, so the contract cannot drift silently. Corollary for authors:
   write the word "checkpoint" unemphasized in ordinary prose.
3. **A ratchet entry is stale.** `CHECKPOINT_BASELINE` in `build.py`
   grandfathers ungated occurrences that predate this control. It only
   shrinks: a new ungated occurrence fails the build, and once a
   grandfathered one is gated, changed, or removed, the build fails until
   its entry is deleted. Never add an entry to ship a new ungated
   command — add a Checkpoint instead.

Rationale for the design decisions, and the derivation of the
fenced-code-block tracker the scan depends on, are documented where they
have to be maintained: the `validate_rendered_bodies` section comment and
`_classify_block_lines` docstring in `build.py`. The tracker's agreement
with CommonMark is adjudicated against a real parser in
`tests/test_fence_tracker_commonmark.py`; each bypass shape closed in
review has a fixture in `tests/test_checkpoint_validator.py`.

## What it does not do

This is a build-time check that gates **exist** in the shipped text. It is
not runtime enforcement, and it is not a complete inventory of destructive
behavior:

- Nothing here makes an agent obey a Checkpoint. A customer who approves
  without reading, or an agent configured to auto-approve, is unaffected by
  it. Runtime enforcement is open work under APPSEC-3963.
- The command list is a deny-list of four known-destructive classes, not an
  allow-list of safe ones. `kubectl apply` and `terraform destroy` are
  among the classes **not** enforced today.
- The scan sees fenced code blocks and literal command text. A destructive
  action reached indirectly is not matched.
- The scan reads each skill's `SKILL.md`. A destructive command in a
  `references/` file is not scanned; none contain one today.

**A green build is therefore not evidence that your destructive commands
are gated.** It rules out four specific ways of getting it wrong. Review is
what covers the rest.
