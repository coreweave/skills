# Scratch testbed for the security-hardening PRs

> ## THIS DIRECTORY MUST NEVER REACH `main`
>
> Every file here is a **deliberate planted violation**. That is the
> point: the hygiene gate is a control whose failure mode is silence, and
> the only way to know it still works is to hand it something it must
> catch. Delete `_scratch/` before any PR that touches it merges.

A place to prove behaviour end-to-end, rather than reasoning about it —
for [APPSEC-3971]'s hygiene gate today, and whatever the rest of the
security PRs turn up next.

## Ground rules

1. **Never a real credential.** Every planted value here is either a
   published documentation constant or obviously synthetic:

   | Kind | Value used | Why it is safe |
   | --- | --- | --- |
   | Email | `@example.com` | RFC 2606 reserves it; it can never be a real domain |
   | IP | `203.0.113.x` | RFC 5737 TEST-NET-3, reserved for documentation |
   | Ticket | `ACME-4471` | Not a CoreWeave project key |
   | Org URL | a made-up org slug | Not an account that exists |
   | UUID | randomly typed | Not an identifier for anything |

   If you need a *credential* shape, generate it locally — see
   "Testing the blocking tier" below. Do not commit one, ever, even a
   fake one: the point of the blocking tier is that those shapes are
   never a coincidence, and a committed fake trains people to ignore a
   real one.

2. **Nothing committed here may block a merge.** The files below plant
   only *warn-tier* findings on purpose. A committed credential shape
   would red-gate the very PR carrying the fix.

3. **Delete, don't allowlist.** If something here starts failing in a
   way you did not intend, that is a finding about the gate. Do not add
   an allowlist entry to quiet it — allowlist entries are permanent
   holes and this directory is temporary.

## What is here

| File | Exercises |
| --- | --- |
| `hygiene-cases.md` | Warn-tier identifier rules in ordinary prose (non-corpus) |
| `../evals/_scratch-paste-cases.jsonl` | Paste-residue rules, which run **only** over the corpora |

The split is deliberate: `_scratch/` is not a corpus path, so
`CORPUS_ONLY_RULES` do not apply to it. The paste fixture has to live
under `evals/` to be seen at all. If you move it, it silently stops
testing anything — which is itself the kind of thing worth checking.

## Running the gate against it

```bash
python3 evals/check_eval_hygiene.py                  # whole repo, warns
python3 evals/check_eval_hygiene.py _scratch/        # just this directory
python3 evals/check_eval_hygiene.py --strict         # everything blocks
python3 evals/check_eval_hygiene.py --format json    # what CI feeds the poster
```

Expect a **non-zero finding count and exit code 0**: warn-tier findings
report without blocking. If it exits 1, either something here is a
credential shape (fix that) or the tiering has regressed (a real bug).

## Testing the review-thread poster

This is the piece that had never actually run against the API — the repo
is clean, so in CI it had only ever printed "nothing to post". To
exercise it for real, open a **draft** PR from a branch containing this
directory and let the workflow post to it. Then check three things:

1. Each finding on a line **inside the diff** appears as its own inline
   review thread.
2. Findings in files the PR does not touch land in the single summary
   comment instead — GitHub cannot anchor a review comment off-diff.
3. **Push again without changing anything.** No new threads should
   appear. If they duplicate, the marker written by `body_for` and the
   one read back by `existing_markers` have diverged.

Then resolve a thread and push once more: it must stay resolved and not
come back.

## Testing the blocking tier

Generate a credential shape locally instead of committing one. Split so
this README does not itself carry the literal:

```bash
printf 'AKIA%s\n' 'IOSFODNN7EXAMPLE' > _scratch/local-only-block-test.md
python3 evals/check_eval_hygiene.py _scratch/   # must exit 1
rm _scratch/local-only-block-test.md
```

That value is AWS's own published example key ID. It is not a
credential, but it has the shape, which is all the rule matches on.

## Testing the PR-text pass

```bash
printf 'repro for ACME-4471\nping ops@example.com\n' \
  | python3 evals/check_eval_hygiene.py --stdin --label "PR body"
```

Exits 1 — a PR body is a gate, because the author can edit it green.
Add `--warn-only` for the comment path, which is an alarm rather than a
gate: a comment was public the moment it posted, so a check that can
never go green would just teach people to ignore it.

[APPSEC-3971]: the tracking ticket; this file is scanned, so it cannot
spell the ID out.
