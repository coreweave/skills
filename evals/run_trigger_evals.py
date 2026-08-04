#!/usr/bin/env python3
"""Trigger-eval runner for the CoreWeave skills bundle.

Answers the bundle-level question from evals/README.md: given a realistic
customer query, does the Skill router pick the right skill (or correctly pick
none)?

Reads a JSONL file of {"query": ..., "expected_skill": ...} records, spawns one
headless `claude -p` per run, and scores whether a Skill call fired and which
skill it named.

Why this exists: `claude plugin eval` has a built-in --ablation baseline arm,
but it is gated behind early access and it only discovers cases shaped as
evals/**/case.yaml. This script needs neither.

Usage
-----
  # default arm: skills installed, MCP servers as configured (realistic)
  ./run_trigger_evals.py

  # isolate the docs-MCP competing attractor
  ./run_trigger_evals.py --no-mcp --out results-nomcp.json

  # no-skill control arm (disable the plugins first, then run)
  #   claude plugin disable coreweave-cks-skills@coreweave-skills
  ./run_trigger_evals.py --out results-baseline.json

Safety
------
Several queries in the eval set ask an agent to provision real GPU
infrastructure ("create a CKS cluster with 8 H100s for me"). By default this
script denies Bash, Write, Edit, NotebookEdit and Task, so a run physically
cannot terraform, kubectl, or shell out to anything.

That denial is not free: denied tools are REMOVED from the model's tool list
(30 tools becomes 28), and a shorter tool list can shift the very routing
decision being measured. Expect the safe arm to read slightly optimistic
relative to a real customer session. Use --allow-exec for a fully faithful
tool list, but only while supervising and only if you accept that a run may
create billable resources.

Read-only MCP tools stay enabled in both modes.
"""

import argparse
import json
import subprocess
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed

# Denied in safe mode. Task is included because a subagent could otherwise
# shell out and defeat the point.
SAFE_DENY = ["Bash", "Write", "Edit", "NotebookEdit", "Task"]

# Sessions x runs multiplies fast: the 21-case set at --runs 3 is 63 sessions.
# Above this, require an explicit --yes so nobody fires a large sweep by
# reading --runs as a total.
MAX_UNCONFIRMED = 12

# Live child processes, so Ctrl+C does not leave a pile of orphaned sessions
# billing in the background.
LIVE = set()

PASS = "PASS"
WRONG_SKILL = "WRONG_SKILL"
BAD_SCOPE = "BAD_SCOPE"
NO_TRIGGER = "NO_TRIGGER"
FALSE_FIRE = "FALSE_FIRE"
INVALID_LABEL = "INVALID_LABEL"
ERROR = "ERROR"

# Chain verdicts, scored independently of the routing verdict above. A run can
# route correctly and still fail to chain (the common case: cw-create-cluster
# fires, then the model hand-rolls the inference manifests itself).
CHAIN_PASS = "CHAIN_PASS"
CHAIN_PARTIAL = "CHAIN_PARTIAL"      # some expected skill never fired
CHAIN_OUT_OF_ORDER = "CHAIN_OUT_OF_ORDER"  # all fired, but not in the expected order


def bare(name):
    """'coreweave-cks-skills:cw-create-cluster' -> 'cw-create-cluster'.

    The eval file labels skills by bare name; the runtime reports them
    plugin-scoped. Compare on the bare name.
    """
    if not name:
        return None
    return name.split(":")[-1]


def load_cases(path):
    cases = []
    with open(path) as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError as exc:
                print(f"warning: {path}:{lineno} is not valid JSON ({exc})", file=sys.stderr)
                continue
            if "query" not in rec:
                print(f"warning: {path}:{lineno} has no 'query' field", file=sys.stderr)
                continue
            cases.append({
                "query": rec["query"],
                "expected": rec.get("expected_skill"),
                # Optional. An ordered list of skills the run should consult.
                # Absent => single-skill case, scored exactly as before.
                "expected_chain": rec.get("expected_chain"),
            })
    return cases


