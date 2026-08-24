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

Current thresholds — more than **8 GPUs total**, or more than **2**
nodes/replicas. The GPU clause is set at one whole node deliberately:
CoreWeave GPU SKUs are `8x`, so a lower bound fires on every GPU request a
customer can make, and a confirmation that always fires becomes ritual rather
than a control (the TM-004 rubber-stamping failure this gate exists to
resist).

Two thresholds are intentionally NOT here:

- The **50 GB** model-size gate in `cw-load-model-to-bucket` — it appears
  exactly once, in that skill's bucket-creation checkpoint, and governs
  storage rather than compute. It is already single-source; extracting it
  would add indirection without removing a copy.
- `cw-create-node-pool/references/nodepool-reference.md` cites this rule but
  cannot render it: a skill's `references/` directory is copied into `dist/`
  verbatim, never templated. It points at its workflow's checkpoint instead of
  restating the numbers.

<!-- snippet:size-scaled-confirmation -->
> 3. **Fresh, size-scaled confirmation.** The {{ ACTION }} proceeds only on a fresh customer reply to this gate message (the one carrying the context and cost lines) — {{ PRIOR_CONSENT }} does not count. If the request is large — more than **8 GPUs total** or more than **2 {{ UNIT }}** — a bare "yes" is not enough: end the gate message by requesting the reply format, e.g. "to proceed, reply with the quantity: {{ EXAMPLE_REPLY }}", so one compliant reply satisfies the gate. At or below those thresholds, a plain fresh "yes" is fine.
<!-- /snippet:size-scaled-confirmation -->
