"""Markdown renderings of runs and comparisons (``--md out.md``).

Each report carries the same sections so they can be pasted into a release
discussion: tier x tool table, gate hits, regressions, judge coverage
(how many cases an LLM judge decided vs deterministic scorers), and skipped
counts with their reasons.
"""
from __future__ import annotations

from typing import Any

from harness.cases import set_label_summary, tier_index


def _pct(x: Any) -> str:
    return "n/a" if x is None else f"{x * 100:.1f}%"


def _score(x: Any) -> str:
    return "n/a" if x is None else f"{x:.2f}"


def md_table(headers: list[str], rows: list[list[str]]) -> str:
    esc = lambda v: str(v).replace("|", "\\|")  # noqa: E731
    lines = ["| " + " | ".join(esc(h) for h in headers) + " |", "|" + "---|" * len(headers)]
    lines += ["| " + " | ".join(esc(c) for c in r) + " |" for r in rows]
    return "\n".join(lines)


def judge_coverage(run: dict[str, Any]) -> dict[str, int]:
    """Cases decided by an LLM judge vs by deterministic scorers only vs skipped."""
    cov = {"judged": 0, "deterministic": 0, "skipped": 0}
    for c in run["cases"]:
        if c["score"] is None:
            cov["skipped"] += 1
        elif any((chk.get("extra") or {}).get("judge") for chk in c.get("checks") or []):
            cov["judged"] += 1
        else:
            cov["deterministic"] += 1
    return cov


def _tier_tool_rows(summary: dict[str, Any]) -> list[list[str]]:
    rows = []
    per_tier = summary.get("per_tier") or {}
    per_tier_tool = summary.get("per_tier_tool") or {}
    if not per_tier:
        rows += [["", t, st["n"], _pct(st["pass_rate"]), _score(st["avg_score"]), st["skipped"]]
                 for t, st in summary["per_tool"].items()]
    for tier in sorted(per_tier, key=tier_index):
        for tool, st in per_tier_tool.get(tier, {}).items():
            rows.append([tier, tool, st["n"], _pct(st["pass_rate"]), _score(st["avg_score"]), st["skipped"]])
        st = per_tier[tier]
        rows.append([f"**{tier}**", "*(all)*", st["n"], _pct(st["pass_rate"]), _score(st["avg_score"]), st["skipped"]])
    o = summary["overall"]
    rows.append(["**ALL**", "", o["n"], _pct(o["pass_rate"]), _score(o["avg_score"]), o["skipped"]])
    return rows


def _gate_lines(run: dict[str, Any]) -> list[str]:
    out = []
    gates = run.get("gates") or []
    if gates:
        out.append(f"mode `{run.get('gate_mode', 'target')}`")
        out.append("")
        out.append(md_table(["tier", "target", "actual", "met?"],
                            [[g["tier"], f"{g['threshold']:.0%}", "n/a" if g["pass_rate"] is None else f"{g['pass_rate']:.1%}",
                              "yes" if g.get("met", g["passed"]) else "**NO**"] for g in gates]))
    for tier, reason in (run.get("skipped_tiers") or {}).items():
        out.append(f"- tier `{tier}` skipped: {reason}")
    return out or ["- none"]


def _skip_lines(summary: dict[str, Any]) -> list[str]:
    reasons = summary.get("skip_reasons") or {}
    lines = [f"- {n} x {reason}" for reason, n in reasons.items()] or ["- none"]
    unsupported = (summary.get("overall") or {}).get("unsupported") or 0
    if unsupported:
        lines.append(f"- unsupported (status=skipped_unsupported, not tested): **{unsupported}**")
    if summary.get("timeouts"):
        lines.append(f"- timeouts (failed, reason \"timeout\"): **{summary['timeouts']}**")
    return lines


def _coverage_lines(run: dict[str, Any]) -> list[str]:
    cov = judge_coverage(run)
    return [f"- judged by LLM judge: {cov['judged']}", f"- deterministic only: {cov['deterministic']}",
            f"- skipped: {cov['skipped']}", f"- judge version: `{run.get('judge_version', '?')}`"]


def render_run_md(run: dict[str, Any]) -> str:
    s = run["summary"]
    out = [f"# Run: {run['agent']}", "",
           f"- timestamp: {run['timestamp']}" + (f"  label: **{run['label']}**" if run.get("label") else ""),
           f"- agent version: `{run.get('agent_version', '?')}`  set version: `{run.get('set_version', '?')}` "
           f"(`{set_label_summary(run.get('set_labels'))}`)  "
           f"judge: `{run.get('judge_version', '?')}`  harness: `{run.get('harness_version', '?')}`",
           f"- cases: {run['n_cases']}", "",
           "## Tier x tool", "",
           md_table(["tier", "tool", "n", "pass", "avg", "skip"], _tier_tool_rows(s)), "",
           "## Targets", "", *_gate_lines(run), "",
           "## Judge coverage", "", *_coverage_lines(run), "",
           "## Skipped", "", *_skip_lines(s), ""]
    prov = run.get("set_provenance") or {}
    if prov:
        out += ["## External sets", "",
                md_table(["file", "tier", "source", "license"],
                         [[n, p["tier"], p["source"], p["license"]] for n, p in prov.items()]), ""]
    failing = [c for c in run["cases"] if c["passed"] is False]
    out += [f"## Failing cases ({len(failing)})", ""]
    if failing:
        rows = [[c["id"], c.get("tier", "unit"), c["tool"],
                 "; ".join(ch["detail"] for ch in c["checks"] if ch["passed"] is False)] for c in failing]
        out.append(md_table(["case", "tier", "tool", "why"], rows))
    else:
        out.append("none")
    return "\n".join(out) + "\n"


