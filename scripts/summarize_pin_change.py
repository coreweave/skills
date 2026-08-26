#!/usr/bin/env python3
"""Do the mechanical half of reviewing a pin bump, and say what is left.

WHY THIS EXISTS
---------------
CONTRIBUTING.md ("Reviewing a Renovate pin PR") asks the reviewer to open the
upstream diff and answer concrete questions: does anything new run during
`terraform apply`, did a chart change its RBAC or default image, is the s5cmd
release still the CoreWeave fork. Those questions are answerable but tedious,
and a tedious check performed weekly is a check that eventually gets skipped —
which is how a pin bump ends up approved on its version number alone.

So this script fetches what the reviewer would have fetched, greps it for the
patterns those questions are really asking about, and posts the result on the
PR. It does NOT decide anything. Every finding is "look at this", never "this
is bad", and the script exits 0 even when it flags something.

WHY IT IS NOT A GATE
--------------------
A grep over an upstream diff produces false positives by construction: a
`local-exec` added to an example, an image tag bumped in a values comment. If
those failed the build, the fix would be to stop reading the output. Advisory
keeps the signal honest — CI still blocks the PR for stale `dist/` and pin
disagreement, which are decidable; this only makes the undecidable part cheaper
to judge.

WHAT IT COVERS
--------------
    reference-architecture   commit range old..new: commits, changed files, and
                             a grep for provisioners, changed module sources,
                             new providers, and credential/network reads.
    Helm charts              downloads both chart versions and diffs them:
                             appVersion, template files added/removed/changed,
                             and a grep for RBAC, image tags, and privilege.
    s5cmd                    confirms the release exists on the CoreWeave fork
                             and still publishes the asset naming and the
                             checksums file the skill depends on.

Uses PyYAML (already a build dependency; the CoreWeave chart index nests
`dependencies:` blocks with their own `version:` keys, so hand-parsing it is a
trap). Network access to github.com and the CoreWeave chart repo. A
GITHUB_TOKEN in the environment raises the API rate limit and is required for
private repos; without one the script degrades to public, unauthenticated
requests and says so rather than failing.

Run locally with:

    python scripts/summarize_pin_change.py --base origin/main --head HEAD
"""

from __future__ import annotations

import argparse
import difflib
import io
import json
import os
import re
import subprocess
import sys
import tarfile
import urllib.error
import urllib.request
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent

REF_ARCH_REPO = "coreweave/reference-architecture"
S5CMD_REPO = "coreweave/s5cmd"
CHART_REPO = "https://charts.core-services.ingress.coreweave.com"

SNIPPET = "_snippets/coreweave-cks.md"
INFERENCE_BODY = "skills/cw-self-managed-inference/body.md"
S5CMD_REF = "skills/cw-load-model-to-bucket/references/s3-client-setup.md"

SHA_RE = re.compile(r"CW_REF_ARCH_SHA=([a-f0-9]{40})")
S5CMD_RE = re.compile(r"S5CMD_VERSION=([0-9]+\.[0-9]+\.[0-9]+-[a-f0-9]+)")
HELM_RE = re.compile(r"coreweave/(cert-manager|traefik)[\s\S]{0,200}?--version (\d+\.\d+\.\d+)")

# (label, pattern, why it matters). Deliberately broad: this is a "look here"
# list, not a verdict. Each maps to a question in CONTRIBUTING.md.
TERRAFORM_SIGNALS = [
    ("provisioner / local-exec", r"\b(local-exec|remote-exec|provisioner)\b",
     "runs a command on apply, under the customer's credentials"),
    ("module source changed", r"^\+\s*source\s*=",
     "pulls Terraform from somewhere new"),
    ("provider added/changed", r"^\+\s*(provider\s+\"|required_providers)",
     "a new provider can reach a new API"),
    ("credential / secret read", r"(?i)^\+.*\b(aws_access|secret_key|token|credential|\.aws/|kubeconfig)\b",
     "reads credentials it did not read before"),
    ("network fetch", r"(?i)^\+.*\b(curl|wget|http_get|data\s+\"http)\b",
     "fetches something at apply time"),
]

