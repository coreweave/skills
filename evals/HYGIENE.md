# Eval corpus hygiene scanner

`check_eval_hygiene.py` is the blocking CI check behind the sanitization
mandate in [README.md](README.md): everything committed under `evals/`
(and under each `skills/<name>/evals/`) ships to customers, so it must
contain no customer identifiers, PII, or credentials. The scanner is
deterministic — stdlib-only regexes and hash comparisons, no network, no
LLM — so a pass or fail is reproducible on any machine. (The tracking
ticket ID lives in the script's docstring; this doc can't cite it because
this doc is itself scanned, and internal ticket IDs are one of the things
the scanner bans.)

CI runs it via `.github/workflows/eval-hygiene.yml` on every PR,
alongside a gitleaks sweep of the same directories and a self-test of
the scanner itself (`scripts/test_eval_hygiene.py`).

## Running it locally

```bash
python3 evals/check_eval_hygiene.py            # evals/ + skills/*/evals/
python3 evals/check_eval_hygiene.py some/dir   # scan another tree
python3 scripts/test_eval_hygiene.py           # self-test the scanner
```

Exit codes: `0` clean, `1` findings, `2` configuration error. Config
errors take precedence: exit `2` means the corpus could not be fully
verified, and includes bad allowlist regexes, malformed denylist
entries, any file the scanner cannot decode, and a **sidecar that
cannot be loaded**. That last one is deliberate and applies to the
built-in defaults, not just to a path you passed: if
`hygiene-denylist.sha256` is deleted, unreadable, or not UTF-8, the run
fails instead of quietly proceeding with customer-name enforcement
switched off.

### Self-testing the scanner

`scripts/test_eval_hygiene.py` (stdlib only, no pytest) builds a
throwaway corpus in a temp directory, plants one violation per hole the
scanner has ever had, and asserts the **exact** number of annotations
per file — including zero for the known-benign vocabulary below. It
lives outside `evals/` on purpose: its planted literals would otherwise
be corpus content, and a committed fixture full of violations would
fail the gate it is testing. Add a case there whenever you add or widen
a rule; CI runs it before the scan, because a rule that has silently
stopped matching reports a leaking corpus as clean.

### File coverage (opt-out, not opt-in)

Every file under the targets is scanned — a `.yaml`, `.csv`, `.txt`, or
`.py` fixture is covered by default, not silently exempt. Hidden files
are scanned too: a committed `.fixture.jsonl` is exactly as public as
any other file in the tree, so a leading dot must not be a way to opt
out of the gate.

The complete exemption list:

- the scanner's own config sidecars (`hygiene-allowlist.txt`,
  `hygiene-denylist.sha256`) and the scripts that implement the gate
  (`check_eval_hygiene.py`, `run_trigger_evals.py`) — their literals
  *are* the ruleset, so scanning them reports the rules rather than a
  leak;
- `.DS_Store` — binary Finder metadata, which the strict decode below
  would turn into a configuration error on every Mac while verifying
  nothing;
- the cache / local-state **directories** named in the scanner's
  `SKIP_DIRNAMES` (`__pycache__`, `.git`, `.venv`, `venv`, `env`,
  `.pytest_cache`, `.mypy_cache`, `.ruff_cache`, `.idea`, `.vscode`,
  `.claude`, `.skillconfig`, `.build-cache`, `node_modules`). Every one
  of those except `.git` is ignored by the repo-root `.gitignore`, so
  none of them can be committed; `.git` is git's own directory, which no
  ignore rule can name and git never tracks. Keep that pairing true
  when you add an entry — `scripts/test_eval_hygiene.py` asserts it, and
  fails the build if a `SKIP_DIRNAMES` entry is not gitignored.
  Directory names only: a *file* called `env` or `node_modules` is
  ordinary corpus content and is scanned.

**Not** exempt: the local sweep artifacts `trigger-results.json` and
`results-*.json`. They are `.gitignore`d, and that is deliberately not
enough on its own to earn a by-name exemption — the two tests above are
"scanning this file is self-defeating" and "scanning it is impossible",
not "this file is not normally committed". A sweep artifact holds raw
model and tool output, the least-reviewed text in the tree, so an
exemption would leave a force-added copy permanently unscanned. If a
local artifact makes the gate red, **delete it**; never allowlist what a
sweep happened to echo.

Three hardening behaviors to know about:

- **JSON-aware scanning.** For `*.json` / `*.jsonl`, the decoded string
  values are scanned in a second pass, so `\uXXXX`-escaping a match (or
  letting `json.dumps` escape a non-ASCII name) cannot hide it from the
  raw-text pass. Whole-document `.json` findings from that pass are
  reported at line 1 with a note.
