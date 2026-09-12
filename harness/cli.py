"""Command-line interface: ``python -m harness <command> ...``."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from harness import audit, badcase, compare, judge, lint, matrix, report, runner


def _cmd_run(args: argparse.Namespace) -> int:
    judge.configure(args.judge)
    result, path = runner.run(args.agent, args.cases, args.runs_dir, args.tool or None,
                              tiers=_split(args.tier), gates=runner.parse_gates(args.gate),
                              include_unagreed=args.include_unagreed)
    print(report.render_run(result, verbose=args.verbose))
    print(f"\nSaved: {path}")
    return 0


def _resolve_run(ref: str, runs_dir: str) -> Path:
    """A compare argument is either a run file path or an agent name (latest run)."""
    return Path(ref) if ref.endswith(".json") else runner.latest_run(ref, runs_dir)


def _cmd_compare(args: argparse.Namespace) -> int:
    run_a = compare.load_run(_resolve_run(args.a, args.runs_dir))
    run_b = compare.load_run(_resolve_run(args.b, args.runs_dir))
    cmp = compare.compare_runs(run_a, run_b)
    print(report.render_compare(cmp))
    if args.out:
        Path(args.out).write_text(json.dumps(cmp, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\nSaved: {args.out}")
    return 0


def _cmd_report(args: argparse.Namespace) -> int:
    print(report.render_run(compare.load_run(args.run), verbose=args.verbose))
    return 0


def _split(values: list[str] | None) -> list[str] | None:
    return [t.strip() for t in ",".join(values or []).split(",") if t.strip()] or None


def _cmd_matrix(args: argparse.Namespace) -> int:
    judge.configure(args.judge)
    agents = _split([args.agents]) or []
    if not agents:
        raise ValueError("--agents needs at least one agent name")
    runs = matrix.collect_runs(agents, args.cases, args.runs_dir, args.tool or None, tiers=_split(args.tier),
                               gates=runner.parse_gates(args.gate), reuse=args.reuse,
                               include_unagreed=args.include_unagreed)
    m = matrix.build_matrix(runs)
    print(matrix.render_text(m, verbose=args.verbose))
    if args.out:
        print(f"\nSaved: {matrix.write(m, args.out, verbose=args.verbose)}")
    return 0


def _cmd_audit(args: argparse.Namespace) -> int:
    if args.apply:
        result, path = audit.apply(args.apply, args.audits_dir)
        print(audit.render_agreement(result))
        print(f"\nSaved: {path}")
        return 0
    if not args.run:
        raise ValueError("audit needs a run.json to sample, or --apply labels.csv")
    run = compare.load_run(args.run)
    rows = audit.build_sheet(run, args.run, args.sample, seed=args.seed)
    judged = audit.judged_checks(run)
    out = args.out or str(Path(args.run).with_suffix("")) + "-audit.csv"
    path = audit.write_sheet(rows, out)
    failed = sum(1 for r in rows if not r["judge_passed"])
    print(f"Audit sheet: {len(rows)} rows ({failed} judged failures, {len(rows) - failed} sampled passes) "
          f"out of {len(judged)} judged checks in {run['n_cases']} cases")
    print("Fill in human_passed (yes/no) and human_note, then: harness audit --apply " + str(path))
    print(f"\nSaved: {path}")
    return 0


def _cmd_lint(args: argparse.Namespace) -> int:
    issues, n_cases, n_files = lint.lint(args.cases)
    print(lint.render(issues, n_cases, n_files))
    return 1 if lint.has_errors(issues) else 0


def _cmd_badcase(args: argparse.Namespace) -> int:
    if args.action == "add":
        path = badcase.add(args.agent, args.prompt, args.expected, note=args.note, tool=args.tool,
                           scorer=args.scorer, observed=args.observed, category=args.category,
                           owner=args.owner, backlog_dir=args.backlog_dir)
        print(f"Captured bad case: {path}")
    elif args.action == "promote":
        path = badcase.promote(args.id, args.backlog_dir, args.golden_file, rewrite=args.rewrite,
                               peer=args.peer, changelog=args.changelog)
        print(f"Promoted {args.id} -> {path}" + (" (prompt rewritten)" if args.rewrite else ""))
        if not args.peer:
            print("Note: promoted as status=draft; add a peer answer (or --peer) so `run` includes it.")
    else:  # list
        entries = badcase.list_entries(args.backlog_dir)
        if not entries:
            print("Backlog is empty.")
        for category, group in badcase.group_by_category(entries).items():
            print(f"[{category}] {len(group)}")
            for e in group:
                print(f"  {e['id']}  [{e.get('tool')}] agent={e.get('agent')}  {e['prompt'][:60]!r}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="harness", description="Lightweight evaluation harness for LLM agents.")
    sub = p.add_subparsers(dest="command", required=True)

    r = sub.add_parser("run", help="run one agent over the golden set")
    r.add_argument("--agent", required=True, help="agent name (agents/<name>.py), module path or .py file")
    r.add_argument("--cases", default="cases/golden", help="golden-set directory")
    r.add_argument("--runs-dir", default="runs")
    r.add_argument("--tool", action="append", help="only run cases for this tool (repeatable)")
    r.add_argument("--tier", action="append", help="only run these tiers, e.g. --tier unit,complex (repeatable)")
    r.add_argument("--gate", action="append",
                   help="stop after <tier> if its pass rate is below <rate>, e.g. --gate unit:0.9 (repeatable)")
    r.add_argument("--include-unagreed", action="store_true",
                   help="also run cases whose answer.status is draft or disputed")
    r.add_argument("--judge", choices=list(judge.BACKENDS), default=None,
                   help="LLM judge backend: auto (default), anthropic, fake (offline, tests/demos), none")
    r.add_argument("-v", "--verbose", action="store_true", help="print every case")
    r.set_defaults(func=_cmd_run)

    c = sub.add_parser("compare", help="compare two runs (agent names -> latest run, or .json paths)")
    c.add_argument("a")
    c.add_argument("b")
    c.add_argument("--runs-dir", default="runs")
    c.add_argument("--out", help="also write the comparison as JSON")
    c.set_defaults(func=_cmd_compare)

    mx = sub.add_parser("matrix", help="run several agents and tabulate agent x tier (x tool with -v)")
    mx.add_argument("--agents", required=True, help="comma-separated agent names, first one is the reference")
    mx.add_argument("--cases", default="cases/golden")
    mx.add_argument("--runs-dir", default="runs")
    mx.add_argument("--tool", action="append")
    mx.add_argument("--tier", action="append")
    mx.add_argument("--gate", action="append")
    mx.add_argument("--include-unagreed", action="store_true")
    mx.add_argument("--judge", choices=list(judge.BACKENDS), default=None)
    mx.add_argument("--reuse", action="store_true", help="reuse the latest saved run per agent instead of re-running")
    mx.add_argument("--out", help="write matrix.json or matrix.md")
    mx.add_argument("-v", "--verbose", action="store_true", help="also break down per tool")
    mx.set_defaults(func=_cmd_matrix)

    au = sub.add_parser("audit", help="sample judged cases for human labelling / compute judge agreement")
    au.add_argument("run", nargs="?", help="runs/<agent>-<ts>.json to sample from")
    au.add_argument("--sample", default="random:20", help="passed cases to sample: all | random:<N> (failed: always all)")
    au.add_argument("--seed", type=int, default=0)
    au.add_argument("--out", help="sheet path (.csv or .json); default <run>-audit.csv")
    au.add_argument("--apply", help="labelled sheet (.csv/.json) -> agreement report under runs/audits/")
    au.add_argument("--audits-dir", default="runs/audits")
    au.set_defaults(func=_cmd_audit)

    ln = sub.add_parser("lint", help="check the golden set: peer answers, statuses, tolerances, ids, scorers")
    ln.add_argument("--cases", default="cases/golden")
    ln.set_defaults(func=_cmd_lint)

    rp = sub.add_parser("report", help="print the report for a saved run")
    rp.add_argument("run", help="path to runs/<agent>-<timestamp>.json")
    rp.add_argument("-v", "--verbose", action="store_true")
    rp.set_defaults(func=_cmd_report)

    b = sub.add_parser("badcase", help="capture and promote failing production samples")
    bs = b.add_subparsers(dest="action", required=True)
    ba = bs.add_parser("add")
    ba.add_argument("--agent", required=True)
    ba.add_argument("--prompt", required=True)
    ba.add_argument("--expected", required=True)
    ba.add_argument("--category", required=True, choices=list(badcase.CATEGORIES),
                    help="what broke: data, tool_choice, ambiguity (fix the question), reasoning, judge")
    ba.add_argument("--owner", default="", help="who wrote the expected value")
    ba.add_argument("--note", default="")
    ba.add_argument("--observed", default="", help="what the agent actually said")
    ba.add_argument("--tool", default="backlog")
    ba.add_argument("--scorer", default="contains", choices=["exact", "contains", "regex", "numeric", "policy", "citation"])
    ba.add_argument("--backlog-dir", default="cases/backlog")
    bp = bs.add_parser("promote")
    bp.add_argument("id")
    bp.add_argument("--backlog-dir", default="cases/backlog")
    bp.add_argument("--golden-file", default="cases/golden/promoted.yaml")
    bp.add_argument("--rewrite", help="clarified prompt; required for category=ambiguity")
    bp.add_argument("--peer", help="peer answer; with it the case is promoted as status=agreed")
    bp.add_argument("--changelog", default=None, help="default: <cases dir>/CHANGELOG.md")
    bl = bs.add_parser("list")
    bl.add_argument("--backlog-dir", default="cases/backlog")
    b.set_defaults(func=_cmd_badcase)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (FileNotFoundError, ValueError, KeyError, AttributeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