# Matched against ADDED diff lines only, so every anchored pattern here has to
# account for the leading `+`. `^\s*` silently matches nothing on a `+`-prefixed
# line — that is how the `image` signal sat dead: three lines that should each
# have fired scored zero. Anchor on `^\+` or leave the pattern unanchored.
CHART_SIGNALS = [
    ("RBAC", r"(?i)(ClusterRole|RoleBinding|rules:|apiGroups)",
     "changes what the release may do in the cluster"),
    ("image", r"(?i)^\+\s*(image|tag|repository):",
     "changes what actually gets run"),
    ("privilege", r"(?i)(privileged|hostNetwork|hostPID|runAsUser|securityContext|serviceAccount)",
     "changes the security posture of the pods"),
    # The signal this repo needs most and had least. Every customer-facing
    # warning in cw-self-managed-inference is about the public IP a Traefik
    # LoadBalancer allocates and bills by the minute, so a bump that adds or
    # retypes a Service is exactly what a reviewer must not miss.
    #
    # traefik 1.36.0 -> 1.37.0 is the worked example: the wrapper's values.yaml
    # had `type:`/`externalTrafficPolicy:` one level above where upstream reads
    # them, so they were silently ignored and `traefik-k8s` rendered ClusterIP.
    # 1.37.0 moved them under `spec:` and the Service became a LoadBalancer for
    # real. Nothing in the three signals above matches `type: LoadBalancer`, so
    # the only behavioral change in the whole bump went unflagged while a
    # docstring containing the word "privileged" scored three hits.
    ("exposure / IP allocation",
     r"(?i)^\+.*(type:\s*LoadBalancer|externalTrafficPolicy|loadBalancerClass"
     r"|coreweave-load-balancer-type|nodePort|hostPort)",
     "allocates a load balancer or changes what is reachable from outside the "
     "cluster — check whether it adds a BILLED public IP"),
]


def sh(*args: str) -> str:
    return subprocess.run(args, capture_output=True, text=True,
                          cwd=REPO_ROOT).stdout


def api(url: str) -> dict | list | None:
    """GitHub API GET. Returns None on any failure — callers degrade."""
    req = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": "cw-skills-pin-review",
    })
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            return json.load(resp)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError):
        return None


def fetch(url: str) -> bytes | None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "cw-skills-pin-review"})
        with urllib.request.urlopen(req, timeout=90) as resp:
            return resp.read()
    except Exception:  # noqa: BLE001 — any failure degrades to "could not check"
        return None


def file_at(ref: str, path: str) -> str:
    return sh("git", "show", f"{ref}:{path}")


def scan(text: str, signals: list) -> list[str]:
    hits = []
    for label, pattern, why in signals:
        n = len(re.findall(pattern, text, re.MULTILINE))
        if n:
            hits.append(f"**{label}** ({n}) — {why}")
    return hits


def report_ref_arch(old: str, new: str) -> str:
    """Commit range old..new, plus a grep of the combined patch."""
    out = [f"### `{REF_ARCH_REPO}` — commit pin moved",
           "",
           f"`{old[:12]}` → `{new[:12]}`",
           "",
           f"[**Read the upstream diff**](https://github.com/{REF_ARCH_REPO}/compare/{old}...{new})"]

    cmp = api(f"https://api.github.com/repos/{REF_ARCH_REPO}/compare/{old}...{new}")
    if not cmp:
        out += ["", "> Could not reach the GitHub API to summarize this range "
                    "(no token, rate limit, or private repo). Open the compare "
                    "link above and review it by hand."]
        return "\n".join(out)

    commits = cmp.get("commits") or []
    files = cmp.get("files") or []
    out += ["", f"**{len(commits)} commit(s), {len(files)} file(s) changed.**", ""]

    if commits:
        out += ["<details><summary>Commits</summary>", ""]
        for c in commits[:40]:
            msg = (c.get("commit", {}).get("message") or "").split("\n")[0][:100]
            out.append(f"- `{c['sha'][:8]}` {msg}")
        if len(commits) > 40:
            out.append(f"- …and {len(commits) - 40} more")
        out += ["", "</details>", ""]

    # Which of the directories the skills actually use were touched?
    used = {"terraform/": [], "inference/": [], "other": []}
    for f in files:
        p = f.get("filename", "")
        key = next((k for k in ("terraform/", "inference/") if p.startswith(k)), "other")
        used[key].append(p)
    touched = [f"`{k}` ({len(v)})" for k, v in used.items() if v and k != "other"]
    out.append("**Directories the skills consume:** "
               + (", ".join(touched) if touched else "none touched — "
                  "this range does not change what the skills run"))

    # Scan the directories the skills actually consume SEPARATELY from the rest.
    # reference-architecture is a big repo; most of it (kafka, ray, skypilot) is
    # never fetched by a skill. A `local-exec` added under kafka-local-nvme/ is
    # not a finding for us, and reporting it next to a real one trains the
    # reviewer to discount both.
    consumed_patch = "\n".join(
        f.get("patch", "") or "" for f in files
        if f.get("filename", "").startswith(("terraform/", "inference/")))
    other_patch = "\n".join(
        f.get("patch", "") or "" for f in files
        if not f.get("filename", "").startswith(("terraform/", "inference/")))

    out += ["", "**Flagged in what the skills run** (`terraform/`, `inference/`):", ""]
    hits = scan(consumed_patch, TERRAFORM_SIGNALS) if consumed_patch else []
    out += [f"- {h}" for h in hits] if hits else [
        "- nothing" + ("" if consumed_patch else " — this range does not touch those directories")]

    elsewhere = scan(other_patch, TERRAFORM_SIGNALS)
    if elsewhere:
        out += ["", "<details><summary>Also matched elsewhere in the repo "
                    "(not fetched by any skill — informational)</summary>", ""]
        out += [f"- {h}" for h in elsewhere]
        out += ["", "</details>"]
    return "\n".join(out)


