# Eval corpus hygiene scanner

`check_eval_hygiene.py` is the CI check behind the sanitization mandate
in [README.md](README.md). It scans the **whole repository**, because
the whole repository is going public: a customer identifier in a root
markdown file is exactly as disclosed as one in a shipped skill. The
threat model's blast radius is disclosure "to anyone with repo read
access, and to the general public once the repo's public launch
completes" — repo visibility, not whether a customer receives the bytes.

The scanner is deterministic — stdlib-only regexes, no network, no LLM —
so a pass or fail is reproducible on any machine.

## Two tiers, and why the scan mostly warns

Scanning everything would drown a build in false positives if every rule
were treated alike. The answer is narrower **rules**, not a narrower
scope. Two axes:

**1. What blocks.** Only credential-shaped findings fail the run
(`BLOCKING_RULES`): Anthropic keys, AWS access key IDs, GitHub and Slack
tokens, PEM headers, JWTs. The split is by *false-positive rate*, not by
how bad the thing sounds — an `AKIA` followed by exactly 16 uppercase
alphanumerics is a credential, never a coincidence, so blocking it is
safe and merging it would publish a live secret.

Everything else — emails, IPs, ticket IDs, UUIDs, tenant URLs — has
legitimate look-alikes, so it is reported as a **warning** and the run
passes. Someone goes and looks. That is deliberate: a gate that
red-gates a merge over a documentation IP is a gate people switch off,
and a switched-off gate protects nothing. Pass `--strict` to make every
finding blocking.

**2. Where the paste-residue rules apply.** Tier 1 (`CORPUS_ONLY_RULES`)
runs *only* over `evals/` and `skills/<name>/evals/`. It asks "did this
text arrive by paste?", which is a sharp question about a corpus of
customer queries and a meaningless one about a hand-written LICENSE or
README — where a typographic quote is just typography. Applied
repo-wide it fired on `LICENSE`, which is precisely the kind of noise
that gets a scanner disabled.

### What is not scanned