- **Fail-closed encoding.** Files must be UTF-8, or UTF-16/32 with a
  BOM. A file that does not decode cleanly, or that contains NUL bytes
  after decoding (binary content, BOM-less UTF-16), exits `2` — the
  scanner never reports a file it could not read as clean.
- **Names are scanned, not just contents.** The target-relative path of
  every file goes through the same rules and denylist, reported at line
  1 with a `(in the file NAME ...)` note. A fixture named after a
  customer, a ticket, or an account ID publishes that identifier in the
  repo's tree listing just as effectively as its contents would, and
  the content pass never sees a file name. Directory components count
  too, and each adjacent hyphen-delimited pair inside a path segment is
  offered as its own candidate — otherwise the `ticket-id` rule's
  standalone-token lookarounds would let a ticket ID hide inside a
  longer name (`PROJ-1234-repro.jsonl`). So: name a case file after the
  *behavior* it covers, never after who reported it. The path used is
  relative to the scanned target, so your checkout location and home
  directory are never part of what gets matched.

  **One rule is stricter on names than on contents:** `ticket-id`
  requires an *uppercase* project key when it runs over a path
  (`NAME_RULES` in the scanner). Splitting a path on hyphens
  manufactures `word-number` candidates out of ordinary names, and
  `word-number` is also the shape of a Jira key — so with the
  case-insensitive content rule, `case-<n>.jsonl`, `batch-<n>.jsonl` and
  `gpu-8-node.jsonl` were each a *blocking* ticket-id finding. Real Jira
  keys are written uppercase (`KEY-<number>`), so requiring that keeps
  the protection and makes ordinary fixture names structurally benign
  rather than benign-by-allowlist. The honest cost: an all-lowercase
  ticket ID in a *file name* (`appsec-3971-repro.jsonl`) is
  indistinguishable from ordinary naming and is **not** flagged by the
  name pass — it is still flagged anywhere in file *contents*. Widening
  the name rule to catch it brings the false positives back, so don't.

## What it checks

| Rule | Catches |
| --- | --- |
| `email-address` | Any email address |
| `internal-domain-handle` | Any `@coreweave` / `@wandb` dot-com handle, even without a local part |
| `anthropic-api-key` | Keys starting `sk-ant-` |
| `aws-access-key-id` | `AKIA` + 16 uppercase alphanumerics |
| `github-token` | `ghp_` / `gho_` / `ghu_` / `ghs_` / `ghr_` tokens |
| `slack-token` | Every `xox?-` token family (xoxb, xoxp, xoxc, xoxd, xoxe, ...) |
| `jwt` | `eyJ`-prefixed dotted base64url values — two segments as well as three, deliberately: an `alg=none` token is `header.payload.` with an empty signature, and a truncated log paste keeps only `header.payload` |
| `pem-header` | `-----BEGIN ... KEY-----` style PEM headers |
| `ipv4-address` | Dotted-quad IPs, including leading-zero and sentence-final spellings |
| `ticket-id` | Jira-style IDs: a letters-only project key, a hyphen, and an issue number. Case-insensitive in file contents, so a lowercased paste still trips; uppercase-key-only when the rule runs over a file *name* (see "Names are scanned") |
| `uuid` | UUID-shaped identifiers |
| `console-url-with-org-id` | CoreWeave console/cloud URLs with an org, account, or tenant ID in the path or query string (snake_case or camelCase). The host must be `coreweave.com` or a dot-delimited subdomain of it, terminated by a port, path, query, or fragment — a third-party lookalike (`fakecoreweave.com`, or `coreweave.com` used as a *prefix* of someone else's domain) is not a CoreWeave URL and is not flagged |
| `customer-denylist` | Tokens whose SHA-256 hash appears in `hygiene-denylist.sha256` |

Findings never echo the full matched value: pattern matches are redacted
to a short prefix — never more than a third of the match, and for emails
never past half of the local part — and denylist hits print only a hash
prefix.

### Known-benign shapes it must not flag

The corpus legitimately contains CoreWeave availability-zone names (like
`US-EAST-04A`), instance types (like `gd-8xh100ib-i128`), GPU names
(`A100-80`), and standards names (`IEEE-754`, `SOC-2`, `FIPS-140`,
`NIST-800`, `GPT-4`, `TLS-1`, `COVID-19`, `SHA-256`, `UTF-8`). The
`ticket-id` rule dodges all of these structurally:

- it only matches standalone `KEY-<number>` tokens — nothing preceded or
  followed by another hyphen-joined segment — so zone names and instance
  types pass;
- the project key must be letters-only, so GPU-ish tokens like `A100-80`
  can never match;
- a benign-prefix class baked into the rule excludes the open-ended
  standards family (IEEE, FIPS, SOC, PCI, NIST, TLS, ISO, RFC, SHA, UTF,
  GPT, COVID, CVE, ...);
- over file *names* it additionally requires an uppercase project key,
  so the ordinary way corpus files are named — `case-<n>.jsonl`,
  `batch-<n>.jsonl`, `gpu-8-node.jsonl`, `shard-3-of-8.jsonl` — cannot
  produce a finding. (Those first two are spelled with a placeholder
  here only because this doc is itself scanned, and the *content* rule
  is still case-insensitive by design; the exact literals are in
  `scripts/test_eval_hygiene.py`, which lives outside `evals/`.)

`hygiene-allowlist.txt` carries zone-name and standards patterns as
defense in depth on top of that. If a new benign identifier family trips
the scanner, prefer extending the rule's benign-prefix class (for an
open-ended family) or the allowlist (for a specific literal shape) —
never weaken the rule's structure.

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
committing the names in searchable form: each line is the SHA-256 hash
of one forbidden lowercase token.