def chart_versions(name: str) -> dict:
    """name -> {version: index entry} from the chart repo index."""
    raw = fetch(f"{CHART_REPO}/index.yaml")
    if not raw:
        return {}
    try:
        index = yaml.safe_load(raw)
    except yaml.YAMLError:
        return {}
    return {e["version"]: e for e in (index.get("entries") or {}).get(name, [])
            if isinstance(e, dict) and e.get("version")}


def subchart_lines(entry: dict) -> list[str]:
    """The wrapper chart's `dependencies:` — where the real upstream lives.

    Every CoreWeave chart here wraps an upstream chart (traefik wraps
    traefik/traefik, and so on). The wrapper's own templates barely move; what
    actually changes under a customer is the pinned sub-chart version, so that
    is the line worth putting in front of a reviewer.
    """
    out = []
    for d in entry.get("dependencies") or []:
        if isinstance(d, dict) and d.get("name"):
            out.append(f"{d['name']}@{d.get('version', '?')} ({d.get('repository', '?')})")
    return out


def chart_templates(entry: dict) -> dict[str, str]:
    """Download a chart .tgz and return {path: content} for its templates."""
    for url in entry.get("urls", []):
        if not url.startswith("http"):
            url = f"{CHART_REPO}/{url.lstrip('/')}"
        blob = fetch(url)
        if not blob:
            continue
        try:
            with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tf:
                return {
                    m.name.split("/", 1)[-1]: tf.extractfile(m).read().decode("utf-8", "replace")
                    for m in tf.getmembers()
                    if m.isfile() and m.size < 512_000
                    and not m.name.endswith((".tgz", ".tar.gz", ".png", ".jpg"))
                }
        except (tarfile.TarError, OSError):
            continue
    return {}


def report_chart(name: str, old: str, new: str) -> str:
    out = [f"### `{name}` Helm chart — version pin moved", "", f"`{old}` → `{new}`", ""]
    index = chart_versions(name)
    if not index:
        out.append("> Could not read the chart index. Check by hand: "
                   f"`helm search repo coreweave/{name} --versions`")
        return "\n".join(out)
    if new not in index:
        out.append(f"> ⚠ **`{new}` is not in the chart repo index.** The install "
                   f"would fail. Available: {', '.join(sorted(index)[-5:])}")
        return "\n".join(out)

    a, b = index.get(old, {}), index[new]
    if a.get("appVersion") or b.get("appVersion"):
        same = a.get("appVersion") == b.get("appVersion")
        out.append(f"- appVersion: `{a.get('appVersion', '?')}` → `{b.get('appVersion', '?')}`"
                   + ("  *(unchanged)*" if same else "  ← **the application itself moved**"))

    # The wrapper's sub-chart pin is the real upstream movement.
    sa, sb = subchart_lines(a) if a else [], subchart_lines(b)
    if sb:
        if set(sa) != set(sb):
            out += ["- **vendored sub-chart changed** — this is what actually moves "
                    "under the customer:"]
            for line in sorted(set(sb) - set(sa)):
                out.append(f"  - now `{line}`")
            for line in sorted(set(sa) - set(sb)):
                out.append(f"  - was `{line}`")
        else:
            out.append(f"- vendored sub-chart unchanged (`{', '.join(sb)}`)")

    ta, tb = (chart_templates(a) if a else {}), chart_templates(b)
    if not tb:
        out += ["", "> Could not download the chart archive to diff its templates. "
                    "The index data above still applies; diff the templates by hand "
                    f"with `helm pull coreweave/{name} --version {new}` if the "
                    "sub-chart moved."]
        return "\n".join(out)
    if ta:
        added = sorted(set(tb) - set(ta))
        removed = sorted(set(ta) - set(tb))
        changed = sorted(k for k in set(ta) & set(tb) if ta[k] != tb[k])
        out.append(f"- wrapper templates: {len(added)} added, {len(removed)} removed, "
                   f"{len(changed)} changed")
        if added or removed:
            out += ["", "<details><summary>Added / removed files</summary>", ""]
            out += [f"- `+ {p}`" for p in added] + [f"- `- {p}`" for p in removed]
            out += ["", "</details>"]
        # Only lines this bump ADDS. Scanning whole files would count every
        # pre-existing `securityContext:` in the chart and report a number that
        # looks alarming and means nothing.
        delta = []
        for k in changed:
            delta += [l for l in difflib.unified_diff(
                ta[k].splitlines(), tb[k].splitlines(), lineterm="", n=0)
                if l.startswith("+") and not l.startswith("+++")]
        for k in added:
            delta += [f"+{l}" for l in tb[k].splitlines()]
        hits = scan("\n".join(delta), CHART_SIGNALS)
        out.append(f"- lines added by this bump: {len(delta)}")
        out += ["", "**Flagged for a closer look:**", ""]
        out += [f"- {h}" for h in hits] if hits else ["- nothing matched the risk patterns"]
    else:
        out.append(f"- could not fetch `{old}` to compare against; only `{new}` was read")
    return "\n".join(out)


