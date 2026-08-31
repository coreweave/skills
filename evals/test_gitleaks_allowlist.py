"""Lock the shape of the .gitleaks.toml allowlist.

eval-hygiene.yml runs gitleaks over the whole repo as a blocking check. The
one allowlist in .gitleaks.toml exists for a false positive in eval DATA -- the
`key` field of rubric_criteria[], whose snake_case identifiers trip the
generic-api-key rule on entropy -- and the danger of any allowlist is that a
later edit widens it until the scanner stops seeing real credentials.

These tests are the guardrail. They run under the same
`python3 -m unittest discover -s evals` step as the validator tests, need only
stdlib, and require no Docker: they re-implement nothing about gitleaks except
the allowlist predicate, and check that predicate against the shapes that were
verified against the pinned gitleaks image when the config was written.

If a change here fails, do not loosen the regex to make it pass. Re-run the
scanner against a planted credential first (the recipe is in .gitleaks.toml).
"""

import re
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / ".gitleaks.toml"

# Lines that must stay exempt: the ONE rubric identifier that gitleaks actually
# reports. The other 79 committed identifiers are deliberately NOT exempt --
# they are not findings in the first place (gitleaks' default stopword
# filtering clears them), so exempting them would buy nothing and would mean
# re-introducing a value SHAPE to cover them. See .gitleaks.toml.
EXEMPT = [
    '          "key": "a100_ib_constraint_explained",',  # gitleaks:allow -- fixture: this IS the false positive under test
]

# Identifiers that are committed but must NOT be bought an exemption by shape.
# They are safe because the scanner does not report them, not because the
# allowlist hides them -- so the allowlist must not match them either.
NOT_EXEMPT_AND_NOT_FINDINGS = [
    '          "key": "no_silent_substitution",',
    '        "key": "x2",',
    '  "key": "stopped_before_mutation"',
]

# Lines that must stay VISIBLE to gitleaks. Every shape below was planted in an
# eval file and confirmed still-detected by gitleaks v8.30.1 with this config in
# place, one at a time.
#
# The fixtures are assembled from fragments on purpose. Written out whole they
# are credential-shaped, and this file is inside the tree the scanner walks --
# a test for the secret scanner must not itself be a finding. Splitting the
# recognisable prefix is enough, and is preferable to exempting this path:
# exempting it would mean the one file guaranteed to contain credential shapes
# is the one file nobody scans.
_PAT = "ghp" + "_A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"
# The all-lowercase variant is the one that defeated the shape rule, and the
# reason every other fixture here passed against it: they all carry uppercase
# or lead with a digit, so `^[a-z][a-z0-9]*(_[a-z0-9]+)*$` never matched them.
# This one IS lowercase snake_case, exactly like a rubric id.
_LC_PAT = "ghp" + "_secret_a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6"
_ANT = "sk-" + "ant-api03-Zx8Kq3Lm9Pw2Nv5Ry7Tb4Hc6Jd1"
_AWS = "AKI" + "A3QZ7W2LMXP8RTV4B"
_B64 = "c2VjcmV0LXZhbHVl" + "LWhlcmUtMTIzNDU2Nzg5MA=="
_HEX = "3f9a1c7e5b2d8046" + "af1e9c3b7d520e8a"
_PEM = "-----BEGIN RSA PRIVATE" + " KEY-----"
_ASSIGN = "api_key = Zx8Kq3Lm" + "9Pw2Nv5Ry7Tb4Hc6Jd1Fg0Ss"

# The mock acting_key fixture and its near-misses, split for the same reason:
# written whole they are access-key-shaped, and this file is inside the tree
# both gitleaks and evals/check_eval_hygiene.py walk.
_MOCK_KEY = "CWSTAG8" + "EXISTINGKEY01"
_MOCK_KEY2 = "CWSTAG8" + "EXISTINGKEY02"
_AWS_DOC = "AKI" + "AIOSFODNN7EXAMPLE"

MUST_NOT_EXEMPT = [
    # a GitHub PAT sitting in the very field the allowlist covers
    '          "key": "%s",' % _PAT,
    # the all-lowercase PAT: satisfies the retired shape, so this line is the
    # blind spot itself and fails against any config that reinstates a shape
    '          "key": "%s",' % _LC_PAT,
    # the same rubric identifier upper-cased: shape exempts, not field name
    '          "key": "A100_IB_CONSTRAINT_EXPLAINED",',  # gitleaks:allow -- fixture: this IS the false positive under test
    # an assignment smuggled into the value
    '          "key": "%s",' % _ASSIGN,
    # base64 and hex payloads
    '          "key": "%s",' % _B64,
    '          "key": "%s",' % _HEX,
    # a private key header
    '          "key": "%s",' % _PEM,
    # provider-prefixed keys
    '          "key": "%s",' % _ANT,
    '          "key": "%s",' % _AWS,
    # a credential on a neighbouring field of the same object
    '          "desc": "token %s here",' % _PAT,
    # the field name alone must not buy an exemption for the whole line
    '          "key": "a100_ib", "secret": "%s"' % _HEX,
    # acting_key is exempt by LITERAL, never by shape: a different
    # uppercase-alphanumeric value in the same field is what a real leak
    # looks like, and must stay visible.
    '            "acting_key": "%s"' % _AWS,
    '            "acting_key": "%s"' % _AWS_DOC,
    '            "acting_key": "%s"' % _MOCK_KEY2,
    '            "acting_key": "%sextra"' % _MOCK_KEY,
    # and the field name must not buy the rest of the line either
    '            "acting_key": "%s", "secret": "%s"' % (_MOCK_KEY, _HEX),
]

