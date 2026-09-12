"""Command-line interface: ``python -m harness <command> ...``."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from harness import audit, badcase, compare, importer, judge, lint, markdown, matrix, report, runner


def _cmd_run(args: argparse.Namespace) -> int:
    judge.configure(args.judge)
    result, path = runner.run(args.agent, args.cases, args.runs_dir, args.tool or None,
                              tiers=_split(args.tier), gates=runner.parse_gates(args.gate),
                              include_unagreed=args.include_unagreed, as_of=args.as_of,
                              gate_mode=args.gate_mode, config=args.config)
    print(report.render_run(result, verbose=args.verbose))
    print(f"\nSaved: {path}")
    if args.md:
        Path(args.md).write_text(markdown.render_run_md(result), encoding="utf-8")
        print(f"Saved: {args.md}")
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
    if args.md:
        Path(args.md).write_text(markdown.render_compare_md(cmp, run_a, run_b), encoding="utf-8")
        print(f"Saved: {args.md}")
    return 0


def _cmd_report(args: argparse.Namespace) -> int:
    run = compare.load_run(args.run)
    print(report.render_run(run, verbose=args.verbose))
    if args.md:
        Path(args.md).write_text(markdown.render_run_md(run), encoding="utf-8")
        print(f"\nSaved: {args.md}")
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
                               include_unagreed=args.include_unagreed, as_of=args.as_of, gate_mode=args.gate_mode)
    m = matrix.build_matrix(runs)
    print(matrix.render_text(m, verbose=args.verbose))
    for out in (args.out, args.md):
        if out:
            print(f"\nSaved: {matrix.write(m, out if out != args.md else str(Path(out).with_suffix('.md')), verbose=args.verbose)}")
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
    buckets = {b: sum(1 for r in rows if r["bucket"] == b) for b in ("fail", "low", "pass")}
    print(f"Audit sheet: {len(rows)} rows ({buckets['fail']} judged failures, {buckets['low']} low-scoring passes, "
          f"{buckets['pass']} sampled passes; rule {args.sample}) out of {len(judged)} judged checks in "
          f"{run['n_cases']} cases")
    print("Fill in human_passed (yes/no) or human_score (0-1), reviewer and human_note, then: "
          "harness audit --apply " + str(path))
    print(f"\nSaved: {path}")
    return 0


def _cmd_import(args: argparse.Namespace) -> int:
    rows = importer.read_rows(args.csv, args.jsonl)
    doc = importer.build_document(rows, importer.parse_map(args.map), args.source, args.license,
                                  tier=args.tier, tool=args.tool, scorer=args.scorer, status=args.status)
    out = args.out or f"cases/golden/external_{importer.slug(args.source)}.yaml"
    path = importer.write_document(doc, out, origin=args.csv or args.jsonl)
    print(f"Imported {len(doc['cases'])} case(s) from {args.csv or args.jsonl} -> {path}")
    print(f"  tier={doc['tier']} source={doc['source']!r} license={doc['license']!r}")
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
        if args.peer is not None:
            print("warning: --peer is deprecated; use --reviewer NAME --agree (recorded as an 'agree' review)",
                  file=sys.stderr)
        path = badcase.promote(args.id, args.backlog_dir, args.golden_file, rewrite=args.rewrite,
                               reviewer=args.reviewer, agree=args.agree, peer=args.peer, changelog=args.changelog,
                               audits_dir=args.audits_dir)
        if path.name == badcase.JUDGE_DISPUTES_FILE:
            print(f"Recorded judge dispute {args.id} -> {path} (not in the golden set: fix the judge, not the agent)")
            return 0
        print(f"Promoted {args.id} -> {path}" + (" (prompt rewritten)" if args.rewrite else ""))
        promoted = badcase.list_golden_statuses(args.golden_file).get(args.id)
        if promoted == "skipped_unsupported":
            print("Note: status=skipped_unsupported; `run` skips it (reason: unsupported) until the tool is supported.")
        elif not (args.agree or args.peer is not None):
            print("Note: promoted as status=draft; record a peer review (answer.peer: {reviewer, verdict: agree}) "
                  "so `run` includes it.")
    else:  # list
        entries = badcase.list_entries(args.backlog_dir)
        if not entries:
            print("Backlog is empty.")
        for category, group in badcase.group_by_category(entries).items():
            hint = badcase.CATEGORY_HINTS.get(category)
            print(f"[{category}] {len(group)}" + (f"  - {hint}" if hint else ""))
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
                   help="per-tier target, e.g. --gate unit:0.9 (repeatable); overrides targets: in harness.yaml")
    r.add_argument("--gate-mode", choices=list(runner.GATE_MODES), default=None,
                   help="target (default: record met/not met, run every tier) or strict (unmet target skips later tiers)")
    r.add_argument("--config", default=str(runner.DEFAULT_CONFIG), help="optional harness.yaml with targets/gate_mode")
    r.add_argument("--include-unagreed", action="store_true",
                   help="also run cases whose answer.status is draft or disputed")
    r.add_argument("--as-of", help="date (YYYY-MM-DD) for {as_of} placeholders and resolvers; default today")
    r.add_argument("--judge", choices=list(judge.BACKENDS), default=None,
                   help="LLM judge backend: auto (default), anthropic, fake (offline, tests/demos), none")
    r.add_argument("--md", help="also write a markdown report")
    r.add_argument("-v", "--verbose", action="store_true", help="print every case")
    r.set_defaults(func=_cmd_run)

    c = sub.add_parser("compare", help="compare two runs (agent names -> latest run, or .json paths)")
    c.add_argument("a")
    c.add_argument("b")
    c.add_argument("--runs-dir", default="runs")
    c.add_argument("--out", help="also write the comparison as JSON")
    c.add_argument("--md", help="also write a markdown report")
    c.set_defaults(func=_cmd_compare)

    mx = sub.add_parser("matrix", help="run several agents and tabulate agent x tier (x tool with -v)")
    mx.add_argument("--agents", required=True, help="comma-separated agent names, first one is the reference")
    mx.add_argument("--cases", default="cases/golden")
    mx.add_argument("--runs-dir", default="runs")
    mx.add_argument("--tool", action="append")
    mx.add_argument("--tier", action="append")
    mx.add_argument("--gate", action="append")
    mx.add_argument("--gate-mode", choices=list(runner.GATE_MODES), default=None)
    mx.add_argument("--include-unagreed", action="store_true")
    mx.add_argument("--as-of", help="date for the dynamic tier; default today")
    mx.add_argument("--judge", choices=list(judge.BACKENDS), default=None)
    mx.add_argument("--reuse", action="store_true", help="reuse the latest saved run per agent instead of re-running")
    mx.add_argument("--out", help="write matrix.json or matrix.md")
    mx.add_argument("--md", help="write a markdown matrix (same as --out with .md)")
    mx.add_argument("-v", "--verbose", action="store_true", help="also break down per tool")
    mx.set_defaults(func=_cmd_matrix)

    au = sub.add_parser("audit", help="sample judged cases for human labelling / compute judge agreement")
    au.add_argument("run", nargs="?", help="runs/<agent>-<ts>.json to sample from")
    au.add_argument("--sample", default=audit.DEFAULT_SAMPLE,
                    help="sampling rule: all-fails (every judged failure), low-first (every pass scored < 0.5), "
                         "pass:<N> (random N passes); also all | random:<N>. Default all-fails,low-first,pass:10")
    au.add_argument("--seed", type=int, default=0)
    au.add_argument("--out", help="sheet path (.csv or .json); default <run>-audit.csv")
    au.add_argument("--apply", help="labelled sheet (.csv/.json) -> agreement report under runs/audits/")
    au.add_argument("--audits-dir", default="runs/audits")
    au.set_defaults(func=_cmd_audit)

    im = sub.add_parser("import", help="import an external benchmark (CSV/JSONL) as an external-tier case file")
    im.add_argument("--csv")
    im.add_argument("--jsonl")
    im.add_argument("--map", default="", help='case field=column, e.g. "prompt=question,expected=answer,tool=category"')
    im.add_argument("--tier", default="external", choices=list(runner.TIERS))
    im.add_argument("--tool", default="external", help="file-level tool when the map has no 'tool' column")
    im.add_argument("--scorer", default="contains", choices=list(importer.DETERMINISTIC_SCORERS))
    im.add_argument("--source", required=True, help="benchmark name (recorded at file level)")
    im.add_argument("--license", required=True, help="licence text or identifier (recorded at file level)")
    im.add_argument("--status", default="agreed", choices=["agreed", "draft"])
    im.add_argument("--out", help="default cases/golden/external_<source>.yaml")
    im.set_defaults(func=_cmd_import)

    ln = sub.add_parser("lint", help="check the golden set: peer answers, statuses, tolerances, ids, scorers")
    ln.add_argument("--cases", default="cases/golden")
    ln.set_defaults(func=_cmd_lint)

    rp = sub.add_parser("report", help="print the report for a saved run")
    rp.add_argument("run", help="path to runs/<agent>-<timestamp>.json")
    rp.add_argument("--md", help="write a markdown report")
    rp.add_argument("-v", "--verbose", action="store_true")
    rp.set_defaults(func=_cmd_report)

    b = sub.add_parser("badcase", help="capture and promote failing production samples")
    bs = b.add_subparsers(dest="action", required=True)
    ba = bs.add_parser("add")
    ba.add_argument("--agent", required=True)
    ba.add_argument("--prompt", required=True)
    ba.add_argument("--expected", required=True)
    ba.add_argument("--category", required=True, choices=list(badcase.CATEGORIES),
                    help="what broke: data, tool_choice, ambiguity (fix the question), reasoning, "
                         "unsupported (not testable yet), judge (fix the judge)")
    ba.add_argument("--owner", default="", help="who wrote the expected value")
    ba.add_argument("--note", default="")
    ba.add_argument("--observed", default="", help="what the agent actually said")
    ba.add_argument("--tool", default="backlog")
    ba.add_argument("--scorer", default="contains", choices=["exact", "contains", "correctness", "regex", "numeric", "policy", "citation"])
    ba.add_argument("--backlog-dir", default="cases/backlog")
    bp = bs.add_parser("promote")
    bp.add_argument("id")
    bp.add_argument("--backlog-dir", default="cases/backlog")
    bp.add_argument("--golden-file", default="cases/golden/promoted.yaml")
    bp.add_argument("--rewrite", help="clarified prompt; required for category=ambiguity")
    bp.add_argument("--reviewer", help="who reviewed the owner answer (recorded in answer.peer.reviewer)")
    bp.add_argument("--agree", action="store_true",
                    help="record the reviewer's verdict as 'agree' -> status=agreed (else draft)")
    bp.add_argument("--peer", help="deprecated alias: records an 'agree' review; use --reviewer NAME --agree")
    bp.add_argument("--changelog", default=None, help="default: <cases dir>/CHANGELOG.md")
    bp.add_argument("--audits-dir", default="runs/audits", help="where judge-category disputes are appended")
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
