"""Plain-text reports for runs and comparisons (no third-party table libs)."""
from __future__ import annotations

from typing import Any

from harness.cases import set_label_summary, tier_index


def _fmt_pct(x: float | None) -> str:
    return "  n/a" if x is None else f"{x * 100:5.1f}%"


def _fmt_score(x: float | None) -> str:
    return " n/a" if x is None else f"{x:.2f}"


def table(headers: list[str], rows: list[list[str]]) -> str:
    """Render a monospace table with left-aligned columns."""
    widths = [max(len(str(h)), *(len(str(r[i])) for r in rows)) if rows else len(h)
              for i, h in enumerate(headers)]
    line = "  ".join(str(h).ljust(w) for h, w in zip(headers, widths))
    sep = "  ".join("-" * w for w in widths)
    body = ["  ".join(str(c).ljust(w) for c, w in zip(r, widths)) for r in rows]
    return "\n".join([line, sep, *body])


def _trim(text: str, n: int = 48) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[: n - 1] + "…"


def _stat_row(label: str, st: dict[str, Any]) -> list[str]:
    return [label, str(st["n"]), _fmt_pct(st["pass_rate"]), _fmt_score(st["avg_score"]), str(st["skipped"])]


def tier_tool_rows(summary: dict[str, Any]) -> list[list[str]]:
    """Rows for a tier x tool table: one line per tool, a subtotal per tier, ALL at the end."""
    rows: list[list[str]] = []
    per_tier_tool = summary.get("per_tier_tool") or {}
    per_tier = summary.get("per_tier") or {}
    if not per_tier:  # runs saved before tiers existed
        rows += [_stat_row(tool, st) for tool, st in summary["per_tool"].items()]
    for tier in sorted(per_tier, key=tier_index):
        for tool, st in per_tier_tool.get(tier, {}).items():
            rows.append([tier, tool, *_stat_row("", st)[1:]])
        rows.append([tier, "(all)", *_stat_row("", per_tier[tier])[1:]])
    rows.append(["ALL", "", *_stat_row("", summary["overall"])[1:]])
    return rows


def gate_rows(run: dict[str, Any]) -> list[list[str]]:
    """``[tier, target, actual, met?]`` per gate."""
    rows = []
    for g in run.get("gates") or []:
        rate = "n/a (nothing scored)" if g["pass_rate"] is None else f"{g['pass_rate']:.1%}"
        rows.append([g["tier"], f"{g['threshold']:.0%}", rate, "yes" if g.get("met", g["passed"]) else "NO"])
    return rows


def render_gates(run: dict[str, Any]) -> list[str]:
    rows = gate_rows(run)
    if not rows and not run.get("skipped_tiers"):
        return []
    mode = run.get("gate_mode", "target")
    out = [f"  mode={mode}" + ("  (unmet target skips later tiers)" if mode == "strict"
                                 else "  (targets are recorded; every tier still runs)")]
    if rows:
        out += ["  " + line for line in table(["tier", "target", "actual", "met?"], rows).splitlines()]
    for tier, reason in (run.get("skipped_tiers") or {}).items():
        out.append(f"  tier {tier} skipped: {reason}")
    return out


def render_run(run: dict[str, Any], verbose: bool = False) -> str:
    """Tier x tool pass rate / avg score, gate results, plus the list of failing cases."""
    s = run["summary"]
    out = [f"Run: agent={run['agent']}  timestamp={run['timestamp']}  cases={run['n_cases']}"
           + (f"  label={run['label']!r}" if run.get("label") else ""),
           f"Versions: agent={run.get('agent_version', '?')}  set={run.get('set_version', '?')} "
           f"({set_label_summary(run.get('set_labels'))})  "
           f"judge={run.get('judge_version', '?')}  harness={run.get('harness_version', '?')}"
           + (f"  as_of={run['as_of']}" if run.get("as_of") else ""), ""]
    if s.get("per_tier"):
        out.append(table(["tier", "tool", "n", "pass", "avg", "skip"], tier_tool_rows(s)))
    else:
        rows = [_stat_row(tool, st) for tool, st in s["per_tool"].items()]
        rows.append(_stat_row("ALL", s["overall"]))
        out.append(table(["tool", "n", "pass", "avg", "skip"], rows))
    gates = render_gates(run)
    if gates:
        out += ["", "Targets:", *gates]
    from harness.markdown import judge_coverage  # local import: markdown depends on cases only
    cov = judge_coverage(run)
    out += ["", f"Judge coverage: {cov['judged']} judged, {cov['deterministic']} deterministic, "
            f"{cov['skipped']} skipped (judge={run.get('judge_version', '?')})"]
    reasons = s.get("skip_reasons") or {}
    if reasons:
        out += ["", "Skipped:"] + [f"  {n:3d}  {reason}" for reason, n in reasons.items()]
    if s.get("timeouts"):
        out += ["", f"Timeouts: {s['timeouts']} case(s) failed with reason \"timeout\" (limit {run.get('timeout')}s)"]
    unsupported = (s.get("overall") or {}).get("unsupported") or 0
    if unsupported:
        per_tool = {t: st["unsupported"] for t, st in s["per_tool"].items() if st.get("unsupported")}
        out += ["", f"Unsupported (status=skipped_unsupported, not tested): {unsupported}  "
                + ", ".join(f"{t}={n}" for t, n in per_tool.items())]
    prov = run.get("set_provenance") or {}
    if prov:
        out += ["", "External sets:"] + [f"  {name}: tier={p['tier']} source={p['source']!r} license={p['license']!r}"
                                          for name, p in prov.items()]
    failing = [c for c in run["cases"] if c["passed"] is False]
    if failing:
        out += ["", f"Failing cases ({len(failing)}):"]
        for c in failing:
            reason = "; ".join(ch["detail"] for ch in c["checks"] if ch["passed"] is False)
            out.append(f"  - {c['id']} [{c.get('tier', 'unit')}/{c['tool']}] {_trim(c['prompt'])}\n"
                       f"      got: {_trim(c['answer'], 70)!r}\n      why: {reason}")
    if verbose:
        out += ["", "All cases:"]
        for c in run["cases"]:
            mark = "SKIP" if c["passed"] is None else ("PASS" if c["passed"] else "FAIL")
            out.append(f"  {mark:4} {c['id']:<14} {_fmt_score(c['score'])}  {_trim(c['answer'], 60)}")
    return "\n".join(out)


