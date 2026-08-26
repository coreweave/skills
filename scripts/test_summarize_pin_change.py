"""Unit tests for the pin-change summarizer's risk signals.

These exist because a signal that matches nothing fails SILENTLY. The summary
still posts, still looks thorough, and simply omits the line it should have
printed — so the failure is invisible exactly when it matters. `scan()` is
stdlib-only and pure, so the patterns are cheap to pin down here rather than
discovering the gap on a live pin PR.

Every pattern is checked against a line that SHOULD fire and, where the
distinction matters, one that should not.

Run with the rest of the repo's tests:

    python3 -m unittest discover -s scripts -p 'test_*.py'
"""

from __future__ import annotations

import unittest

from summarize_pin_change import CHART_SIGNALS, TERRAFORM_SIGNALS, scan


def labels(text: str, signals: list) -> set[str]:
    """The set of signal labels that fired, without the counts."""
    return {h.split("**")[1] for h in scan(text, signals)}


class ChartSignalAnchoring(unittest.TestCase):
    """`scan()` is fed diff lines that keep their `+`, so anchors must allow it."""

    def test_image_signal_fires_on_added_lines(self):
        # The regression: `^\s*` cannot match a line starting with `+`, so this
        # signal scored zero on every pin PR ever reviewed.
        delta = "\n".join([
            "+        image: docker.io/traefik:v3.7.10",
            "+  repository: traefik",
            "+    tag: v1.2.3",
        ])
        self.assertIn("image", labels(delta, CHART_SIGNALS))

    def test_every_anchored_chart_pattern_tolerates_the_diff_prefix(self):
        # Guards the whole table against the same mistake, not just `image`.
        for label, pattern, _ in CHART_SIGNALS:
            bare = pattern.replace("(?i)", "")
            if bare.startswith("^"):
                self.assertTrue(
                    bare.startswith("^\\+"),
                    f"{label!r} is anchored at ^ but not ^\\+, so it can never "
                    f"match a `+`-prefixed diff line: {pattern!r}",
                )


class ExposureSignal(unittest.TestCase):
    """The traefik 1.36.0 -> 1.37.0 case, which the original table missed."""

    def test_service_retyped_to_loadbalancer_is_flagged(self):
        delta = "\n".join([
            "+        spec:",
            "+          type: LoadBalancer",
            "+          externalTrafficPolicy: Local",
        ])
        self.assertIn("exposure / IP allocation", labels(delta, CHART_SIGNALS))

    def test_public_loadbalancer_annotation_is_flagged(self):
        delta = "+    service.beta.kubernetes.io/coreweave-load-balancer-type: public"
        self.assertIn("exposure / IP allocation", labels(delta, CHART_SIGNALS))

    def test_nodeport_and_hostport_are_flagged(self):
        for line in ("+      nodePort: 30080", "+        hostPort: 8080"):
            with self.subTest(line=line):
                self.assertIn("exposure / IP allocation",
                              labels(line, CHART_SIGNALS))

    def test_unrelated_change_does_not_fire_it(self):
        # A label bump must stay quiet, or the signal becomes noise and gets
        # skipped like the always-red check this repo already worried about.
        delta = "+    helm.sh/chart: traefik-41.2.0"
        self.assertNotIn("exposure / IP allocation",
                         labels(delta, CHART_SIGNALS))

    def test_prose_mentioning_loadbalancer_does_not_fire_it(self):
        # The `privilege` signal's false positive on this same bump came from a
        # docstring. Require the YAML key shape, not the bare word.
        delta = "+  # -- Whether the LoadBalancer should be public."
        self.assertNotIn("exposure / IP allocation",
                         labels(delta, CHART_SIGNALS))


class SignalTableHygiene(unittest.TestCase):

    def test_labels_are_unique(self):
        for name, table in (("CHART_SIGNALS", CHART_SIGNALS),
                            ("TERRAFORM_SIGNALS", TERRAFORM_SIGNALS)):
            with self.subTest(table=name):
                found = [label for label, _, _ in table]
                self.assertEqual(len(found), len(set(found)),
                                 f"duplicate label in {name}: {found}")

    def test_every_pattern_compiles(self):
        import re
        for name, table in (("CHART_SIGNALS", CHART_SIGNALS),
                            ("TERRAFORM_SIGNALS", TERRAFORM_SIGNALS)):
            for label, pattern, why in table:
                with self.subTest(table=name, label=label):
                    re.compile(pattern)
                    self.assertTrue(why, f"{label} has no explanation")


if __name__ == "__main__":
    unittest.main()