# The one mock acting_key fixture, in the shape the seeds actually write it.
EXEMPT_ACTING_KEY = [
    '            "acting_key": "%s"' % _MOCK_KEY,
    '            "acting_key": "%s",' % _MOCK_KEY,
]

EVAL_GLOBS = ("skills/*/evals/evals.json", "evals/standalone/*.evals.json")


class GitleaksAllowlistTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = tomllib.loads(CONFIG.read_text(encoding="utf-8"))
        allowlists = cls.config.get("allowlists", [])
        assert len(allowlists) == 1, "expected exactly one allowlist block"
        cls.allowlist = allowlists[0]
        cls.patterns = [re.compile(r) for r in cls.allowlist["regexes"]]

    def exempt(self, line):
        return any(p.search(line) for p in self.patterns)

    def test_default_ruleset_is_extended(self):
        """A config with no rules scans everything and reports nothing."""
        self.assertIs(self.config.get("extend", {}).get("useDefault"), True)

    def test_allowlist_is_line_scoped(self):
        self.assertEqual(self.allowlist.get("regexTarget"), "line")

    def test_allowlist_has_no_path_or_commit_criteria(self):
        """`paths` exempts the whole FILE on v8.30.1, even with condition=AND.

        Verified: paths + regexes + condition="AND" stopped detecting a planted
        GitHub PAT and a planted RSA private key inside the same eval files.
        The path-scoped version reads tighter and is strictly weaker.
        """
        for forbidden in ("paths", "commits", "stopwords"):
            self.assertNotIn(forbidden, self.allowlist)

    def test_real_rubric_identifiers_are_exempt(self):
        for line in EXEMPT:
            with self.subTest(line=line):
                self.assertTrue(self.exempt(line), "should be exempt")

    def test_credential_shapes_are_not_exempt(self):
        for line in MUST_NOT_EXEMPT:
            with self.subTest(line=line):
                self.assertFalse(self.exempt(line), "must stay visible to gitleaks")

    def test_identifiers_that_are_not_findings_are_not_exempt(self):
        """The allowlist covers findings, not the whole data shape.

        The previous revision asserted that EVERY committed `"key":` line was
        exempt. Only a value shape can satisfy that over 80 identifiers, and
        the shape that did -- lowercase snake_case -- is satisfied by real
        credentials too (a <prefix>_<prefix>_<32 hex> PAT). That assertion is
        what forced the blind spot, so it is gone.
        """
        for line in NOT_EXEMPT_AND_NOT_FINDINGS:
            with self.subTest(line=line):
                self.assertFalse(self.exempt(line), "should not need an exemption")

    def test_every_regex_is_a_fully_literal_anchored_pattern(self):
        """No quantifiers and no escapes: an exemption matches exactly one string.

        This is the guard that stops the shape rule coming back. A pattern
        containing `*`, `+`, `{n,}`, `.`, a character class, or a class escape
        like `\\w` can exempt values nobody hand-verified; a pattern whose
        payload is bare literal text between ^ and $ cannot.
        """
        # Outside the leading-whitespace/optional-comma frame, the payload must
        # carry no regex metacharacter and no backslash of any kind.
        frame = re.compile(
            r'^\^\\s\*'          # ^\s*   (gitleaks hands the regex a leading newline)
            r'(?P<body>.*?)'      # the literal payload
            r',\?\\s\*\$$'        # ,?\s*$
        )
        for raw in self.allowlist["regexes"]:
            with self.subTest(regex=raw):
                m = frame.match(raw)
                self.assertIsNotNone(m, "must be framed as ^\\s*...,?\\s*$")
                body = m.group("body")
                for meta in ("*", "+", "?", "[", "]", "(", ")", "{", "}", "|"):
                    self.assertNotIn(meta, body, f"open quantifier {meta!r} in {raw!r}")
                # No backslash at all. Banning the quantifiers is not enough on
                # its own: `^\s*"key": "\w\w\w...",?\s*$` carries none of them
                # and still exempts any same-length lowercase token, which is
                # the very hole this test exists to keep shut. The payload is
                # plain JSON text, so it needs no escape to express -- and a `.`
                # is then covered by the same rule as `\w`.
                self.assertNotIn("\\", body, f"escape sequence in {raw!r}; literals only")

    def test_the_mock_acting_key_fixture_is_exempt(self):
        for line in EXEMPT_ACTING_KEY:
            with self.subTest(line=line):
                self.assertTrue(self.exempt(line), "should be exempt")

    def test_every_committed_acting_key_line_is_covered(self):
        """Same backstop as the rubric keys: the allowlist must cover the
        data that is actually committed, or gitleaks stays red."""
        acting = re.compile(r'^\s*"acting_key":')
        seen = 0
        for glob in EVAL_GLOBS:
            for path in sorted(ROOT.glob(glob)):
                for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                    if not acting.match(line):
                        continue
                    seen += 1
                    with self.subTest(file=str(path.relative_to(ROOT)), line=n):
                        self.assertTrue(self.exempt(line), line.strip())
        self.assertGreater(seen, 0, "expected acting_key assertions in the seeds")


if __name__ == "__main__":
    unittest.main()