def run_once(query, model, allow_exec, no_mcp, max_tools, timeout, collect_chain=False):
    """Run one headless session and return what the router did.

    Single-skill mode (the default) stops after the first Skill call's result
    comes back, or after `max_tools` tool calls, whichever comes first. That
    bounds cost and keeps a run from proceeding into real work.

    `collect_chain=True` keeps going and records EVERY Skill call, because a
    chain case asks a question the early exit cannot answer: does the run go on
    to consult the next skill, or does it hand-roll the rest itself? Cost is
    bounded by `max_tools` alone, so chain cases want a much larger budget.

    We wait for each Skill *result*, not just the call, because a model can
    invoke Skill with a name that does not exist (a plausible-looking but wrong
    plugin prefix, say). That errors, the skill never loads, and scoring it as
    a successful trigger would be wrong.
    """
    cmd = ["claude", "-p"]
    if not allow_exec:
        cmd += ["--settings", json.dumps({"permissions": {"deny": SAFE_DENY}})]
    if no_mcp:
        cmd += ["--strict-mcp-config"]
    cmd += ["--output-format", "stream-json", "--verbose", "--model", model, query]

    out = {
        "skills_loaded": [],
        "mcp_servers": [],
        "tool_count": None,
        "tool_sequence": [],
        "fired_skill": None,
        "skill_name_exists": None,
        "skill_call_failed": None,
        "skill_result": None,
        # Every Skill call in order: {"skill", "exists", "failed"}. The first
        # entry mirrors the fired_skill/* fields above.
        "fired_skills": [],
        "error": None,
    }
    pending = {}  # tool_use_id -> record in out["fired_skills"]

    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True
    )
    LIVE.add(proc)
    try:
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue

            if ev.get("subtype") == "init":
                out["skills_loaded"] = ev.get("skills", []) or []
                out["mcp_servers"] = ev.get("mcp_servers", []) or []
                out["tool_count"] = len(ev.get("tools", []) or [])

            if ev.get("type") == "assistant":
                content = (ev.get("message") or {}).get("content") or []
                for block in content:
                    if not isinstance(block, dict) or block.get("type") != "tool_use":
                        continue
                    name = block.get("name")
                    out["tool_sequence"].append(name)
                    if name != "Skill":
                        continue
                    named = (block.get("input") or {}).get("skill")
                    rec = {
                        "skill": named,
                        "exists": named in out["skills_loaded"],
                        "failed": None,
                    }
                    out["fired_skills"].append(rec)
                    if out["fired_skill"] is None:
                        out["fired_skill"] = named
                        out["skill_name_exists"] = rec["exists"]
                    if block.get("id"):
                        pending[block["id"]] = rec
                    if not collect_chain:
                        break

            # Collect each Skill call's result.
            if pending:
                content = (ev.get("message") or {}).get("content") or []
                for block in content:
                    if not isinstance(block, dict) or block.get("type") != "tool_result":
                        continue
                    rec = pending.pop(block.get("tool_use_id"), None)
                    if rec is None:
                        continue
                    rec["failed"] = bool(block.get("is_error"))
                    if rec is out["fired_skills"][0]:
                        out["skill_call_failed"] = rec["failed"]
                        out["skill_result"] = str(block.get("content"))[:300]

            if collect_chain:
                # Only the tool budget bounds a chain run: the whole point is to
                # see whether later skills fire, which happens after the first
                # one resolves.
                if len(out["tool_sequence"]) >= max_tools:
                    break
            else:
                if out["fired_skill"] is not None and not pending:
                    break
                if len(out["tool_sequence"]) >= max_tools and out["fired_skill"] is None:
                    break
    except Exception as exc:  # noqa: BLE001 - report, never crash the sweep
        out["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        LIVE.discard(proc)
        proc.kill()
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            pass

    return out


def score(case, run):
    """Classify one run against its expected label."""
    if run["error"]:
        return ERROR

    expected = case["expected"]
    fired = bare(run["fired_skill"])
    loaded = {bare(s) for s in run["skills_loaded"]}

    if expected is None:
        # "no skill applies, just chat"
        return PASS if fired is None else FALSE_FIRE

    # A skill that is not loaded cannot fire. Surface that as a data bug in the
    # eval file rather than silently counting it as a routing failure.
    if expected not in loaded:
        return INVALID_LABEL

    if fired is None:
        return NO_TRIGGER
    if fired != expected:
        return WRONG_SKILL
    # Right skill, but the invocation itself did not resolve: the model named a
    # skill that does not exist (usually a wrong plugin prefix), so nothing
    # loaded. Right intent, failed call.
    if run["skill_name_exists"] is False or run["skill_call_failed"]:
        return BAD_SCOPE
    return PASS


def score_chain(case, run):
    """Classify the chain, independently of routing. None if not a chain case.

    Returns (verdict, missing). Order is matched as a SUBSEQUENCE, not as
    adjacency: a real run legitimately interleaves get-coreweave-kubeconfig or
    verify-coreweave-workload-health between the skills we care about, and
    demanding adjacency would fail those runs for no good reason.
    """
    chain = case.get("expected_chain")
    if not chain:
        return None, []
    if run["error"]:
        return ERROR, []

    # A call that errored never loaded the skill, so it does not count.
    fired = [bare(f["skill"]) for f in run["fired_skills"] if not f["failed"]]

    missing = [s for s in chain if s not in fired]
    if missing:
        return CHAIN_PARTIAL, missing

    remaining = iter(fired)
    in_order = all(any(f == want for f in remaining) for want in chain)
    return (CHAIN_PASS if in_order else CHAIN_OUT_OF_ORDER), []


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--file", default="trigger-evals.jsonl", help="JSONL eval set (default: %(default)s)")
    ap.add_argument("--runs", type=int, default=3,
                    help="runs PER CASE, not in total; routing is stochastic (default: %(default)s)")
    ap.add_argument("--limit", type=int, help="only run the first N cases")
    ap.add_argument("--dry-run", action="store_true", help="print the session count and exit without spending anything")
    ap.add_argument("--yes", action="store_true",
                    help=f"confirm a sweep larger than {MAX_UNCONFIRMED} sessions")
    ap.add_argument("--model", default="claude-haiku-4-5-20251001")
    ap.add_argument("--jobs", type=int, default=3, help="concurrent sessions (default: %(default)s)")
    ap.add_argument("--max-tools", type=int, default=4,
                    help="trigger window: give up after this many tool calls (default: %(default)s)")
    ap.add_argument("--chain-max-tools", type=int, default=40,
                    help="tool budget for cases carrying expected_chain; a chain "
                         "needs room to reach the second skill (default: %(default)s)")
    ap.add_argument("--timeout", type=int, default=120, help="per-run seconds (default: %(default)s)")
    ap.add_argument("--no-mcp", action="store_true", help="run with --strict-mcp-config (drops the docs MCP)")
    ap.add_argument("--allow-exec", action="store_true",
                    help="UNSAFE: faithful tool list, but a run may create billable resources")
    ap.add_argument("--case", help="only run cases whose query contains this substring")
    ap.add_argument("--out", default="trigger-results.json")
    args = ap.parse_args()

    cases = load_cases(args.file)
    if args.case:
        cases = [c for c in cases if args.case.lower() in c["query"].lower()]
    if args.limit:
        cases = cases[: args.limit]
    if not cases:
        sys.exit("no cases to run")

    sessions = len(cases) * args.runs
    plan = (f"{len(cases)} cases x {args.runs} runs = {sessions} sessions "
            f"| model={args.model} | mcp={'off' if args.no_mcp else 'on'} "
            f"| mode={'exec' if args.allow_exec else 'safe'}")

    if args.dry_run:
        print(plan + "\n(dry run: nothing spent)")
        return

    if sessions > MAX_UNCONFIRMED and not args.yes:
        sys.exit(
            f"{plan}\n\n"
            f"That is {sessions} sessions. --runs is per case, not a total.\n"
            f"  smaller:  --limit 3 --runs 1   (3 sessions)\n"
            f"  preview:  --dry-run\n"
            f"  proceed:  add --yes"
        )

    if args.allow_exec:
        print("!! --allow-exec: tools are unrestricted. Runs may provision real, billable infrastructure.\n")

    print(plan + "\n")

    jobs = [(ci, c) for ci, c in enumerate(cases) for _ in range(args.runs)]
    results = [[] for _ in cases]

    def work(job):
        ci, case = job
        is_chain = bool(case.get("expected_chain"))
        run = run_once(case["query"], args.model, args.allow_exec, args.no_mcp,
                       args.chain_max_tools if is_chain else args.max_tools,
                       args.timeout, collect_chain=is_chain)
        chain_verdict, missing = score_chain(case, run)
        run["chain_verdict"] = chain_verdict
        run["chain_missing"] = missing
        return ci, run, score(case, run)

    def save():
        """Persist after every result, so an interrupted sweep is not wasted."""
        with open(args.out, "w") as fh:
            json.dump(
                {
                    "config": vars(args),
                    "cases": [
                        {"query": c["query"], "expected_skill": c["expected"],
                         "expected_chain": c.get("expected_chain"), "runs": runs}
                        for c, runs in zip(cases, results)
                    ],
                },
                fh,
                indent=2,
            )

    pool = ThreadPoolExecutor(max_workers=args.jobs)
    futures = [pool.submit(work, job) for job in jobs]
    try:
        for fut in as_completed(futures):
            ci, run, verdict = fut.result()
            results[ci].append({"verdict": verdict, **run})
            save()
            print(".", end="", flush=True)
    except KeyboardInterrupt:
        for fut in futures:
            fut.cancel()
        for proc in list(LIVE):
            proc.kill()
        done = sum(len(r) for r in results)
        print(f"\n\ninterrupted after {done}/{len(jobs)} sessions; partial results in {args.out}")
    finally:
        pool.shutdown(wait=False)
    print("\n")

    # Per-case report
    rows = []
    for case, runs in zip(cases, results):
        if not runs:
            continue  # never got to this case (interrupted sweep)
        verdicts = Counter(r["verdict"] for r in runs)
        rows.append((verdicts[PASS] / len(runs), case, verdicts, runs))
    if not rows:
        sys.exit("no completed runs to report")

    width = 58
    print(f"{'rate':>6}  {'expected':<32}  query")
    print("-" * (width + 46))
    for rate, case, verdicts, _ in sorted(rows, key=lambda r: r[0]):
        exp = case["expected"] or "(none)"
        dominant = ", ".join(f"{v}x{k}" for k, v in verdicts.most_common() if k != PASS)
        q = case["query"][:width]
        print(f"{rate:>5.0%}  {exp:<32}  {q}")
        if dominant:
            print(f"{'':>6}  {'':<32}  -> {dominant}")

    # Aggregates
    all_verdicts = Counter(r["verdict"] for runs in results for r in runs)
    total = sum(all_verdicts.values())
    scorable = total - all_verdicts[INVALID_LABEL] - all_verdicts[ERROR]
    print(f"\ntrigger accuracy: {all_verdicts[PASS]}/{scorable}"
          f" ({all_verdicts[PASS] / scorable:.0%})" if scorable else "\nnothing scorable")
    for verdict, n in all_verdicts.most_common():
        print(f"  {verdict:<14} {n}")

    # Chain report. Separate block because chaining and routing fail for
    # different reasons and have different fixes.
    chain_rows = [(c, runs) for c, runs in zip(cases, results)
                  if c.get("expected_chain") and runs]
    if chain_rows:
        print("\nchain accuracy (does the run consult the whole sequence?)")
        for case, runs in chain_rows:
            verdicts = Counter(r.get("chain_verdict") for r in runs)
            rate = verdicts[CHAIN_PASS] / len(runs)
            print(f"{rate:>5.0%}  {' -> '.join(case['expected_chain'])}")
            print(f"{'':>6}  {case['query'][:62]}")
            missing = Counter(m for r in runs for m in (r.get("chain_missing") or []))
            if missing:
                never = ", ".join(f"{name} ({n}/{len(runs)} runs)"
                                  for name, n in missing.most_common())
                print(f"{'':>6}  never fired: {never}")
        print("\nIn the default safe arm Bash/Write/Task are denied, so this "
              "measures whether the run\nCONSULTS each skill, not whether it "
              "executes them. That is the intended signal:\nthe observed failure "
              "is that later skills are never loaded at all.")

    if all_verdicts[INVALID_LABEL]:
        stale = sorted({c["expected"] for c, runs in zip(cases, results)
                        if any(r["verdict"] == INVALID_LABEL for r in runs)})
        print(f"\nINVALID_LABEL means the expected skill is not installed. Fix these labels: {', '.join(stale)}")

    # What the router reached for instead. This is where a competing docs MCP
    # shows up: a first call into mcp__crwv-docs__* means the query was
    # answered by documentation lookup rather than routed to a skill.
    firsts = Counter(r["tool_sequence"][0] for runs in results for r in runs if r["tool_sequence"])
    if firsts:
        print("\nfirst tool call across all runs:")
        for name, n in firsts.most_common(10):
            print(f"  {name:<38} {n}")

    save()
    print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
