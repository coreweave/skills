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

# Lines that must stay exempt: the real rubric identifiers.
EXEMPT = [
    '          "key": "a100_ib_constraint_explained",',  # gitleaks:allow -- fixture: this IS the false positive under test
    '          "key": "no_silent_substitution",',
    '        "key": "x2",',  # shortest identifier shape in the corpus
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
_ANT = "sk-" + "ant-api03-Zx8Kq3Lm9Pw2Nv5Ry7Tb4Hc6Jd1"
_AWS = "AKI" + "A3QZ7W2LMXP8RTV4B"
_B64 = "c2VjcmV0LXZhbHVl" + "LWhlcmUtMTIzNDU2Nzg5MA=="
_HEX = "3f9a1c7e5b2d8046" + "af1e9c3b7d520e8a"
_PEM = "-----BEGIN RSA PRIVATE" + " KEY-----"
_ASSIGN = "api_key = Zx8Kq3Lm" + "9Pw2Nv5Ry7Tb4Hc6Jd1Fg0Ss"

MUST_NOT_EXEMPT = [
    # a GitHub PAT sitting in the very field the allowlist covers
    '          "key": "%s",' % _PAT,
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
    '            "acting_key": "AKIAIOSFODNN7EXAMPLE"',
    '            "acting_key": "CWSTAG8EXISTINGKEY02"',
    '            "acting_key": "CWSTAG8EXISTINGKEY01extra"',
    # and the field name must not buy the rest of the line either
    '            "acting_key": "CWSTAG8EXISTINGKEY01", "secret": "%s"' % _HEX,
]

# The one mock acting_key fixture, in the shape the seeds actually write it.
EXEMPT_ACTING_KEY = [
    '            "acting_key": "CWSTAG8EXISTINGKEY01"',
    '            "acting_key": "CWSTAG8EXISTINGKEY01",',
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

    def test_every_committed_rubric_key_line_is_covered(self):
        """The allowlist must actually cover the data, or the check stays red."""
        key_line = re.compile(r'^\s*"key":')
        seen = 0
        for glob in EVAL_GLOBS:
            for path in sorted(ROOT.glob(glob)):
                for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                    if not key_line.match(line):
                        continue
                    seen += 1
                    with self.subTest(file=str(path.relative_to(ROOT)), line=n):
                        self.assertTrue(self.exempt(line), line.strip())
        self.assertGreater(seen, 100, "expected the eval corpus to be present")

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