Only three classes, each because scanning it is impossible or
self-defeating: binary files (by extension — a file that *claims* to be
text and isn't is still a hard error), the gate's own files including
`check_eval_hygiene_selftest.py`, which is a planted-violation battery
by design, and the cache/local-state directories listed below.

## Running it locally

```bash
python3 evals/check_eval_hygiene.py           # the whole repo
python3 evals/check_eval_hygiene.py some/dir  # scan another tree
python3 evals/check_eval_hygiene.py --strict  # make every finding block
python3 scripts/check_eval_hygiene_selftest.py  # self-test the scanner

# PR text uses the same rules, via stdin:
gh pr view 44 --json body -q .body | \
  python3 evals/check_eval_hygiene.py --stdin --label "PR body"
```

Exit codes: `0` clean **or warnings only**, `1` a blocking
(credential-shaped) finding, `2` configuration error. Config
errors take precedence: exit `2` means the corpus could not be fully
verified, and includes bad allowlist regexes, any file the scanner
cannot decode, and a **sidecar that cannot be loaded**. That last one
is deliberate and applies to the built-in default, not just to a path
you passed: if `hygiene-allowlist.txt` is deleted, unreadable, or not
UTF-8, the run fails instead of quietly proceeding with an empty
ruleset.

### Self-testing the scanner

`scripts/check_eval_hygiene_selftest.py` (stdlib only, no pytest) builds a
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

- the scanner's own config sidecar (`hygiene-allowlist.txt`) and the
  scripts that implement the gate
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
  when you add an entry — `scripts/check_eval_hygiene_selftest.py` asserts it, and
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
  every file goes through the same rules, reported at line 1 with a
  `(in the file NAME ...)` note. A fixture named after a ticket or an
  account ID publishes that identifier in the
  repo's tree listing just as effectively as its contents would, and
  the content pass never sees a file name. Directory components count
  too, and each adjacent hyphen-delimited pair inside a path segment is
  offered as its own candidate — otherwise the `ticket-id` rule's
  standalone-token lookarounds would let a ticket ID hide inside a
  longer name (`PROJ-1234-repro.jsonl`). So: name a case file after the
  *behavior* it covers, never after who reported it. The path used is
  relative to the scanned target, so your checkout location and home
  directory are never part of what gets matched.

  Names and contents run the **same** rules. They used to diverge on
  `ticket-id` — uppercase-only on names, case-insensitive in contents —
  because splitting a path on hyphens manufactures `word-number`
  candidates out of ordinary names. That split is gone: the content rule
  is uppercase-only too now, for exactly the same reason, since shipped
  prose turned out to be just as full of `word-number` tokens as file
  names are. See the `ticket-id` trade under "Known-benign shapes".

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
| `ticket-id` | Jira-style IDs: an **uppercase** letters-only project key, a hyphen, and an issue number |
| `uuid` | UUID-shaped identifiers |
| `invisible-character` | Paste residue: non-breaking and zero-width spaces, joiners, soft hyphen, a stray BOM. Reported as a code point (`U+00A0`), since redacting an invisible character prints nothing |
| `smart-quote` | Curly quotes — what a chat client's autoformat produces |
| `chat-mention` | Slack user/channel markup (`<@U…>`, `<#C…\|name>`), and broadcast mentions — an `@` followed by *here*, *channel*, or *everyone* (not spelled out here: this doc is scanned by its own rules) |
| `quoted-reply-header` | Mail and chat quote scaffolding: `On <date>, <name> wrote:`, `Sent from my …`, bracketed clock times |
| `console-url-with-org-id` | CoreWeave console/cloud URLs with an org, account, or tenant ID in the path or query string (snake_case or camelCase). The host must be `coreweave.com` or a dot-delimited subdomain of it, terminated by a port, path, query, or fragment — a third-party lookalike (`fakecoreweave.com`, or `coreweave.com` used as a *prefix* of someone else's domain) is not a CoreWeave URL and is not flagged |

Findings never echo the full matched value: matches are redacted to a
short prefix — never more than a third of the match, and for emails
never past half of the local part.

**Every rule above matches a SHAPE.** The scanner holds no list of
customers, and adding one is out of scope by policy — see "Why there is
no customer-name list" below.

### Tier 1: paste residue (corpora only)

Four of those rules do not look for an identifier at all. They look for
evidence that text **arrived by copy-paste rather than by authoring** —
because that is the moment sanitization gets skipped. The threat model's
attack vector is not "someone quoted a transcript", it is *"their manual
sanitization pass misses an identifier"*, and that is overwhelmingly a
property of careless pasting.

Provenance itself is undetectable, and no rule here pretends otherwise:
a well-sanitized transcript quote and a well-written synthetic query are
the same artifact by construction. What is detectable is a *sloppy*
paste, and these four catch it cheaply and deterministically.

Every character class was measured across `evals/`, `dist/`, `plugins/`,
`skills/` and `_snippets/` and found **zero** times before being made
blocking. The negatives matter just as much: em-dash appears in 44 files
and `…` in 12, so neither is ever flagged. The fix for a hit is always
the same — retype the character in ASCII.

### Known-benign shapes it must not flag

The shipped content legitimately contains CoreWeave availability-zone
names (like `US-EAST-04A`), instance types (`gd-8xh100ib-i128`, and
`cpu-4` — 16 times in `dist/`), model names (`Llama-3` 8 times,
`TinyLlama-1` twice), GPU names (`A100-80`), and standards names (`IEEE-754`, `SOC-2`, `FIPS-140`,
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
- the project key must be **UPPERCASE**, which is what makes all of the
  above structurally benign along with the ordinary way corpus files are
  named (`case-<n>.jsonl`, `batch-<n>.jsonl`, `gpu-8-node.jsonl`).

  This is a measured trade, not a default. A scan of `dist/` found
  `cpu-4`, `Llama-3` and `TinyLlama-1` a combined 26 times and **zero**
  real ticket IDs — a case-insensitive rule red-gates every one of them,
  and a gate that cries wolf on the product's own vocabulary gets routed
  around rather than fixed. The cost, stated plainly: an all-lowercase
  paste (`see appsec-1234`) is **not** flagged. Real Jira keys are
  written uppercase, so this keeps the case that matters.

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

## Why there is no customer-name list

An earlier revision of this gate carried `hygiene-denylist.sha256`: a
list of SHA-256 hashes of forbidden customer/org names, so the scanner
could block a name without the name appearing in the repo in plaintext.
It was removed, and it should not come back here. The reasoning, so
nobody re-derives it:

- **The hashes are reversible.** They were unsalted digests of
  low-entropy company names. Anyone holding a candidate list — a
  customer roster, a logo wall, a market directory — recovers every
  entry by brute force in seconds.
- **The file leaks without being read.** Its line count says how many
  names are considered sensitive, and `git log` dates every addition.
  "Which customer was added the same week as $EVENT" is a correlation
  you cannot retract from a public repo, and deleting the line later
  does not help, because history is public too.
- **It was the wrong layer.** The tracking threat model's defense
  chain (ticket ID in the scanner's docstring — this doc is scanned, so
  it cannot spell one) puts pattern-shaped leaks in an automated CI
  check, this scanner, at layer 3; customer identity sits at layer 4, a
  **second-reviewer requirement for transcript-derived entries**. A name in otherwise-clean
  prose has no shape to match; it needs a human who knows the account.
  Hashing was an attempt to do layer 4's job in layer 3, and the cost
  of making it work was committing the very names the control exists
  to protect.

So this gate is pattern-only and commits nothing about anyone. Catching
a customer name is a review responsibility, and `evals/README.md`'s
sanitization rules are what a reviewer checks against. **If you find
yourself wanting to add a name list here, that is the signal to ask for
the second-reviewer control instead.**

## Residual gaps, honestly

Three gaps are inherent to matching on shape, and none is fixable by
tightening a rule:

- **Obfuscated spellings.** `name AT domain DOT com` and `bob(at)
  example.com` are emails to a human and not to a regex. Matching them
  means matching the word "at" between two words, which the corpus is
  full of.
- **Encoded payloads.** A base64- or hex-wrapped secret is high-entropy
  noise to these rules. gitleaks' entropy heuristics cover part of this
  and are why that second job exists; neither job covers all of it.
- **Lowercase ticket IDs**, per the uppercase-key trade above.
- **Provenance.** Nothing here can tell a sanitized transcript quote
  from a synthetic query — by construction they are the same artifact.
  The Tier 1 rules detect careless *pasting*, which is the risky
  behavior; they do not detect careful quoting, which is the safe one.

- **Non-ASCII lookalikes.** The rules run on raw text with no Unicode
  normalization, so a fullwidth or homoglyph spelling of an address
  does not match the ASCII rule. (The scanner used to normalize, but
  only on the customer-name path, which is gone; the pattern rules
  never used it.) `scripts/check_eval_hygiene_selftest.py` asserts this
  as a zero-finding case so it stays a stated gap, not a surprise.

All three are inherent to a deterministic gate, which is what makes it
trustworthy enough to block a merge. They are the reason this is a
backstop under human review rather than a replacement for it.

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

## PR text

`.github/workflows/pr-text-hygiene.yml` pipes the PR body, every
comment, every review body, and every review comment through
`--stdin`, using the **identifier** rules — emails, tokens, IPs, ticket
IDs, UUIDs, tenant URLs. PR text is a publication surface
nothing reviews: a diff gets read line by line, a description gets
skimmed once, and a comment is where someone pastes the log line or the
node IP that explains what they were debugging.

**The two surfaces are not the same, and the difference decides what to
do about a hit:**

- A **PR body** is genuinely gated. Edit the description, the check goes
  green, nothing merged.
- A **comment** is an *alarm only*, reported as a `::warning::` that
  does **not** fail the job. It was public the moment it posted, so a
  failure could never be cleared — and a permanently-red check is one
  people learn to ignore, which costs more than the signal is worth. A
  warning means handle a disclosure: rotate the credential, and know
  that GitHub keeps edit history. Editing it is not a fix. Same rule as
  a red push-to-`main` run, below, for the same reason.

**Tier 1 rules do not run on PR text**, deliberately. They ask "did this
arrive by paste?", which is a meaningful question about a committed
corpus file and a meaningless one about a comment somebody typed into a
web box. Applied to prose, `smart-quote` fired on ordinary review
comments — a curly apostrophe in a sentence is just an apostrophe — and
that is precisely how a gate earns its way onto the ignore list.

## Gate integrity — two policies

- **The gate can be edited by the PR it gates.** A PR that adds a leak
  can, in the same diff, add an allowlist entry that suppresses the
  finding which would have caught it. No workflow can prevent that; review
  can, so every surface that could switch this gate off is listed in
  [`.github/CODEOWNERS`](../.github/CODEOWNERS): the scanner, the
  allowlist, the self-test, and `.github/workflows/`. A gate edit
  therefore requires a second set of eyes from `@coreweave/docs`.
  Treat an allowlist addition arriving in the same PR as the corpus
  entry it unblocks as the thing to look at hardest — that is the exact
  shape this rule exists to catch.
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

1. **hygiene-scan** — this scanner, preceded by its self-test.
   Findings surface as inline `::error` annotations on the PR diff.
2. **gitleaks** — the upstream gitleaks scanner (pinned by image digest)
   run with `detect --no-git` over the eval directories, as an
   independent second opinion on credential shapes this script doesn't
   model.

Both are blocking by design; the branch protection rule marking them
required is configured in the repo settings, not in the workflow.