def render_compare(cmp: dict[str, Any]) -> str:
    """Side-by-side tier/tool table, per-case win/loss/tie, and a verdict line."""
    a, b = cmp["agent_a"], cmp["agent_b"]
    out = [f"Compare: A={a} ({cmp['run_a_timestamp']})  vs  B={b} ({cmp['run_b_timestamp']})"
           + (f"  labels {cmp.get('label_a')!r} vs {cmp.get('label_b')!r}" if cmp.get("label_a") or cmp.get("label_b") else ""),
           f"Versions: agent {cmp.get('agent_version_a')} vs {cmp.get('agent_version_b')}  "
           f"set {cmp.get('set_version_a')} ({cmp.get('set_label_a', '?')}) vs "
           f"{cmp.get('set_version_b')} ({cmp.get('set_label_b', '?')})  "
           f"judge {cmp.get('judge_version_a')} vs {cmp.get('judge_version_b')}"]
    for w in cmp.get("warnings") or []:
        out.append(f"!!! WARNING: {w}")
    out.append("")
    sa_all, sb_all = cmp["summary_a"], cmp["summary_b"]
    rows = []
    # Per tier x tool when both runs know about tiers, else the flat per-tool view.
    tiers = sorted(set(sa_all.get("per_tier") or {}) | set(sb_all.get("per_tier") or {}), key=tier_index)
    for tier in tiers:
        ta = (sa_all.get("per_tier_tool") or {}).get(tier, {})
        tb = (sb_all.get("per_tier_tool") or {}).get(tier, {})
        for t in sorted(set(ta) | set(tb)):
            sa, sb = ta.get(t, {}), tb.get(t, {})
            pt = (cmp.get("per_tier_tool") or {}).get(tier, {}).get(t, {})
            rows.append([tier, t, _fmt_pct(sa.get("pass_rate")), _fmt_pct(sb.get("pass_rate")),
                         _fmt_score(sa.get("avg_score")), _fmt_score(sb.get("avg_score")),
                         f"{pt.get('win', 0)}/{pt.get('loss', 0)}/{pt.get('tie', 0)}"])
        sa, sb = sa_all["per_tier"].get(tier, {}), sb_all["per_tier"].get(tier, {})
        pt = cmp.get("per_tier", {}).get(tier, {})
        rows.append([tier, "(all)", _fmt_pct(sa.get("pass_rate")), _fmt_pct(sb.get("pass_rate")),
                     _fmt_score(sa.get("avg_score")), _fmt_score(sb.get("avg_score")),
                     f"{pt.get('win', 0)}/{pt.get('loss', 0)}/{pt.get('tie', 0)}"])
    if not tiers:
        for t in sorted(set(sa_all["per_tool"]) | set(sb_all["per_tool"])):
            sa, sb = sa_all["per_tool"].get(t, {}), sb_all["per_tool"].get(t, {})
            pt = cmp["per_tool"].get(t, {})
            rows.append(["", t, _fmt_pct(sa.get("pass_rate")), _fmt_pct(sb.get("pass_rate")),
                         _fmt_score(sa.get("avg_score")), _fmt_score(sb.get("avg_score")),
                         f"{pt.get('win', 0)}/{pt.get('loss', 0)}/{pt.get('tie', 0)}"])
    oa, ob = sa_all["overall"], sb_all["overall"]
    ty = cmp["tally"]
    rows.append(["ALL", "", _fmt_pct(oa["pass_rate"]), _fmt_pct(ob["pass_rate"]),
                 _fmt_score(oa["avg_score"]), _fmt_score(ob["avg_score"]), f"{ty['win']}/{ty['loss']}/{ty['tie']}"])
    out.append(table(["tier", "tool", f"pass {a}", f"pass {b}", f"avg {a}", f"avg {b}", "B win/loss/tie"], rows))

    out += ["", "Per-case (outcome is for B relative to A):"]
    case_rows = [[c["id"], c["tool"], _fmt_score(c["score_a"]), _fmt_score(c["score_b"]), c["outcome"].upper(),
                  _trim(c["answer_b"], 40)] for c in cmp["cases"]]
    out.append(table(["case", "tool", a, b, "outcome", f"{b} answer"], case_rows))

    verdict = ("B is better" if ty["win"] > ty["loss"] else "A is better" if ty["loss"] > ty["win"] else "no clear winner")
    regressions = [c["id"] for c in cmp["cases"] if c["outcome"] == "loss"]
    out += ["", f"Summary: {b} wins {ty['win']}, loses {ty['loss']}, ties {ty['tie']}"
            + (f", skipped {ty['skip']}" if ty["skip"] else "") + f" -> {verdict}."]
    if regressions:
        out.append(f"Regressions in {b}: {', '.join(regressions)}")
    um = cmp["unmatched"]
    if um["only_in_a"] or um["only_in_b"]:
        out.append(f"Unmatched cases: only in A {um['only_in_a']}, only in B {um['only_in_b']}")
    return "\n".join(out)
