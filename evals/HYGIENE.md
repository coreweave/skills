# Eval corpus hygiene scanner

`check_eval_hygiene.py` is the blocking CI check behind the sanitization
mandate in [README.md](README.md): everything committed under `evals/`
ships to customers, so it must contain no customer identifiers, PII, or
credentials. The scanner is deterministic — stdlib-only regexes and hash
comparisons, no network, no LLM — so a pass or fail is reproducible on
any machine. (The tracking ticket ID lives in the script's docstring;
this doc can't cite it because this doc is itself scanned, and internal
ticket IDs are one of the things the scanner bans.)

CI runs it via `.github/workflows/eval-hygiene.yml` on every PR,
alongside a gitleaks sweep of the same directory.

## Running it locally

```bash
python3 evals/check_eval_hygiene.py            # scan evals/ (the default)
python3 evals/check_eval_hygiene.py some/dir   # scan another tree
```

Exit codes: `0` clean, `1` findings, `2` configuration error (bad
allowlist regex or malformed denylist entry).

It scans `*.jsonl`, `*.json`, and `*.md` files. Its own config sidecars
(`hygiene-allowlist.txt`, `hygiene-denylist.sha256`) are exempt.

## What it checks

| Rule | Catches |
| --- | --- |
| `email-address` | Any email address |
| `internal-domain-handle` | Any `@coreweave` / `@wandb` dot-com handle, even without a local part |
| `anthropic-api-key` | Keys starting `sk-ant-` |
| `aws-access-key-id` | `AKIA` + 16 uppercase alphanumerics |
| `github-token` | `ghp_` / `gho_` / `ghu_` / `ghs_` / `ghr_` tokens |
| `slack-token` | `xoxb-`-style tokens (all `xox?-` families) |
| `jwt` | `eyJ`-prefixed dotted base64url triples |
| `pem-header` | `-----BEGIN ... KEY-----` style PEM headers |
| `ipv4-address` | Valid dotted-quad IPs |
| `ticket-id` | Jira-style IDs: an uppercase project key, a hyphen, and an issue number |
| `uuid` | UUID-shaped identifiers |
| `console-url-with-org-id` | CoreWeave console/cloud URLs with an org, account, or tenant ID in the path or query string |
| `customer-denylist` | Tokens whose SHA-256 hash appears in `hygiene-denylist.sha256` |

Findings never echo the full matched value: pattern matches are redacted
to a short prefix, and denylist hits print only a hash prefix.

### Known-benign shapes it must not flag

The corpus legitimately contains CoreWeave availability-zone names (like
`US-EAST-04A`) and instance types (like `gd-8xh100ib-i128`). The
`ticket-id` rule only matches standalone `KEY-<number>` tokens — nothing
preceded or followed by another hyphen-joined segment — so zone names and
instance types pass. `hygiene-allowlist.txt` additionally carries an
explicit zone-name pattern as defense in depth. If you add a new benign
identifier family and it trips the scanner, extend the allowlist (below)
rather than weakening a rule.

## How to fix a hit

1. **Prefer rewriting the entry.** The eval only needs the *phrasing
   pattern*, never the real identifier. Replace the flagged value with a
   generic placeholder in the register the query uses: "my org",
   "the staging cluster", `[USER]@[DOMAIN]`, `[PROJECT]-[NUMBER]`,
   `10.0.0.[N]`. If the query can't survive sanitization, write a
   synthetic equivalent (see README.md, "Sanitize before committing").
2. **Never "fix" a credential hit by editing the string.** If a real
   token or key was committed — even briefly — treat it as leaked:
   revoke/rotate it first, then clean the file. Git history is public
   once pushed.
3. **Only if the flagged text is genuinely benign**, add an allowlist
   entry (below) with a comment explaining why.

## Extending the hashed customer-name denylist

`hygiene-denylist.sha256` blocks specific customer/org names without
committing the names themselves: each line is the SHA-256 hash of one
forbidden lowercase token, and the scanner hashes every word token in the
corpus and compares.

To add a name:

```bash
printf '%s' 'name' | tr 'A-Z' 'a-z' | shasum -a 256
```

Paste the resulting hex digest on its own line. Rules that make the
mechanism actually catch things:

- Hash the **all-lowercase** form; the scanner lowercases before hashing.
- Hash the **separator-free** form (no hyphens/dots/underscores). The
  scanner also strips separators from compound tokens before hashing, so
  one separator-free hash catches the hyphenated, dotted, and underscored
  spellings.
- For multi-word names, add one entry per word (when the word alone is
  identifying) plus the joined form.
- Comment *who added it and when* — never what it hashes.

The file ships with one documented example: the hash of the placeholder
token spelled `example` + `customer` (joined, no space). Planting that
joined token in a scanned file is the quickest end-to-end test of the
mechanism. (This doc spells it split apart for the obvious reason: this
file is scanned too.)

## Extending the allowlist

`hygiene-allowlist.txt` is one Python regex per line (`#` comments
allowed). A finding is suppressed when an allowlist regex matches a span
on the same line that fully covers the flagged text. It's a sidecar file
because JSONL has no comment syntax, so suppression can never be inline.

When adding an entry:

- Make it as narrow as possible — anchor to the exact benign shape
  (`US-(EAST|WEST|CENTRAL)-\d+[A-Z]?`), never something broad like a
  bare `.*` around a rule's whole pattern space.
- Add a comment above it: what it allows and why it's benign.
- Remember every entry is a standing hole in the scanner. Rewording the
  corpus entry is almost always better.

## CI wiring

`.github/workflows/eval-hygiene.yml` runs two independent jobs on every
PR (deliberately unfiltered, so the jobs can be marked required without
the path-filter/required-check deadlock) and on pushes to `main` that
touch `evals/**`:

1. **hygiene-scan** — this scanner. Findings surface as inline `::error`
   annotations on the PR diff.
2. **gitleaks** — the upstream gitleaks scanner (pinned by image digest)
   run with `detect --no-git --source evals/`, as an independent second
   opinion on credential shapes this script doesn't model.

Both are blocking by design; the branch protection rule marking them
required is configured in the repo settings, not in the workflow.
