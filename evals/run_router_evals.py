#!/usr/bin/env python3
"""CI gate for the bundle-level trigger evals: a router simulation over the API.

This is the OTHER half of the bundle eval story (see evals/README.md):

- `run_trigger_evals.py` (the session harness) spawns real headless `claude -p`
  sessions. It measures the product end to end — including chain behavior — but
  it needs a Claude Code login, installed plugins, and minutes per sweep, so it
  cannot gate a PR.
- `run_router_evals.py` (this file) asks the model the routing question
  directly: one `messages.create` call per query, with the same evidence the
  production router sees — each shipped skill's `name` + `description` from
  `dist/<name>/SKILL.md`. Cheap, fast, deterministic enough to gate CI.

Router candidates are the skills a customer can actually install. Which dist
skills those are is owned by standalone-skills.yaml (an entry without `plugin:`
is include-only: rendered to dist/ for the eval harness, shipped in no plugin),
read through scripts/check_plugin_parity.py's include_only_skills() so this
runner can never disagree with the build about the shipped set. The plugin
mirrors under plugins/*/skills/ are cross-checked against that answer and any
disagreement refuses to run. Pass --include-unshipped to offer include-only
skills to the router anyway (experiments only).

Scoring is top-1 exact match on `expected_skill`; `expected_skill: null` must
route to "none". Entries may carry optional `id` (string), `required: true`
(JSON boolean; must pass regardless of overall accuracy), and `notes` (string)
fields — all three are type-checked in preflight, because a non-string `id`
becomes a dict key and a coerced `required` silently changes who can veto the
gate. On all three, an explicit `null` means the field is absent (reported as
a warning, since a hand-written null is usually a mistake). Ids must be unique
AND queries must be unique — compared ignoring case and surrounding/repeated
whitespace, because "q" and "q " are one question the router answers twice,
which counts double in accuracy; the string sent to the model is always the
raw query. `expected_chain` (used by the session harness) is tolerated and
ignored — a single forced-choice call cannot measure chaining.

Exit codes:
  0  gate passed (or --dry-run preflight passed)
  1  gate failed (accuracy below --min-accuracy, a required entry failed,
     or a regression against --baseline)
  2  config/env error. Bad corpus, bad labels, packaging drift, an
     out-of-range --min-accuracy, and a missing API key are all caught BEFORE
     any API call; a credential rejection or a malformed request reported by
     the API also lands here mid-run.
  3  transient API errors that persisted through retries

Usage:
  python evals/run_router_evals.py --output results.json
  python evals/run_router_evals.py --votes 3                # majority of 3
  python evals/run_router_evals.py --dry-run                # preflight, no key
  python evals/run_router_evals.py --baseline old.json      # local, experimental
"""

import argparse
import importlib.util
import json
import math
import os
import random
import re
import sys
import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

EXIT_PASS = 0
EXIT_GATE = 1
EXIT_CONFIG = 2
EXIT_API = 3

# API calls per vote for transient failures (429/5xx/408/409/network). The
# client is constructed with max_retries=0 so this loop is the only retry
# owner; backoff is exponential with jitter.
MAX_ATTEMPTS = 4

NONE_LABEL = "none"
# Sentinel ballots. Skill names are directory names, so neither can collide.
NO_ROUTE = "(no route call)"      # the model answered without the forced tool
NO_MAJORITY = "(no majority)"     # e.g. 1-1-1 with --votes 3


class ConfigError(Exception):
    """Anything wrong with the environment, the eval data, or the request
    shape the API refuses outright. Exit 2."""


class ApiError(Exception):
    """The API kept failing transiently after retries. Exit 3."""


class Cancelled(Exception):
    """Internal: another worker hit a fatal error; stop quickly and quietly."""


def gha_escape(message):
    """Escape a workflow-command message so corpus text can't forge commands."""
    return (message.replace("%", "%25")
                   .replace("\r", "%0D")
                   .replace("\n", "%0A"))


