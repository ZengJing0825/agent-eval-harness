"""Command-line interface: ``python -m harness <command> ...``."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from harness import badcase, compare, judge, report, runner


def _cmd_run(args: argparse.Namespace) -> int:
    judge.configure(args.judge)
    tiers = [t.strip() for t in ",".join(args.tier or []).split(",") if t.strip()] or None
    result, path = runner.run(args.agent, args.cases, args.runs_dir, args.tool or None,
                              tiers=tiers, gates=runner.parse_gates(args.gate))
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


def _cmd_badcase(args: argparse.Namespace) -> int:
    if args.action == "add":
        path = badcase.add(args.agent, args.prompt, args.expected, note=args.note, tool=args.tool,
                           scorer=args.scorer, observed=args.observed, backlog_dir=args.backlog_dir)
        print(f"Captured bad case: {path}")
    elif args.action == "promote":
        path = badcase.promote(args.id, args.backlog_dir, args.golden_file)
        print(f"Promoted {args.id} -> {path}")
    else:  # list
        entries = badcase.list_entries(args.backlog_dir)
        if not entries:
            print("Backlog is empty.")
        for e in entries:
            print(f"{e['id']}  [{e.get('tool')}] agent={e.get('agent')}  {e['prompt'][:60]!r}")
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
    ba.add_argument("--note", default="")
    ba.add_argument("--observed", default="", help="what the agent actually said")
    ba.add_argument("--tool", default="backlog")
    ba.add_argument("--scorer", default="contains", choices=["exact", "contains", "regex", "numeric", "policy", "citation"])
    ba.add_argument("--backlog-dir", default="cases/backlog")
    bp = bs.add_parser("promote")
    bp.add_argument("id")
    bp.add_argument("--backlog-dir", default="cases/backlog")
    bp.add_argument("--golden-file", default="cases/golden/promoted.yaml")
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
