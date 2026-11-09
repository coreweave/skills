# Cost gates

Shared text for the denial-of-wallet controls required by APPSEC-3972
(TM-013). The ticket asks for a size-scaled second confirmation above a
**configurable** threshold, so the threshold values live here, in one place,
and every workflow gate renders them from this snippet — the same reason the
reference-architecture commit SHA lives once inside `fetch-pinned-ref-arch`.

**To retune the policy, edit the numbers below and re-run `python build.py`.**
Do not restate them in a `body.md`: skill bodies are never run through Jinja2
(only snippet bodies are), so a `{{ PARAM }}` written there is inert, and a
hand-typed number is a copy that will drift.

## Current thresholds

More than **8 GPUs total**, or more than **2** nodes/replicas. Either clause
alone escalates. The two cover different shapes of request and both are
needed:

- CoreWeave's common GPU SKUs are whole-node `8x` types (`gd-8xh100ib-i128`,
  `gd-8xl40`) and bill whole. The GPU clause sits at one node's worth, so a
  single `8x` node clears on a plain confirmation while two or more — 16+
  GPUs — must be re-stated. A lower bound would fire on every `8x` request,
  and a confirmation that always fires decays into ritual rather than a
  control (the TM-004 rubber-stamping failure this gate exists to resist).
- Single-GPU types also exist: `gd-1xgh200` is the only single-GPU type on
  offer in US-EAST-04A and a reasonable pick for a small model. A GPU-count
  bound alone would let a wide pool of those through — eight of them is 8
  GPUs and would not trip the GPU clause. The node clause catches it: three
  or more nodes escalates regardless of GPUs per node.

Size is measured against each autoscaling pool's **maximum**, not its initial
target. `nodepool_autoscaling` / `nodepool_max_nodes` are supported in both
CKS skills, and the ceiling is what the customer can be billed for without
passing this gate again — so the ceiling is what the gate must weigh.

## Two thresholds are intentionally NOT here

- The **50 GB** model-size gate in `cw-load-model-to-bucket` — it appears
  exactly once, in that skill's bucket-creation checkpoint, and governs
  storage rather than compute. It is already single-source; extracting it
  would add indirection without removing a copy.
- `cw-create-node-pool/references/nodepool-reference.md` cites this rule but
  cannot render it: a skill's `references/` directory is copied into `dist/`
  verbatim, never templated. It points at its workflow's checkpoint instead of
  restating the numbers.

<!-- snippet:size-scaled-confirmation -->
> 3. **Fresh, size-scaled confirmation.** The {{ ACTION }} proceeds only on a fresh customer reply to this gate message (the one carrying the context and cost lines) — {{ PRIOR_CONSENT }} does not count. Size the request from what you just showed: total GPUs and total {{ UNIT }}, counting any autoscaling pool at its **maximum**, not its initial target — the ceiling is what can be billed without passing this gate again. If either figure is large — more than **8 GPUs total** or more than **2 {{ UNIT }}** — a bare "yes" is not enough: ask the customer to reply with the quantity **you computed**, in the shape of "{{ EXAMPLE_REPLY }}" but carrying the real numbers, never the example's. Then check the reply against your own figure and **treat any mismatch as a refusal** — a bare "yes", a different count, or a quantity you cannot reconcile means do not {{ ACTION }}: re-state the real figure and ask again. At or below both thresholds, a plain fresh "yes" is fine.
<!-- /snippet:size-scaled-confirmation -->