def md_cell(text):
    """Make arbitrary corpus text safe inside a one-line markdown table cell."""
    return (text.replace("\\", "\\\\")
                .replace("|", "\\|")
                .replace("`", "\\`")
                .replace("\r", " ")
                .replace("\n", " "))


def die(code, message):
    """One loud, actionable line — never a silent failure."""
    print(f"error: {message}", file=sys.stderr)
    if os.environ.get("GITHUB_ACTIONS"):
        print(f"::error ::{gha_escape(message)}")
    sys.exit(code)


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------

def read_frontmatter(skill_md):
    """name + whitespace-normalized description from a SKILL.md frontmatter."""
    import frontmatter

    try:
        post = frontmatter.load(skill_md)
    except Exception as exc:  # yaml errors, encoding, malformed fences
        raise ConfigError(f"could not parse the frontmatter of {skill_md}: {exc}")
    name = post.metadata.get("name")
    description = post.metadata.get("description")
    if not name or not description:
        raise ConfigError(f"{skill_md} frontmatter is missing name or description")
    return str(name), " ".join(str(description).split())


def manifest_include_only(repo_root):
    """The include-only set, from the code that owns that question.

    scripts/check_plugin_parity.py reads standalone-skills.yaml — the same
    manifest build.py reads — so this runner cannot develop its own opinion
    about which dist skills ship.
    """
    parity_path = repo_root / "scripts" / "check_plugin_parity.py"
    if not parity_path.is_file():
        raise ConfigError(
            f"{parity_path} not found — run from the repo root, or pass "
            "--include-unshipped to skip shipped-set resolution"
        )
    try:
        spec = importlib.util.spec_from_file_location("check_plugin_parity", parity_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.include_only_skills()
    except ConfigError:
        raise
    except Exception as exc:
        raise ConfigError(f"could not load {parity_path}: {exc}")


def load_candidates(dist_dir, include_unshipped):
    """Router candidates: name + description of every shipped dist skill."""
    dist_dir = Path(dist_dir)
    if not dist_dir.is_dir():
        raise ConfigError(f"dist directory not found: {dist_dir}")
    repo_root = dist_dir.resolve().parent

    skill_files = sorted(dist_dir.glob("*/SKILL.md"))
    if not skill_files:
        raise ConfigError(f"no */SKILL.md found under {dist_dir}")

    dist_skills = {}
    for skill_md in skill_files:
        name, description = read_frontmatter(skill_md)
        if name != skill_md.parent.name:
            raise ConfigError(
                f"{skill_md}: frontmatter name {name!r} does not match its "
                f"directory {skill_md.parent.name!r} — the build output is "
                "inconsistent; run `python build.py` and commit the result"
            )
        dist_skills[name] = description

    if include_unshipped:
        return dist_skills, set(dist_skills), []

    include_only = manifest_include_only(repo_root)
    candidates = {n: d for n, d in dist_skills.items() if n not in include_only}
    if not candidates:
        raise ConfigError(f"every skill under {dist_dir} is include-only — nothing to route to")

    # Cross-check the manifest's answer against the committed plugin mirrors.
    # The manifest owns the question; a disagreement means packaging drift.
    mirrored = {p.parent.name
                for p in (repo_root / "plugins").glob("*/skills/*/SKILL.md")}
    if set(candidates) != mirrored:
        manifest_only = sorted(set(candidates) - mirrored)
        mirror_only = sorted(mirrored - set(candidates))
        detail = []
        if manifest_only:
            detail.append(f"shipped per the manifest but mirrored in no plugin: {', '.join(manifest_only)}")
        if mirror_only:
            detail.append(f"mirrored in a plugin but include-only or unknown per the manifest: {', '.join(mirror_only)}")
        raise ConfigError(
            "standalone-skills.yaml and the plugins/ mirrors disagree about "
            "what ships (" + "; ".join(detail) +
            ") — run `python scripts/check_plugin_parity.py`"
        )

    unshipped = sorted(include_only & set(dist_skills))
    return candidates, set(dist_skills), unshipped


def normalize_query(query):
    """Fold the differences that do not make two queries a different test.

    Case and whitespace only. Nothing here touches the string that is sent
    to the model — this exists purely so the duplicate check cannot be
    slipped by a trailing space.
    """
    return re.sub(r"\s+", " ", query.strip()).casefold()


def load_evals(evals_path, dist_names, candidate_names):
    """Load and validate the JSONL corpus. Any defect is exit 2, pre-API."""
    evals_path = Path(evals_path)
    if not evals_path.is_file():
        raise ConfigError(f"eval file not found: {evals_path}")

    entries = []
    null_fields = []
    with evals_path.open(encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ConfigError(f"{evals_path}:{lineno} is not valid JSON ({exc})")
            if not isinstance(rec, dict):
                raise ConfigError(f"{evals_path}:{lineno} is not a JSON object")
            query = rec.get("query")
            if not isinstance(query, str) or not query.strip():
                raise ConfigError(f"{evals_path}:{lineno} has no usable 'query'")
            if "expected_skill" not in rec:
                raise ConfigError(f"{evals_path}:{lineno} is missing 'expected_skill'")
            expected = rec["expected_skill"]
            if expected is not None and not isinstance(expected, str):
                raise ConfigError(
                    f"{evals_path}:{lineno} 'expected_skill' must be a string or null"
                )
            if expected is not None:
                if expected not in dist_names:
                    raise ConfigError(
                        f"{evals_path}:{lineno} expects '{expected}', which is not a "
                        f"directory in dist/ — fix the label or build the skill"
                    )
                if expected not in candidate_names:
                    raise ConfigError(
                        f"{evals_path}:{lineno} expects '{expected}', which is in dist/ "
                        "but shipped in no plugin (include-only) — the router can never "
                        "pick it; fix the label per the no-broader-skill rule in "
                        "evals/README.md"
                    )
            # The optional metadata is type-checked too. An `id` that isn't a
            # string is later used as a dict key (baseline lookup, dedup) and
            # would raise an unhandled TypeError — exiting 1, which reads as
            # GATE FAILED — instead of the documented exit 2. And `bool()` on
            # `required` would turn the string "false" or the number 0.0 into
            # a value that changes which entries can veto the gate.
            #
            # ONE RULE FOR ALL THREE OPTIONAL FIELDS: an explicit JSON `null`
            # means "this field is absent". `id: null` and `notes: null` were
            # always read that way, while `required: null` was a hard exit-2
            # config error — so the same generator emitting `null` for every
            # unset optional field was accepted or rejected depending on
            # which field it happened to leave unset. Absent is what `null`
            # plainly means here (and for `required`, absent has always meant
            # False), so the ambiguity is resolved toward absent rather than
            # toward three different rules. It is still reported, because a
            # `null` in a hand-written corpus is more likely a mistake than
            # an intention.
            for optional in ("id", "required", "notes"):
                if optional in rec and rec[optional] is None:
                    null_fields.append((lineno, optional))
                    del rec[optional]
            entry_id = rec.get("id")
            if entry_id is not None and not isinstance(entry_id, str):
                raise ConfigError(
                    f"{evals_path}:{lineno} 'id' must be a string or absent, "
                    f"not {type(entry_id).__name__} ({entry_id!r})"
                )
            if entry_id is not None and not entry_id.strip():
                raise ConfigError(
                    f"{evals_path}:{lineno} 'id' is blank — omit the field or "
                    "give it a real value (a blank id silently falls back to "
                    "keying on the query)"
                )
            required = rec.get("required", False)
            if not isinstance(required, bool):
                raise ConfigError(
                    f"{evals_path}:{lineno} 'required' must be the JSON "
                    f"literal true or false, not {type(required).__name__} "
                    f"({required!r}) — coercing it would quietly promote or "
                    "demote this entry's veto over the gate"
                )
            notes = rec.get("notes")
            if notes is not None and not isinstance(notes, str):
                raise ConfigError(
                    f"{evals_path}:{lineno} 'notes' must be a string or "
                    f"absent, not {type(notes).__name__}"
                )
            entries.append({
                "line": lineno,
                "query": query,
                "expected": expected,
                "id": entry_id,
                "required": required,
                "notes": notes,
            })
    if not entries:
        raise ConfigError(f"{evals_path} contains no eval entries")

    for lineno, field in null_fields:
        print(f"warning: {evals_path}:{lineno} has '{field}': null, read as "
              f"the field being absent"
              + (" (so this entry cannot veto the gate)"
                 if field == "required" else ""),
              file=sys.stderr)

    # Two distinct duplicate hazards, so two namespaces:
    #
    #   * the baseline and the results table key on `id` (falling back to
    #     `query`), so a reused key collapses two entries into one there —
    #     including the cross-namespace case where one entry's `id` equals
    #     another entry's `query`;
    #   * a repeated query is routed and scored twice no matter how the ids
    #     differ, double-weighting it in accuracy.
    #
    # A single `id or query` check missed the second hazard entirely: two rows
    # with distinct ids and an identical query both got evaluated.
    seen_keys = {}
    seen_queries = {}
    for entry in entries:
        # The results key stays RAW: it is what lands in --output and what
        # --baseline looks up, so this check is about a literal dict
        # collision.
        key = entry["id"] or entry["query"]
        if key in seen_keys:
            raise ConfigError(
                f"{evals_path}:{entry['line']} reuses the results key {key!r} "
                f"of line {seen_keys[key]} (the key is `id` when present, "
                "otherwise `query`) — give each entry a unique id"
            )
        seen_keys[key] = entry["line"]
        # The query check normalizes, because this one is about the ROUTER:
        # "near dup query" and "near dup query " are one question asked
        # twice, and the router answers both, so accuracy counts it double
        # exactly as an exact duplicate would. Trailing whitespace and case
        # are not phrasing variants worth a second slot in the corpus; a
        # genuine variant should differ in words. Normalization is only for
        # detection — the query sent to the model is always the raw string,
        # so this cannot change a routing result.
        norm = normalize_query(entry["query"])
        if norm in seen_queries:
            raise ConfigError(
                f"{evals_path}:{entry['line']} repeats the query of line "
                f"{seen_queries[norm]} ({entry['query']!r}) — ignoring case "
                "and surrounding/repeated whitespace, these are the same "
                "query; it would be routed and scored twice, counting double "
                "in accuracy. Drop one, or reword it so it is a real "
                "phrasing variant"
            )
        seen_queries[norm] = entry["line"]
    return entries


def load_baseline(baseline_path):
    """Map entry key -> passed? from a previous --output file."""
    baseline_path = Path(baseline_path)
    if not baseline_path.is_file():
        raise ConfigError(f"baseline file not found: {baseline_path}")
    try:
        data = json.loads(baseline_path.read_text(encoding="utf-8"))
        rows = data["results"]
        if not isinstance(rows, list):
            raise TypeError("'results' is not a list")
        if isinstance(data, dict) and data.get("aborted"):
            # An aborted run's file exists precisely so the failure is
            # inspectable; it is not a comparison point.
            raise ConfigError(
                f"{baseline_path} is from an aborted run "
                f"({data['aborted']}) — it only covers the entries that "
                "finished, so it cannot be a regression baseline"
            )
        baseline = {}
        for row in rows:
            if not isinstance(row, dict) or "pass" not in row:
                raise TypeError(f"malformed results entry: {row!r}")
            key = row.get("id") or row.get("query")
            if not key:
                raise TypeError(f"results entry has neither id nor query: {row!r}")
            baseline[key] = bool(row["pass"])
        return baseline
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise ConfigError(
            f"{baseline_path} is not a results file this runner wrote "
            f"(expected a top-level 'results' array of entries; {exc})"
        )


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------

def build_request_parts(candidates):
    """The stable prompt + strict forced tool. Byte-stable across calls so the
    system block prompt-caches: candidates are sorted, nothing volatile."""
    names = sorted(candidates)
    listing = "\n".join(f"- {name}: {candidates[name]}" for name in names)
    system_text = (
        "You are the skill router; pick the single skill that should handle "
        "this message, or none.\n\n"
        "You will be given one customer message. Choose the one skill from the "
        "list below whose description best matches the message. If no listed "
        'skill should handle the message, choose "none". Route on the '
        "customer's primary intent, not on incidental keywords.\n\n"
        "Skills:\n"
        f"{listing}"
    )
    system = [{
        "type": "text",
        "text": system_text,
        "cache_control": {"type": "ephemeral"},
    }]
    tools = [{
        "name": "route",
        "description": "Report which skill should handle the customer message.",
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {"skill": {"type": "string", "enum": names + [NONE_LABEL]}},
            "required": ["skill"],
            "additionalProperties": False,
        },
    }]
    return system, tools


def route_once(client, model, system, tools, query, abort):
    """One routing call. Returns a ballot: a skill name, 'none', or NO_ROUTE."""
    import anthropic

    attempts = 0
    no_route_retried = False
    while True:
        if abort.is_set():
            raise Cancelled()
        transient = None
        try:
            response = client.messages.create(
                model=model,
                max_tokens=256,
                system=system,
                tools=tools,
                tool_choice={"type": "tool", "name": "route"},
                messages=[{"role": "user", "content": query}],
            )
        except anthropic.APIConnectionError as exc:
            transient = exc
        except anthropic.APIStatusError as exc:
            status = exc.status_code
            if status in (401, 403):
                raise ConfigError(
                    f"the API rejected the credentials (HTTP {status}) — "
                    f"check ANTHROPIC_API_KEY: {exc}"
                )
            if status in (408, 409, 429) or (status is not None and status >= 500):
                transient = exc
            else:
                raise ConfigError(
                    f"the API rejected the request (HTTP {status}) — check "
                    f"--model and the request shape: {exc}"
                )

        if transient is None:
            block = next((b for b in response.content
                          if b.type == "tool_use" and b.name == "route"), None)
            if block is not None:
                skill = (block.input.get("skill")
                         if isinstance(block.input, dict) else None)
                if isinstance(skill, str):
                    return skill
            # No forced tool call came back (effectively a refusal), or its
            # input was malformed despite strict mode. Retry once; if it
            # repeats, score this ballot as a failure instead of killing the
            # whole sweep.
            if no_route_retried:
                return NO_ROUTE
            no_route_retried = True
            continue

        attempts += 1
        if attempts >= MAX_ATTEMPTS:
            raise ApiError(
                f"API kept failing after {MAX_ATTEMPTS} attempts "
                f"({transient}) — query: {query!r}"
            )
        # Interruptible backoff: wakes immediately if another worker aborts.
        abort.wait(min(30.0, 2 ** attempts + random.random()))


def route_entry(client, model, system, tools, entry, votes, abort):
    """Majority of --votes ballots for one entry, short-circuiting once a
    label is mathematically decided. No strict majority => NO_MAJORITY."""
    need = votes // 2 + 1
    ballots = []
    counts = Counter()
    for _ in range(votes):
        vote = route_once(client, model, system, tools, entry["query"], abort)
        ballots.append(vote)
        counts[vote] += 1
        if counts[vote] >= need:
            return ballots, vote
    return ballots, NO_MAJORITY


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def subset_stats(results, predicate):
    subset = [r for r in results if predicate(r)]
    n_pass = sum(1 for r in subset if r["pass"])
    return {
        "n": len(subset),
        "n_pass": n_pass,
        "accuracy": round(n_pass / len(subset), 4) if subset else None,
    }


def emit_github_annotations(evals_path, failures_full):
    for r in failures_full:
        expected = r["expected"] or NONE_LABEL
        message = (f"trigger eval failed: expected {expected}, got {r['got']} "
                   f"— {r['query']}")
        print(f"::error file={evals_path},line={r['line']}::{gha_escape(message)}")


def emit_step_summary(summary, results, gate_reasons):
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    verdict = "PASS" if not gate_reasons else "FAIL — " + "; ".join(gate_reasons)
    lines = [
        "## Trigger-eval router gate",
        "",
        f"**{verdict}** — accuracy {summary['n_pass']}/{summary['n']} "
        f"({summary['accuracy']:.0%}) on `{summary['model']}`, "
        f"votes={summary['votes']}, gate ≥ {summary['min_accuracy']:.0%}",
        "",
        "| # | Query | Expected | Got | Result |",
        "|---|-------|----------|-----|--------|",
    ]
    for i, r in enumerate(results, 1):
        expected = r["expected"] or NONE_LABEL
        mark = "pass" if r["pass"] else ("**FAIL (required)**" if r["required"] else "**FAIL**")
        lines.append(
            f"| {i} | {md_cell(r['query'])} | {md_cell(expected)} | "
            f"{md_cell(r['got'])} | {mark} |"
        )
    if summary.get("regressions"):
        lines += ["", "### Baseline regressions", ""]
        lines += [f"- {md_cell(q)}" for q in summary["regressions"]]
    with open(path, "a", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--evals", default="evals/trigger-evals.jsonl",
                    help="JSONL eval corpus (default: %(default)s)")
    ap.add_argument("--dist", default="dist",
                    help="rendered skills directory (default: %(default)s)")
    ap.add_argument("--model", default="claude-opus-5")
    ap.add_argument("--min-accuracy", type=float, default=0.90,
                    help="gate threshold; CI deliberately does not override "
                         "this, so this default IS the gate (default: %(default)s)")
    ap.add_argument("--votes", type=int, default=1,
                    help="ballots per query, odd; strict majority wins "
                         "(default: %(default)s)")
    ap.add_argument("--concurrency", type=int, default=6,
                    help="entries routed in parallel (default: %(default)s)")
    ap.add_argument("--output", help="write the JSON summary here")
    ap.add_argument("--baseline",
                    help="previous --output file; any entry that passed there "
                         "must still pass. Experimental: CI does not wire a "
                         "baseline yet, so this is a local tool")
    ap.add_argument("--include-unshipped", action="store_true",
                    help="also offer include-only dist skills (rendered but "
                         "shipped in no plugin) to the router")
    ap.add_argument("--dry-run", action="store_true",
                    help="run every preflight check (packaging parity, corpus "
                         "shape, label validity — plus baseline shape, but "
                         "only if --baseline is also passed, which CI's "
                         "preflight job does not do) and stop before the "
                         "first API call. Needs no credential, so CI can run "
                         "it on untrusted PR code")
    args = ap.parse_args()

    if args.votes < 1 or args.votes % 2 == 0:
        die(EXIT_CONFIG, "--votes must be a positive odd number "
                         "(majority voting needs an odd ballot count)")
    if args.concurrency < 1:
        die(EXIT_CONFIG, "--concurrency must be >= 1")
    # A gate that cannot fail is worse than no gate. `argparse type=float`
    # happily accepts "nan", "-1" and "5": every `accuracy < nan` comparison
    # is false, so a 0%-accuracy run would print "gate passed" and exit 0.
    if not math.isfinite(args.min_accuracy) or not 0.0 <= args.min_accuracy <= 1.0:
        die(EXIT_CONFIG,
            f"--min-accuracy must be a finite fraction in [0, 1], got "
            f"{args.min_accuracy!r} — NaN and negative thresholds make the "
            "gate unfailable, and a threshold above 1 makes it unpassable")

    # --- Everything that can fail cheaply fails here, before any API call. ---
    try:
        candidates, dist_names, unshipped = load_candidates(
            args.dist, args.include_unshipped)
        entries = load_evals(args.evals, dist_names, set(candidates))
        baseline = load_baseline(args.baseline) if args.baseline else None
    except ConfigError as exc:
        die(EXIT_CONFIG, str(exc))

    if args.dry_run:
        # The credential-free half of the gate. Everything above is exactly
        # what the real run validates, so a PR that mislabels an entry or
        # drifts the packaging goes red here without any secret being exposed
        # to PR-authored code. It proves nothing about accuracy.
        print(f"preflight OK: {len(entries)} eval entries, "
              f"{len(candidates)} shipped router candidates"
              + (f", baseline covers {len(baseline)} keys" if baseline else ""))
        if unshipped:
            print(f"excluded include-only dist skills (no plugin ships them): "
                  f"{', '.join(unshipped)}")
        print("--dry-run: stopping before the first API call "
              "(accuracy NOT measured)")
        sys.exit(EXIT_PASS)

    if not (os.environ.get("ANTHROPIC_API_KEY")
            or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        die(EXIT_CONFIG,
            "ANTHROPIC_API_KEY is not set — export ANTHROPIC_API_KEY=<key> "
            "locally, or add it as an ENVIRONMENT secret in CI (`gh secret "
            "set ANTHROPIC_API_KEY --repo coreweave/skills --env evals-pr` "
            "and `--env evals-main`; never as a repository secret, see the "
            "header of .github/workflows/trigger-evals.yml). Or pass "
            "--dry-run to validate the corpus without a credential")

    try:
        import anthropic
    except ImportError:
        die(EXIT_CONFIG,
            'the anthropic SDK is not installed — run: pip install -e ".[evals]"')

    system, tools = build_request_parts(candidates)
    # max_retries=0: route_once owns retries; layering the SDK's own retries
    # under ours would mean up to a dozen HTTP attempts per vote.
    client = anthropic.Anthropic(max_retries=0)

    print(f"routing {len(entries)} queries against {len(candidates)} shipped "
          f"skills | model={args.model} votes={args.votes} "
          f"concurrency={args.concurrency}")
    if unshipped:
        print(f"excluded include-only dist skills (no plugin ships them): "
              f"{', '.join(unshipped)}")

    abort = threading.Event()

    def work(entry):
        try:
            ballots, got = route_entry(client, args.model, system, tools,
                                       entry, args.votes, abort)
            return entry, ballots, got
        except (ConfigError, ApiError, Cancelled):
            raise
        except Exception as exc:
            # Anything unexpected still maps to the documented exit semantics
            # with a real message, never a bare traceback.
            raise ApiError(f"unexpected error while routing "
                           f"{entry['query']!r}: {type(exc).__name__}: {exc}")

    results = []
    fatal = None
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futures = [pool.submit(work, entry) for entry in entries]
        for future in as_completed(futures):
            try:
                entry, ballots, got = future.result()
            except Cancelled:
                continue
            except (ConfigError, ApiError) as exc:
                if fatal is None:
                    fatal = exc
                    abort.set()  # in-flight workers bail at their next check
                continue
            expected_label = entry["expected"] or NONE_LABEL
            passed = got == expected_label
            results.append({
                "id": entry["id"],
                "line": entry["line"],
                "query": entry["query"],
                "expected": entry["expected"],
                "got": got,
                "pass": passed,
                "required": entry["required"],
                "votes": ballots,
            })
            print(("PASS " if passed else "FAIL ")
                  + f"expected={expected_label:<36} got={got:<36} "
                  + entry["query"][:70])

    results.sort(key=lambda r: r["line"])

    if fatal is not None:
        # Keep the evidence for the always() artifact step, then fail properly.
        # Write whenever --output was asked for, even with zero results: a run
        # that died on its very first call is exactly the case where the
        # artifact matters most, and `{"aborted": ..., "results": []}` IS the
        # evidence. Requiring a non-empty `results` left the upload step with
        # no file at all for fatal runs.
        if args.output:
            Path(args.output).write_text(json.dumps({
                "model": args.model,
                "votes": args.votes,
                "aborted": str(fatal),
                "n_planned": len(entries),
                "results": results,
            }, indent=2) + "\n", encoding="utf-8")
            print(f"wrote partial results ({len(results)}/{len(entries)} "
                  f"entries) to {args.output}", file=sys.stderr)
        die(EXIT_CONFIG if isinstance(fatal, ConfigError) else EXIT_API,
            str(fatal))

    n = len(results)
    n_pass = sum(1 for r in results if r["pass"])
    accuracy = n_pass / n
    failures_full = [r for r in results if not r["pass"]]
    required_failures = [r for r in failures_full if r["required"]]

    regressions = []
    baseline_covered = None
    if baseline is not None:
        # A regression is an entry that is PRESENT in the baseline, passed
        # there, and fails now. Entries absent from the baseline are new —
        # they are gated by accuracy/required only, never by regression.
        covered = 0
        for r in results:
            key = r["id"] or r["query"]
            if key not in baseline:
                continue
            covered += 1
            if baseline[key] and not r["pass"]:
                regressions.append(r["query"])
        baseline_covered = covered
        if covered == 0:
            message = ("--baseline covers NONE of the current entries — the "
                       "regression gate is a no-op (was the corpus renamed?)")
            print(f"warning: {message}", file=sys.stderr)
            if os.environ.get("GITHUB_ACTIONS"):
                print(f"::warning ::{gha_escape(message)}")
        elif covered < n / 2:
            print(f"warning: --baseline covers only {covered}/{n} current "
                  f"entries; the regression gate ignores the rest",
                  file=sys.stderr)

    gate_reasons = []
    if accuracy < args.min_accuracy:
        gate_reasons.append(
            f"accuracy {accuracy:.2%} is below the gate ({args.min_accuracy:.0%})")
    if required_failures:
        gate_reasons.append(
            f"{len(required_failures)} required entr"
            f"{'y' if len(required_failures) == 1 else 'ies'} failed")
    if regressions:
        gate_reasons.append(
            f"{len(regressions)} entr"
            f"{'y' if len(regressions) == 1 else 'ies'} regressed vs baseline")

    summary = {
        "model": args.model,
        "votes": args.votes,
        "min_accuracy": args.min_accuracy,
        "candidates": sorted(candidates),
        "excluded_unshipped": unshipped,
        "n": n,
        "n_pass": n_pass,
        "accuracy": round(accuracy, 4),
        "positives": subset_stats(results, lambda r: r["expected"] is not None),
        "negatives": subset_stats(results, lambda r: r["expected"] is None),
        "failures": [
            {"query": r["query"], "expected": r["expected"], "got": r["got"]}
            for r in failures_full
        ],
        "required_failures": [r["query"] for r in required_failures],
        "regressions": regressions,
        "baseline_covered": baseline_covered,
        "gate_passed": not gate_reasons,
        "gate_reasons": gate_reasons,
        "results": results,
    }

    if args.output:
        Path(args.output).write_text(
            json.dumps(summary, indent=2) + "\n", encoding="utf-8")

    print(f"\naccuracy: {n_pass}/{n} ({accuracy:.2%}) | gate >= "
          f"{args.min_accuracy:.0%} | positives "
          f"{summary['positives']['n_pass']}/{summary['positives']['n']} | "
          f"negatives {summary['negatives']['n_pass']}/{summary['negatives']['n']}")

    if os.environ.get("GITHUB_ACTIONS"):
        emit_github_annotations(args.evals, failures_full)
        emit_step_summary(summary, results, gate_reasons)

    if gate_reasons:
        sys.stdout.flush()
        print("GATE FAILED: " + "; ".join(gate_reasons), file=sys.stderr)
        sys.exit(EXIT_GATE)
    print("gate passed")
    sys.exit(EXIT_PASS)


if __name__ == "__main__":
    main()