**Threat-model limit — do not over-trust this.** The hashes are
unsalted and the inputs are low-entropy company names: anyone with a
candidate list (a customer roster, a market directory) can reverse every
entry by brute force in seconds, and the entry count reveals how many
names are considered sensitive. The mechanism buys grep-resistance — a
name never appears in the repo in plaintext — not secrecy against a
motivated reader.

What the scanner hashes and compares, per line of corpus text:

- the line is NFKC-normalized **before** it is split into tokens, then
  lowercased. Order matters: a combining accent is not a word
  character, so a canonically decomposed spelling (`e` + U+0301) splits
  into runs that per-token normalization could never rejoin, and its
  hash would never match the precomposed form's. Normalizing first also
  folds compatibility spellings (fullwidth text, ligatures) onto ASCII;
- every word token, lowercased;
- every adjacent 2- and 3-token join, so spaced, hyphenated, dotted, and
  underscore-joined spellings of a multi-part name reduce to the same
  candidate (a name split as two words, embedded in a hostname, or
  buried in a resource slug still trips);
- digit-stripped variants, so a year or numeric suffix fused into the
  token doesn't evade.

**Residual gaps, honestly:** exact hashing cannot catch a name fused
with other letters (a denylisted `acmecorp` hiding inside
`acmecorpinc`), leetspeak substitutions, or homoglyph spellings. The
denylist is a tripwire for the common accidental paste, not a
substitute for review.

To add a name:

```bash
printf '%s' 'name' | tr 'A-Z' 'a-z' | shasum -a 256
```

Paste the resulting hex digest on its own line. Hash the all-lowercase,
separator-free, digit-free form (`acmecorp`, not `Acme-Corp` or
`acmecorp2024`); for multi-word names add one entry per identifying word
plus the joined form. Comment *who added it and when* — never what it
hashes. The file ships with one documented placeholder entry (see its
header comment for the token, which this doc must not spell out —
adjacent words get joined and hashed, so even a split spelling here
would trip the scanner on its own documentation). Planting that token in
a scanned file is the quickest end-to-end test of the mechanism.

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

## Gate integrity — two policies

- **The gate can be edited by the PR it gates.** A PR that adds a leak
  can, in the same diff, add an allowlist entry or delete a denylist
  hash that would have caught it. The workflow cannot prevent that;
  review can. Recommended admin follow-up (deliberately not part of this
  change): a CODEOWNERS rule covering `evals/hygiene-*`,
  `evals/check_eval_hygiene.py`, and the workflow file, so gate edits
  require a second set of eyes.
- **A red push-to-main run is a leak, not a flake.** By the time the
  push-to-main backstop fails, the content is already on `main` and
  effectively public. Do not just fix-forward and re-run: treat it as a
  disclosure — rotate any credential, scrub the identifier, and remember
  the value also lives in git history (see "How to fix a hit", item 2).

## CI wiring

`.github/workflows/eval-hygiene.yml` runs two independent jobs on every
PR (deliberately unfiltered, so the jobs can be marked required without
the path-filter/required-check deadlock) and on pushes to `main` that
touch eval content:

1. **hygiene-scan** — this scanner. Findings surface as inline `::error`
   annotations on the PR diff.
2. **gitleaks** — the upstream gitleaks scanner (pinned by image digest)
   run with `detect --no-git` over the eval directories, as an
   independent second opinion on credential shapes this script doesn't
   model.

Both are blocking by design; the branch protection rule marking them
required is configured in the repo settings, not in the workflow.