def render_compare_md(cmp: dict[str, Any], run_a: dict[str, Any] | None = None,
                      run_b: dict[str, Any] | None = None) -> str:
    a, b = cmp["agent_a"], cmp["agent_b"]
    sa, sb = cmp["summary_a"], cmp["summary_b"]
    out = [f"# Compare: {a} vs {b}", "",
           f"- A: `{a}` {cmp.get('agent_version_a')} ({cmp['run_a_timestamp']})  "
           f"B: `{b}` {cmp.get('agent_version_b')} ({cmp['run_b_timestamp']})",
           f"- set version: `{cmp.get('set_version_a')}` (`{cmp.get('set_label_a', '?')}`) vs "
           f"`{cmp.get('set_version_b')}` (`{cmp.get('set_label_b', '?')}`)  "
           f"judge: `{cmp.get('judge_version_a')}` vs `{cmp.get('judge_version_b')}`"]
    for w in cmp.get("warnings") or []:
        out.append(f"- **WARNING:** {w}")
    out += ["", "## Tier x tool", ""]
    rows = []
    tiers = sorted(set(sa.get("per_tier") or {}) | set(sb.get("per_tier") or {}), key=tier_index)
    for tier in tiers:
        ta, tb = (sa.get("per_tier_tool") or {}).get(tier, {}), (sb.get("per_tier_tool") or {}).get(tier, {})
        for tool in sorted(set(ta) | set(tb)):
            pa, pb = ta.get(tool, {}), tb.get(tool, {})
            pt = (cmp.get("per_tier_tool") or {}).get(tier, {}).get(tool, {})
            rows.append([tier, tool, _pct(pa.get("pass_rate")), _pct(pb.get("pass_rate")),
                         _score(pa.get("avg_score")), _score(pb.get("avg_score")),
                         f"{pt.get('win', 0)}/{pt.get('loss', 0)}/{pt.get('tie', 0)}"])
        pa, pb = sa["per_tier"].get(tier, {}), sb["per_tier"].get(tier, {})
        pt = (cmp.get("per_tier") or {}).get(tier, {})
        rows.append([f"**{tier}**", "*(all)*", _pct(pa.get("pass_rate")), _pct(pb.get("pass_rate")),
                     _score(pa.get("avg_score")), _score(pb.get("avg_score")),
                     f"{pt.get('win', 0)}/{pt.get('loss', 0)}/{pt.get('tie', 0)}"])
    if not tiers:
        for tool in sorted(set(sa["per_tool"]) | set(sb["per_tool"])):
            pa, pb, pt = sa["per_tool"].get(tool, {}), sb["per_tool"].get(tool, {}), cmp["per_tool"].get(tool, {})
            rows.append(["", tool, _pct(pa.get("pass_rate")), _pct(pb.get("pass_rate")),
                         _score(pa.get("avg_score")), _score(pb.get("avg_score")),
                         f"{pt.get('win', 0)}/{pt.get('loss', 0)}/{pt.get('tie', 0)}"])
    oa, ob, ty = sa["overall"], sb["overall"], cmp["tally"]
    rows.append(["**ALL**", "", _pct(oa["pass_rate"]), _pct(ob["pass_rate"]), _score(oa["avg_score"]),
                 _score(ob["avg_score"]), f"{ty['win']}/{ty['loss']}/{ty['tie']}"])
    out.append(md_table(["tier", "tool", f"pass {a}", f"pass {b}", f"avg {a}", f"avg {b}", "B win/loss/tie"], rows))
    verdict = ("B is better" if ty["win"] > ty["loss"] else "A is better" if ty["loss"] > ty["win"] else "no clear winner")
    out += ["", f"**Summary:** {b} wins {ty['win']}, loses {ty['loss']}, ties {ty['tie']}"
            + (f", skipped {ty['skip']}" if ty["skip"] else "") + f" -> {verdict}.", ""]
    regs = [c for c in cmp["cases"] if c["outcome"] == "loss"]
    out += [f"## Regressions in {b} ({len(regs)})", ""]
    if regs:
        out.append(md_table(["case", "tier", "tool", f"{a}", f"{b}", f"{b} answer"],
                            [[c["id"], c.get("tier", "unit"), c["tool"], _score(c["score_a"]), _score(c["score_b"]),
                              " ".join(str(c["answer_b"]).split())[:80]] for c in regs]))
    else:
        out.append("none")
    for label, run in (("A", run_a), ("B", run_b)):
        if run is None:
            continue
        out += ["", f"## {label}: {run['agent']}", "", "Targets:", *_gate_lines(run), "",
                "Judge coverage:", *_coverage_lines(run), "", "Skipped:", *_skip_lines(run["summary"])]
    um = cmp["unmatched"]
    if um["only_in_a"] or um["only_in_b"]:
        out += ["", f"Unmatched cases: only in A {um['only_in_a']}, only in B {um['only_in_b']}"]
    return "\n".join(out) + "\n"