def report_s5cmd(old: str, new: str) -> str:
    out = [f"### `{S5CMD_REPO}` — release pin moved", "", f"`{old}` → `{new}`", "",
           f"[**Read the upstream diff**](https://github.com/{S5CMD_REPO}/compare/v{old}...v{new})", ""]
    rel = api(f"https://api.github.com/repos/{S5CMD_REPO}/releases/tags/v{new}")
    if not rel:
        out.append(f"> Could not confirm release `v{new}` exists on {S5CMD_REPO}. "
                   "Check before merging — the skill downloads this by tag.")
        return "\n".join(out)
    assets = [a.get("name", "") for a in rel.get("assets", [])]
    has_sums = "s5cmd_checksums.txt" in assets
    pattern = [a for a in assets if re.match(rf"s5cmd_{re.escape(new)}_.+\.tar\.gz$", a)]
    out += [
        f"- release `v{new}` exists on **the CoreWeave fork** ({len(assets)} assets)",
        f"- `s5cmd_checksums.txt` published: {'yes' if has_sums else '**NO — the skill verifies against this**'}",
        f"- assets matching `s5cmd_{new}_<OS-ARCH>.tar.gz`: {len(pattern)}"
        + ("" if pattern else "  ← **naming changed; the skill's download would 404**"),
    ]
    return "\n".join(out)


def diff_pins(base: str, head: str) -> list[tuple]:
    """(kind, name, old, new) for every pin that moved between two refs."""
    moved = []

    def one(path, regex, kind, name=None):
        old_t, new_t = file_at(base, path), file_at(head, path)
        o = regex.search(old_t)
        n = regex.search(new_t)
        if o and n and o.group(1) != n.group(1):
            moved.append((kind, name or kind, o.group(1), n.group(1)))

    one(SNIPPET, SHA_RE, "ref-arch")
    one(S5CMD_REF, S5CMD_RE, "s5cmd")

    old_charts = dict(HELM_RE.findall(file_at(base, INFERENCE_BODY)))
    new_charts = dict(HELM_RE.findall(file_at(head, INFERENCE_BODY)))
    for chart, new_v in new_charts.items():
        old_v = old_charts.get(chart)
        if old_v and old_v != new_v:
            moved.append(("chart", chart, old_v, new_v))
    return moved


HUMAN_PART = """
---

### What this check did **not** decide

Everything above is mechanical: what moved, what it touched, and which patterns
appeared. None of it is a verdict. Before approving, you still have to answer:

- Is the new behavior acceptable for a customer running this on **their** cluster
  with **their** credentials?
- Does the skill's surrounding prose still describe what the new version does?
- Does it still work? Run the skill's evals or smoke-test it — a pin bump is a
  behavior change until proven otherwise.

And this PR is red on purpose: Renovate cannot run the build, so `dist/` is
stale. Finish it with `gh pr checkout`, `python build.py`, commit, push. See
CONTRIBUTING.md, "Reviewing a Renovate pin PR".
"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base", default="origin/main")
    ap.add_argument("--head", default="HEAD")
    args = ap.parse_args()

    moved = diff_pins(args.base, args.head)
    if not moved:
        print("NO_PIN_CHANGE")
        return 0

    parts = ["## Pin change review", "",
             "One or more pinned dependencies moved in this PR. The mechanical "
             "half of the review is below; the judgment half is at the end.", ""]
    for kind, name, old, new in moved:
        if kind == "ref-arch":
            parts.append(report_ref_arch(old, new))
        elif kind == "chart":
            parts.append(report_chart(name, old, new))
        elif kind == "s5cmd":
            parts.append(report_s5cmd(old, new))
        parts.append("")
    parts.append(HUMAN_PART)

    if not (os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")):
        parts.append("\n> Ran without a GitHub token, so API-backed sections may "
                     "be incomplete.")
    print("\n".join(parts))
    return 0


if __name__ == "__main__":
    sys.exit(main())
