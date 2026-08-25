#!/usr/bin/env python3
"""Self-test battery for evals/check_eval_hygiene.py.

WHY THIS EXISTS
---------------
The hygiene scanner is a blocking gate whose failure mode is silence: a
regex that stops matching, an exemption that grows, or a dedup key that
suppresses one finding too many all leave the build green while the
corpus leaks. Every hole this file plants was a real, reproduced hole in
the scanner at some point; this battery is what keeps them closed.

The fixtures are BUILT IN A TEMP DIRECTORY, never committed, for two
reasons: a committed fixture full of planted violations would fail the
gate it is testing, and this file lives outside evals/ so the planted
literals below are not themselves corpus content.

    python3 scripts/check_eval_hygiene_selftest.py     # exit 0 = all checks pass

Each check states the invariant it defends. When you add a rule to the
scanner, add a case here — both a positive (it trips) and, if the rule
lives anywhere near the corpus's real vocabulary, a negative (a
known-benign shape it must not trip).

NOT named ``test_*``, deliberately. pytest is in this repo's dev extras,
and under pytest's default collection a ``test_*.py`` file of ``test_*``
functions reports this battery as PASSING when it is doing nothing of
the kind: the ones taking a ``tmp`` argument error out as a missing
fixture, and the ones that don't record their failures into the module
``failures`` list, which only ``main()`` ever inspects — so pytest sees
a function that returned without raising and calls it green. A
self-test that reports green while asserting nothing is worse than no
self-test, so the names keep pytest from collecting it at all. Run it
as the script it is.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import os
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCANNER = REPO_ROOT / "evals" / "check_eval_hygiene.py"

_spec = importlib.util.spec_from_file_location("check_eval_hygiene", SCANNER)
assert _spec and _spec.loader
hygiene = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hygiene)

# A planted email, used wherever a case needs one specific value to
# appear in two places (raw and escaped, or name and contents). The
# scanner is pattern-only, so every fixture value below is a SHAPE.
LEAK_EMAIL = "bob@example.com"

failures: list[str] = []
checks = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global checks
    checks += 1
    if not ok:
        failures.append(f"{name}: {detail}" if detail else name)


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


# --------------------------------------------------------------------------
# Fixture corpus: one planted violation per hole, plus the benign battery.
# --------------------------------------------------------------------------
def build_corpus(root: Path) -> tuple[Path, Path, dict[str, int]]:
    """Write the fixture tree.

    Returns (allowlist, expected), where expected maps a
    corpus-relative path to the exact number of annotations it must
    produce — 0 for the negative battery and the legitimate exemptions.
    """
    allowlist = write(
        root / "allow.txt",
        "# fixture allowlist: the planted address is allowed ONLY in this\n"
        "# one documented phrase, to prove suppression is context-specific.\n"
        f"sanitized sample: {LEAK_EMAIL}\n",
    )

    corpus = root / "corpus"
    expected: dict[str, int] = {}

    # Baseline: an ordinary planted email in an ordinary file.
    write(corpus / "plain.jsonl", '{"query": "mail bob@example.com about it"}\n')
    expected["plain.jsonl"] = 1  # one email, reported once (not twice: raw + decoded dedup)

    # HOLE: hidden files were skipped wholesale, so a committed
    # .fixture.jsonl was exempt from a gate nothing else covers.
    write(corpus / ".fixture.jsonl", '{"query": "see PROJ-1234 for the repro"}\n')
    expected[".fixture.jsonl"] = 1

    # ... while genuine cache/local-state dirs stay exempt (they are
    # gitignored, and scanning a .venv would be noise, not coverage).
    write(corpus / "__pycache__" / "junk.txt", "bob@example.com\n")
    write(corpus / ".venv" / "lib" / "junk.txt", "bob@example.com\n")
    write(corpus / ".git" / "COMMIT_EDITMSG", "bob@example.com\n")
    expected["__pycache__/junk.txt"] = 0
    expected[".venv/lib/junk.txt"] = 0
    expected[".git/COMMIT_EDITMSG"] = 0

    # HOLE: dedup keys were recorded for findings the allowlist had
    # SUPPRESSED, so one allowed occurrence covered an unrelated
    # \uXXXX-escaped occurrence elsewhere in the same document.
    #
    # The escape is in the DOMAIN on purpose. Escaping a local-part
    # character instead leaves the raw text still email-SHAPED
    # ("\u0062ob@example.com" matches the rule as written), so the raw
    # pass reports it under a different key and the case passes for the
    # wrong reason — it would no longer detect the bug it exists for.
    # With the domain escaped, the raw pass finds nothing here and the
    # only route to this leak is the decoded pass.
    write(
        corpus / "escaped.json",
        "{\n"
        f'  "doc": "sanitized sample: {LEAK_EMAIL}",\n'
        '  "leak": "acct bob@\\u0065xample.com prod"\n'
        "}\n",
    )
    expected["escaped.json"] = 1

    # Fullwidth / decomposed spellings are NOT normalized: the rules run
    # on raw text. Asserted as zero so the gap stays a stated one (see
    # HYGIENE.md, "Residual gaps") rather than a silent surprise.
    write(corpus / "unicode.jsonl",
          '{"query": "mail ｂｏｂ＠ｅｘａｍｐｌｅ．ｃｏｍ today"}\n')
    expected["unicode.jsonl"] = 0

    # HOLE: only file CONTENTS were scanned, so a file named after a
    # ticket or an account passed a clean gate.
    # ... including a ticket ID fused into a longer name, which the
    # ticket rule's standalone-token lookarounds cannot see in the path
    # as a whole (that is what the hyphen-pair candidates are for), and
    # an address or account ID used as a name.
    write(corpus / "PROJ-1234-repro.jsonl", '{"query": "nothing to see"}\n')
    expected["PROJ-1234-repro.jsonl"] = 1
    # ... and the plain standalone spelling, which the whole path matches.
    write(corpus / "PROJ-5678.jsonl", '{"query": "nothing to see"}\n')
    expected["PROJ-5678.jsonl"] = 1
    write(corpus / "bob@example.com.jsonl", '{"query": "nothing to see"}\n')
    expected["bob@example.com.jsonl"] = 1
    write(corpus / "3fa85f64-5717-4562-b3fc-2c963f66afa6-run.jsonl",
          '{"query": "nothing to see"}\n')
    expected["3fa85f64-5717-4562-b3fc-2c963f66afa6-run.jsonl"] = 1
    # A directory component counts as much as the file name.
    write(corpus / "cases" / "PROJ-9999" / "PROJ-4321-a.jsonl",
          '{"query": "nothing to see"}\n')
    expected["cases/PROJ-9999/PROJ-4321-a.jsonl"] = 2  # ticket dir + ticket file
    # ... and each distinct value is reported ONCE, not once per candidate:
    # the ticket below matches in a hyphen pair AND could match again via
    # a longer candidate. Two DISTINCT values, so two findings, not four.
    write(corpus / "UUID-PROJ-1234-case.jsonl", '{"query": "nothing to see"}\n')
    expected["UUID-PROJ-1234-case.jsonl"] = 1  # just the ticket, reported once

    # HOLE: the local sweep artifact was exempt BY NAME while not being
    # gitignored, so a force-added copy was permanently unscanned.
    write(corpus / "trigger-results.json", '{"config": {"user": "bob@example.com"}}\n')
    expected["trigger-results.json"] = 1

    # HOLE: the SKIP_DIRNAMES membership test covered every part of the
    # relative path INCLUDING the final one, so a FILE whose name
    # happened to match a cache-directory name was exempt, contents and
    # all. Extensionless fixture names are ordinary corpus content.
    for name in ("env", "node_modules", ".claude"):
        write(corpus / name, "bob@example.com\n")
        expected[name] = 1
    # ... while the same name as a DIRECTORY stays exempt (in a
    # subdirectory, since the corpus root already holds the file above).
    write(corpus / "nested" / "node_modules" / "pkg" / "junk.txt", "bob@example.com\n")
    expected["nested/node_modules/pkg/junk.txt"] = 0

    # The scanner's own config/scripts stay exempt: they carry the rules.
    write(corpus / "hygiene-allowlist.txt", "bob@example.com\n")
    write(corpus / "check_eval_hygiene.py", "bob@example.com\n")
    expected["hygiene-allowlist.txt"] = 0
    expected["check_eval_hygiene.py"] = 0

    # Positive: a real tenant-bearing CoreWeave console URL.
    write(
        corpus / "console.jsonl",
        '{"query": "open https://console.coreweave.com/orgs/acme-eng"}\n',
    )
    expected["console.jsonl"] = 1

    # Benign hyphenated FILE NAMES: the name pass must not turn the
    # corpus's real vocabulary — or the way corpus files are ordinarily
    # named — into blocking findings.
    #
    # REGRESSION: the first four below each produced a blocking
    # ticket-id name finding when the name pass reused the
    # case-insensitive content rule, because `<word>-<number>` is both
    # the shape of a Jira key and the shape of half the file names in
    # any corpus. NAME_RULES requires an uppercase project key for
    # exactly this reason; these cases fail if that is relaxed.
    for name in (
        "case-1.jsonl",
        "batch-2.jsonl",
        "gpu-8-node.jsonl",
        "shard-3-of-8.jsonl",
        "us-east-04a-zone-cases.jsonl",
        "a100-80-benchmarks.jsonl",
        "ieee-754-rounding.jsonl",
        "gd-8xh100ib-i128-sizing.jsonl",
        "soc-2-evidence.jsonl",
        "tls-1-3-handshake.jsonl",
    ):
        write(corpus / name, '{"query": "nothing to see"}\n')
        expected[name] = 0

    # TIER 1 — paste residue. One file, four planted artifacts of text
    # that arrived by copy-paste rather than by authoring: a NBSP, a
    # curly quote, a Slack mention, and a mail quote header. This is the
    # signal that actually correlates with the ticket's attack vector —
    # "their manual sanitization pass misses an identifier" is what
    # happens when text is pasted, not when it is written.
    #
    # Under evals/ specifically: these rules are CORPUS-ONLY. The same
    # artifacts in authored prose are not findings, which the negative
    # below asserts — a typographic quote in a LICENSE or a README is
    # typography, not evidence that a transcript was pasted.
    write(
        corpus / "evals" / "pasted.jsonl",
        '{"query": "spin up\u00a0a cluster"}\n'
        '{"query": "the \u201cstaging\u201d cluster"}\n'
        '{"query": "ask <@U01ABCDEF> about it"}\n'
        '{"query": "On Tue, Jan 6, 2026 at 3:14 PM, Someone wrote:"}\n',
    )
    # Line 2 has TWO curly quotes, so five findings across four lines.
    expected["evals/pasted.jsonl"] = 5

    # Byte-for-byte the same artifacts, outside a corpus: zero findings.
    # This is the check that keeps the gate off LICENSE and the docs.
    write(
        corpus / "docs" / "prose.md",
        "spin up\u00a0a cluster\n"
        "the \u201cstaging\u201d cluster\n"
        "ask <@U01ABCDEF> about it\n"
        "On Tue, Jan 6, 2026 at 3:14 PM, Someone wrote:\n",
    )
    expected["docs/prose.md"] = 0

    # NEGATIVE BATTERY — none of these may produce a finding.
    #   * lookalike hosts (used to be reported as CoreWeave tenant URLs),
    #   * the known-benign standards/zone/instance/GPU vocabulary the
    #     corpus legitimately uses.
    write(
        corpus / "benign.jsonl",
        '{"query": "https://fakecoreweave.com/orgs/acme is not ours"}\n'
        '{"query": "nor is https://coreweave.com.evil.example/orgs/acme"}\n'
        '{"query": "IEEE-754 rounding under SOC-2 audit with GPT-4"}\n'
        '{"query": "an A100-80 in US-EAST-04A on gd-8xh100ib-i128"}\n'
        '{"query": "FIPS-140, NIST-800, TLS-1, SHA-256, UTF-8, COVID-19"}\n'
        # Shipped-content vocabulary measured in dist/, plus the prose
        # punctuation this repo uses in 44 and 12 files respectively.
        '{"query": "serve Llama-3 or TinyLlama-1 on a cpu-4 node"}\n'
        '{"query": "scale up \u2014 then wait\u2026 and retry"}\n',
        # NOTE: nothing allowlist-dependent belongs in this file. The e2e
        # fixture runs against its own minimal allowlist so suppression
        # stays controlled, so a documentation CIDR would fire here even
        # though the real allowlist covers it. Those live in
        # verify_rule_shapes(), which loads the REAL allowlist.
    )
    expected["benign.jsonl"] = 0
    return allowlist, expected


def run_scanner(corpus: Path, allowlist: Path) -> tuple[int, list[str]]:
    """Run main() as CI does (annotation mode) and return (rc, lines)."""
    buf = io.StringIO()
    previous = os.environ.get("GITHUB_ACTIONS")
    os.environ["GITHUB_ACTIONS"] = "true"
    try:
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
            rc = hygiene.main([str(corpus), "--allowlist", str(allowlist),
                               "--strict"])
    finally:
        if previous is None:
            os.environ.pop("GITHUB_ACTIONS", None)
        else:
            os.environ["GITHUB_ACTIONS"] = previous
    return rc, [ln for ln in buf.getvalue().splitlines() if ln.startswith("::error")]


def verify_end_to_end(tmp: Path) -> None:
    allowlist, expected = build_corpus(tmp / "e2e")
    corpus = tmp / "e2e" / "corpus"
    rc, annotations = run_scanner(corpus, allowlist)

    check("e2e exit code is 1 (findings)", rc == 1, f"got {rc}")

    counts: dict[str, int] = {}
    for annotation in annotations:
        flagged = annotation.split("file=", 1)[1].split(",line=", 1)[0]
        rel = str(Path(flagged).resolve().relative_to(corpus.resolve()))
        counts[rel] = counts.get(rel, 0) + 1

    for rel, want in sorted(expected.items()):
        check(
            f"e2e: {rel} produces exactly {want} annotation(s)",
            counts.get(rel, 0) == want,
            f"got {counts.get(rel, 0)}\n     all annotations:\n       "
            + "\n       ".join(annotations),
        )
    check(
        "e2e reports nothing outside the fixture inventory",
        set(counts) <= set(expected),
        f"unexpected files flagged: {sorted(set(counts) - set(expected))}",
    )
    check(
        "e2e annotation total matches the planted total",
        len(annotations) == sum(expected.values()),
        f"expected {sum(expected.values())}, got {len(annotations)}",
    )

    # A clean tree must still exit 0 and say so.
    clean = tmp / "clean" / "corpus"
    write(clean / "ok.jsonl", '{"query": "how do I resize a cluster?"}\n')
    rc, annotations = run_scanner(clean, allowlist)
    check("clean tree exits 0", rc == 0, f"got {rc}")
    check("clean tree emits no annotations", not annotations, str(annotations))


def verify_stdin_mode() -> None:
    """--stdin applies the same rules to PR bodies and comments.

    PR text is a publication surface nothing reviews: a diff is read line
    by line, a PR description is skimmed once. Same ruleset, so a rule
    added for files cannot silently fail to cover the text surface.
    """
    def run(text: str) -> tuple[int, str]:
        buf = io.StringIO()
        allow = hygiene.load_allowlist(hygiene.DEFAULT_ALLOWLIST)
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
            rc = hygiene.scan_stdin(text, "PR body", allow, False)
        return rc, buf.getvalue()

    rc, out = run("spin up a cluster with 8 H100s\nno secrets here\n")
    check("stdin: clean text exits 0", rc == 0, f"got {rc}")
    check("stdin: clean text says so", "clean" in out, out)

    # NOT bob@example.com: example.com is RFC 2606 reserved, so the real
    # allowlist suppresses it on purpose. A planted leak has to be a
    # value that could actually belong to someone.
    # Neither value may be one the real allowlist covers: example.com is
    # RFC 2606 reserved, and our own project keys are allowlisted as
    # maintainer notes. A planted leak has to look like a CUSTOMER's.
    leak = "ops@acmecloud.io"
    rc, out = run(f"repro for ACME-4471\nping {leak}\nnode at 10.16.4.7\n")
    check("stdin: a leaky body exits 1", rc == 1, f"got {rc}")
    for rule in ("ticket-id", "email-address", "ipv4-address"):
        check(f"stdin: reports {rule}", rule in out, out)
    check("stdin: reports the line number", "line 2" in out, out)
    check("stdin: never echoes the full value", leak not in out, out)

    # The allowlist applies here too — otherwise every PR quoting a
    # documentation CIDR or an example address would block a merge.
    for benign in ("the pod cidr is 10.0.0.0/13", "mail bob@example.com",
                   "peer at 203.0.113.7"):
        rc, _ = run(benign + "\n")
        check(f"stdin: allowlist covers {benign!r}", rc == 0, f"got {rc}")

    # Paste-residue rules must NOT run on prose. A curly apostrophe in a
    # review comment is an apostrophe; in a corpus file it is a signal.
    # This fired on real review comments before the split.
    rc, out = run("it\u2019s fine \u2014 see <@U01ABCDEF>, ping @here\n")
    check("stdin: paste-residue rules are skipped on PR text",
          rc == 0 and "smart-quote" not in out and "chat-mention" not in out, out)

    # ... while an identifier in that same prose still reports.
    rc, out = run("it\u2019s at 10.16.4.7\n")
    check("stdin: identifier rules still apply to prose",
          rc == 1 and "ipv4-address" in out, out)

    # warn-only: annotate, but do not fail. A comment cannot be un-posted,
    # and a permanently-red check gets ignored rather than fixed.
    buf = io.StringIO()
    allow = hygiene.load_allowlist(hygiene.DEFAULT_ALLOWLIST)
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
        rc = hygiene.scan_stdin(f"ping {leak}\n", "comments", allow, True,
                                warn_only=True)
    out = buf.getvalue()
    check("stdin --warn-only exits 0 on findings", rc == 0, f"got {rc}")
    check("stdin --warn-only still annotates, as a warning",
          "::warning::" in out and "email-address" in out, out)


def verify_warn_vs_block(tmp: Path) -> None:
    """PII warns and exits 0; a credential shape blocks and exits 1.

    This is the contract that decides whether the repo can merge, so it
    gets its own check rather than riding on the e2e counts. The split
    is by FALSE-POSITIVE RATE: an AKIA followed by exactly 16 uppercase
    alphanumerics is a credential, never a coincidence, so blocking it
    is safe. An IP or an email has legitimate look-alikes, so it reports
    and lets a human judge instead of stopping the queue.
    """
    root = tmp / "tier"
    allowlist = write(root / "allow.txt", "# empty\n")

    def run(*paths: Path, strict: bool = False) -> tuple[int, str]:
        buf = io.StringIO()
        argv = [str(p) for p in paths] + ["--allowlist", str(allowlist)]
        if strict:
            argv.append("--strict")
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
            rc = hygiene.main(argv)
        return rc, buf.getvalue()

    pii = write(root / "pii" / "notes.md",
                "reach ops@acmecloud.io about node 10.16.4.7\n")
    rc, out = run(pii.parent)
    check("PII alone does not block the merge", rc == 0, f"got {rc}")
    check("PII is still reported, as a warning",
          "[warning]" in out and "email-address" in out, out)
    check("a warning is never emitted as an error", "[error]" not in out, out)

    # --strict is the escape hatch for anyone who wants the old behavior.
    rc, _ = run(pii.parent, strict=True)
    check("--strict makes a warning blocking", rc == 1, f"got {rc}")

    for rule, planted in (
        ("aws-access-key-id", "AKIAIOSFODNN7EXAMPLE"),
        ("anthropic-api-key", "sk-ant-api03-notarealkey"),
        ("github-token", "ghp_" + "a" * 22),
        ("pem-header", "-----BEGIN RSA PRIVATE KEY-----"),
    ):
        cred = write(root / rule / "leak.md", f"{planted}\n")
        rc, out = run(cred.parent)
        check(f"{rule} BLOCKS without --strict", rc == 1, f"got {rc}")
        check(f"{rule} is emitted as an error", "[error]" in out, out)
        check(f"{rule} never echoes the planted value", planted not in out, out)

    # Mixed: one credential among several warnings still blocks, and the
    # warnings are still reported rather than swallowed by the failure.
    mixed = write(root / "mixed" / "both.md",
                  "ops@acmecloud.io\nAKIAIOSFODNN7EXAMPLE\nnode 10.16.4.7\n")
    rc, out = run(mixed.parent)
    check("one credential among warnings blocks", rc == 1, f"got {rc}")
    check("the warnings alongside it are still reported",
          "[warning]" in out and "[error]" in out, out)


def verify_config_fails_closed(tmp: Path) -> None:
    """A sidecar that cannot be loaded is exit 2, never an empty ruleset."""
    for kind, loader in (("allowlist", hygiene.load_allowlist),):
        missing = tmp / "nope" / f"{kind}.txt"
        try:
            loader(missing)
            check(f"missing default {kind} is a ConfigError", False, "loaded as empty")
        except hygiene.ConfigError:
            check(f"missing default {kind} is a ConfigError", True)

        bad = write(tmp / f"bad-{kind}.txt", "placeholder")
        bad.write_bytes(b"\xff\xfe\x00not utf-8")
        try:
            loader(bad)
            check(f"non-UTF-8 {kind} is a ConfigError", False, "accepted")
        except hygiene.ConfigError:
            check(f"non-UTF-8 {kind} is a ConfigError", True)
        except UnicodeDecodeError as exc:
            check(f"non-UTF-8 {kind} is a ConfigError", False, f"raw {exc!r}")

    if os.geteuid() != 0:  # root can read a 0o000 file, so this proves nothing
        locked = write(tmp / "locked.txt", "x\n")
        os.chmod(locked, 0o000)
        try:
            hygiene.load_allowlist(locked)
            check("unreadable allowlist is a ConfigError", False, "accepted")
        except hygiene.ConfigError:
            check("unreadable allowlist is a ConfigError", True)
        except OSError as exc:
            check("unreadable allowlist is a ConfigError", False, f"raw {exc!r}")
        finally:
            os.chmod(locked, 0o600)

    # Whole-run behavior: a bad sidecar is exit 2, not exit 0/1.
    corpus = write(tmp / "cfg" / "ok.jsonl", '{"query": "clean"}\n').parent
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        rc = hygiene.main([str(corpus), "--allowlist", str(tmp / "nope" / "a.txt")])
    check("missing sidecar makes the whole run exit 2", rc == 2, f"got {rc}")


def verify_rule_shapes() -> None:
    """Unit-level guards for rules whose shape has bitten us before."""
    rules = dict(hygiene.RULES)

    url = rules["console-url-with-org-id"]
    for text in (
        "https://console.coreweave.com/orgs/acme",
        "https://cloud.coreweave.com/?org_id=abc123",
        "https://coreweave.com/accounts/12ab",
        "https://a.b.coreweave.com:8443/tenants/xy",
    ):
        check(f"console URL rule matches {text}", bool(url.search(text)))
    for text in (
        "https://fakecoreweave.com/orgs/acme",
        "https://coreweave.com.evil.example/orgs/acme",
        "https://notcoreweave.com/accounts/12ab",
        "https://console.coreweave.com.attacker.test/orgs/acme",
    ):
        check(f"console URL rule does NOT match {text}", not url.search(text))

    # The jwt rule deliberately reaches TWO segments as well as three: an
    # alg=none token is `header.payload.` with an empty signature, and a
    # truncated log paste keeps `header.payload`. Requiring two
    # separators would miss both.
    jwt = rules["jwt"]
    for text in (
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.sIgNaTuRe",
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0",
        "eyJhbGciOiJub25lIn0.eyJzdWIiOiIxIn0.",
    ):
        check(f"jwt rule matches {text[:28]}...", bool(jwt.search(text)))
    check("jwt rule ignores the bare prefix in prose",
          not jwt.search("a JWT starts with eyJ and then some"))

    # Ticket IDs: standalone tokens only, letters-only project key.
    check("name candidates split a fused ticket ID out of a long name",
          "PROJ-1234" in set(hygiene._name_candidates("a/PROJ-1234-repro.jsonl")))
    check("name candidates leave benign compounds benign",
          not any(rules["ticket-id"].search(c)
                  for c in hygiene._name_candidates("gd-8xh100ib-i128-sizing.jsonl")))

    ticket = rules["ticket-id"]
    for text in ("see PROJ-1234", "and APPSEC-3971 as well"):
        check(f"ticket rule matches {text!r}", bool(ticket.search(text)))
    for text in ("A100-80", "US-EAST-04A", "gd-8xh100ib-i128", "IEEE-754",
                 "SOC-2", "GPT-4", "FIPS-140", "COVID-19"):
        check(f"ticket rule does NOT match {text!r}", not ticket.search(text))

    # The ticket rule requires an UPPERCASE project key, in contents and
    # in names alike. `word-number` is simply how this product talks —
    # instance types, model versions, fixture names — so a
    # case-insensitive rule red-gates ordinary vocabulary.
    name_ticket = ticket
    for text in ("PROJ-1234", "AB-7", "APPSEC-3971"):
        check(f"ticket rule matches {text!r}", bool(name_ticket.search(text)))
    for text in ("case-1", "batch-2", "gpu-8", "shard-3", "run-12", "step-4"):
        check(f"ticket rule does NOT match {text!r}",
              not name_ticket.search(text))
    check("ticket rule still excludes the standards vocabulary",
          not any(name_ticket.search(t) for t in ("IEEE-754", "SOC-2", "TLS-1")))
    # Measured in the shipped dist/ tree: cpu-4 x16, Llama-3 x8,
    # TinyLlama-1 x2, and ZERO real ticket IDs. Every one of these was a
    # blocking finding under the old case-insensitive rule.
    for text in ("cpu-4", "Llama-3", "TinyLlama-1", "Qwen2.5-7B"):
        check(f"shipped-content vocabulary {text!r} is not ticket-shaped",
              not name_ticket.search(text))
    check("the documented cost is real: a lowercase paste is NOT caught",
          not name_ticket.search("proj-1234"))

    # ---- paste-residue rules (Tier 1) --------------------------------
    # Each character class was verified to occur ZERO times across
    # evals/, dist/, plugins/, skills/ and _snippets/ before being made
    # blocking. The NEGATIVES are the load-bearing half: em-dash and
    # ellipsis appear in 44 and 12 files, so flagging either would make
    # the gate unusable and get it switched off.
    invisible = rules["invisible-character"]
    for name, ch in (("NBSP", "\u00a0"), ("zero-width space", "\u200b"),
                     ("ZWNJ", "\u200c"), ("ZWJ", "\u200d"),
                     ("word joiner", "\u2060"), ("BOM in body", "\ufeff"),
                     ("soft hyphen", "\u00ad"), ("narrow NBSP", "\u202f")):
        check(f"invisible-character catches {name}",
              bool(invisible.search(f"spin up{ch}a cluster")))
    for name, ch in (("em-dash", "\u2014"), ("ellipsis", "\u2026"),
                     ("en-dash", "\u2013"), ("ordinary space", " ")):
        check(f"invisible-character does NOT flag {name} (used repo-wide)",
              not invisible.search(f"spin up{ch}a cluster"))
    check("invisible findings report a code point, not an empty redaction",
          "U+00A0" in hygiene.scan_line(Path("x"), 1, "a\u00a0b")[0].message)

    quote = rules["smart-quote"]
    for ch in ("\u2018", "\u2019", "\u201c", "\u201d"):
        check(f"smart-quote catches U+{ord(ch):04X}",
              bool(quote.search(f"the {ch}cluster")))
    for ch in ("'", chr(34), "`"):
        check(f"smart-quote does NOT flag ASCII {ch!r}",
              not quote.search(f"the {ch}cluster"))

    mention = rules["chat-mention"]
    for text in ("<@U01ABCDEF>", "<#C01ABCDEF|infra>", "ping @here", "@channel please"):
        check(f"chat-mention catches {text!r}", bool(mention.search(text)))
    for text in ("email me at a@b.com", "the @ sign", "see user@host",
                 "https://example.com/@handle"):
        check(f"chat-mention does NOT flag {text!r}", not mention.search(text))

    quoted = rules["quoted-reply-header"]
    for text in ("On Tue, Jan 6, 2026 at 3:14 PM, Someone wrote:",
                 "Sent from my iPhone",
                 "[10:32 AM] and then it failed",
                 "10:32:05 AM the pod restarted"):
        check(f"quoted-reply-header catches {text!r}", bool(quoted.search(text)))
    for text in ("the job wrote: three files", "restart at 10:32 UTC",
                 "scale to 24 nodes"):
        check(f"quoted-reply-header does NOT flag {text!r}", not quoted.search(text))

    # ---- allowlist discriminators ------------------------------------
    # A private network written as CIDR is reference-architecture
    # documentation; a bare private HOST address is what a real customer
    # node looks like. The `/mask` is the entire distinction, so both
    # directions are asserted — a one-character widening of that entry
    # would silently stop flagging customer node IPs.
    allow = hygiene.load_allowlist(hygiene.DEFAULT_ALLOWLIST)
    ip = rules["ipv4-address"]

    def suppressed(line: str) -> bool:
        found = hygiene.scan_line(Path("x"), 1, line)
        return bool(found) and all(hygiene.is_allowed(line, f, allow) for f in found)

    for text in ("pod cidr 10.0.0.0/13", "service cidr 10.16.0.0/22",
                 "192.168.1.0/24", "172.16.0.0/12", "at 169.254.169.254"):
        check(f"allowlist covers documentation network {text!r}", suppressed(text))
    for text in ("the node came up at 10.16.4.7", "ssh 192.168.1.44"):
        check(f"a bare private HOST address still fires: {text!r}",
              bool(ip.search(text)) and not suppressed(text))
    # A genuinely public, non-reserved address: 203.0.113.x would prove
    # nothing here now, since the RFC 5737 documentation entry covers it
    # in its own right.
    # CoreWeave's own project keys are allowlisted so a maintainer note
    # ("implements <OURKEY>-3972") is legal, while a key that could be a
    # customer's still fires. The rule exists for transcript pastes.
    check("our own project keys are allowlisted in maintainer notes",
          suppressed("the size-scaled confirmation gate (APPSEC-3972)"))
    check("a project key that is NOT ours still fires",
          not suppressed("see CUSTOMER-3972 for context"))

    check("a public quad with a mask is NOT covered by the CIDR entry",
          not suppressed("peer 104.18.32.7/32"))

    # Every benign name that regressed, checked at the candidate level too,
    # so a failure points at the rule rather than only at the e2e counts.
    for name in ("case-1.jsonl", "batch-2.jsonl", "gpu-8-node.jsonl",
                 "shard-3-of-8.jsonl"):
        check(f"no name candidate of {name!r} is ticket-shaped",
              not any(name_ticket.search(c) for c in hygiene._name_candidates(name)))
    check("a fused uppercase ticket ID is still found in a name candidate",
          any(name_ticket.search(c)
              for c in hygiene._name_candidates("a/APPSEC-3971-repro.jsonl")))


def verify_annotation_escaping() -> None:
    """Workflow-command PROPERTIES need ':' and ',' escaped, not just '%'."""
    escaped = hygiene._gha_escape_property("evals/we,ird:name\nv2.jsonl")
    for ch, why in ((",", "ends the property list"), (":", "ends the key"),
                    ("\n", "ends the command")):
        check(
            f"property escaping removes {ch!r} ({why})",
            ch not in escaped,
            f"got {escaped!r}",
        )
    check("property escaping keeps the path readable",
          escaped == "evals/we%2Cird%3Aname%0Av2.jsonl", f"got {escaped!r}")

    finding = hygiene.Finding(
        Path("evals/we,ird:name.jsonl"), 3, 5, "email-address",
        "matched: ab***", (0, 5), "x",
    )
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        hygiene.emit(finding, github=True, blocking=True)
    line = buf.getvalue().strip()
    head = line.split("::", 2)[1]  # the "error file=...,line=3,col=5" part
    check(
        "emitted annotation keeps exactly the intended properties",
        head.count(",") == 2 and head.count("file=") == 1 and head.endswith("col=5"),
        f"got {head!r}",
    )


def verify_config_error_annotation_escaping(tmp: Path) -> None:
    """The exit-2 annotation escapes its file= property, like emit() does.

    A file the scanner cannot decode is announced through its own
    ``::error file=...`` line rather than through emit(), so it needs the
    same PROPERTY escaping. Unescaped, a corpus file named ``a,b:c.bin``
    truncates the annotation's metadata — GitHub attaches the error to
    the wrong file, or drops it — and a name containing CR/LF could close
    the workflow command and inject a second one. This case fails if
    that branch is reverted to the DATA escape.
    """
    corpus = tmp / "cfgerr" / "corpus"
    corpus.mkdir(parents=True, exist_ok=True)
    # Undecodable (NUL bytes), with ',' and ':' in the name. Both are
    # legal POSIX filename characters and both are workflow-command
    # metacharacters.
    (corpus / "we,ird:name.bin").write_bytes(b"\x00\xffnot text")
    allowlist = write(tmp / "cfgerr" / "allow.txt", "# empty\n")

    rc, annotations = run_scanner(corpus, allowlist)
    check("undecodable file makes the run exit 2", rc == 2, f"got {rc}")
    check("undecodable file emits exactly one annotation",
          len(annotations) == 1, str(annotations))
    if not annotations:
        return
    # "::error file=<escaped>::<message>" -> properties are part 1.
    properties = annotations[0].split("::", 2)[1]
    for ch, why in ((",", "ends the property list"), (":", "ends the key")):
        check(
            f"config-error annotation escapes {ch!r} in the file name ({why})",
            ch not in properties,
            f"got {properties!r}",
        )
    check(
        "config-error annotation still names the file, escaped",
        properties.startswith("error file=")
        and properties.endswith("we%2Cird%3Aname.bin"),
        f"got {properties!r}",
    )


def verify_skip_dirnames_are_gitignored() -> None:
    """The 'keep in sync with .gitignore' comment, made checkable.

    SKIP_DIRNAMES is the gate's only blanket exemption, and its
    justification is that none of those directories can be committed.
    That is a claim about .gitignore, so assert it instead of asserting
    it in a comment. '.git' is exempt from the assertion: git never
    tracks its own directory and no ignore rule can name it.
    """
    gitignore = REPO_ROOT / ".gitignore"
    if not gitignore.is_file():  # source export without the ignore file
        return
    ignored = {
        line.strip().rstrip("/").lstrip("/")
        for line in gitignore.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    }
    missing = sorted((hygiene.SKIP_DIRNAMES - {".git"}) - ignored)
    check(
        "every SKIP_DIRNAMES entry except .git is in .gitignore",
        not missing,
        f"not ignored, so they could be committed and would still be "
        f"exempt from the gate: {missing}",
    )
    check(
        ".DS_Store is gitignored as well as name-exempt",
        ".DS_Store" in ignored,
    )


def main() -> int:
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        verify_end_to_end(tmp)
        verify_config_fails_closed(tmp)
        verify_rule_shapes()
        verify_stdin_mode()
        verify_warn_vs_block(tmp)
        verify_annotation_escaping()
        verify_config_error_annotation_escaping(tmp)
        verify_skip_dirnames_are_gitignored()

    if failures:
        print(f"{len(failures)} of {checks} hygiene-scanner check(s) FAILED:", file=sys.stderr)
        for failure in failures:
            print(f"  - {failure}", file=sys.stderr)
        print(
            "\nThe scanner in evals/check_eval_hygiene.py no longer holds a "
            "property this battery guards. Do not relax the check to match "
            "the code without re-reading why the case exists.",
            file=sys.stderr,
        )
        return 1
    print(f"✓ {checks} hygiene-scanner check(s) passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
