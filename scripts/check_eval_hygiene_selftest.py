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
        '{"query": "FIPS-140, NIST-800, TLS-1, SHA-256, UTF-8, COVID-19"}\n',
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
            rc = hygiene.main([str(corpus), "--allowlist", str(allowlist)])
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
    for text in ("see PROJ-1234", "lowercase proj-1234 too"):
        check(f"ticket rule matches {text!r}", bool(ticket.search(text)))
    for text in ("A100-80", "US-EAST-04A", "gd-8xh100ib-i128", "IEEE-754",
                 "SOC-2", "GPT-4", "FIPS-140", "COVID-19"):
        check(f"ticket rule does NOT match {text!r}", not ticket.search(text))

    # The NAME variant of the ticket rule is deliberately stricter: an
    # uppercase project key. Splitting a path on hyphens manufactures
    # `word-number` candidates out of ordinary fixture names, so the
    # case-insensitive content rule made benign names blocking findings.
    name_rules = dict(hygiene.NAME_RULES)
    name_ticket = name_rules["ticket-id"]
    for text in ("PROJ-1234", "AB-7", "APPSEC-3971"):
        check(f"name ticket rule matches {text!r}", bool(name_ticket.search(text)))
    for text in ("case-1", "batch-2", "gpu-8", "shard-3", "run-12", "step-4"):
        check(f"name ticket rule does NOT match {text!r}",
              not name_ticket.search(text))
    check("name ticket rule still excludes the standards vocabulary",
          not any(name_ticket.search(t) for t in ("IEEE-754", "SOC-2", "TLS-1")))
    check("the content ticket rule stays case-insensitive",
          bool(ticket.search("proj-1234")) and not name_ticket.search("proj-1234"))
    check("NAME_RULES differs from RULES in ticket-id only",
          [n for n, p in hygiene.NAME_RULES if p is not dict(hygiene.RULES)[n]]
          == ["ticket-id"])

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
        hygiene.emit(finding, github=True)
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
