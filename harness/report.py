"""Plain-text reports for runs and comparisons (no third-party table libs)."""
from __future__ import annotations

from typing import Any


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


def render_run(run: dict[str, Any], verbose: bool = False) -> str:
    """Per-tool pass rate / avg score, plus the list of failing cases."""
    s = run["summary"]
    out = [f"Run: agent={run['agent']}  timestamp={run['timestamp']}  cases={run['n_cases']}", ""]
    rows = [[tool, str(st["n"]), _fmt_pct(st["pass_rate"]), _fmt_score(st["avg_score"]), str(st["skipped"])]
            for tool, st in s["per_tool"].items()]
    o = s["overall"]
    rows.append(["ALL", str(o["n"]), _fmt_pct(o["pass_rate"]), _fmt_score(o["avg_score"]), str(o["skipped"])])
    out.append(table(["tool", "n", "pass", "avg", "skip"], rows))
    failing = [c for c in run["cases"] if c["passed"] is False]
    if failing:
        out += ["", f"Failing cases ({len(failing)}):"]
        for c in failing:
            reason = "; ".join(ch["detail"] for ch in c["checks"] if ch["passed"] is False)
            out.append(f"  - {c['id']} [{c['tool']}] {_trim(c['prompt'])}\n      got: {_trim(c['answer'], 70)!r}\n      why: {reason}")
    if verbose:
        out += ["", "All cases:"]
        for c in run["cases"]:
            mark = "?" if c["passed"] is None else ("PASS" if c["passed"] else "FAIL")
            out.append(f"  {mark:4} {c['id']:<14} {_fmt_score(c['score'])}  {_trim(c['answer'], 60)}")
    return "\n".join(out)


def render_compare(cmp: dict[str, Any]) -> str:
    """Side-by-side per-tool table, per-case win/loss/tie, and a verdict line."""
    a, b = cmp["agent_a"], cmp["agent_b"]
    out = [f"Compare: A={a} ({cmp['run_a_timestamp']})  vs  B={b} ({cmp['run_b_timestamp']})", ""]
    tools = sorted(set(cmp["summary_a"]["per_tool"]) | set(cmp["summary_b"]["per_tool"]))
    rows = []
    for t in tools:
        sa = cmp["summary_a"]["per_tool"].get(t, {})
        sb = cmp["summary_b"]["per_tool"].get(t, {})
        pt = cmp["per_tool"].get(t, {})
        rows.append([t, _fmt_pct(sa.get("pass_rate")), _fmt_pct(sb.get("pass_rate")),
                     _fmt_score(sa.get("avg_score")), _fmt_score(sb.get("avg_score")),
                     f"{pt.get('win', 0)}/{pt.get('loss', 0)}/{pt.get('tie', 0)}"])
    oa, ob = cmp["summary_a"]["overall"], cmp["summary_b"]["overall"]
    ty = cmp["tally"]
    rows.append(["ALL", _fmt_pct(oa["pass_rate"]), _fmt_pct(ob["pass_rate"]),
                 _fmt_score(oa["avg_score"]), _fmt_score(ob["avg_score"]), f"{ty['win']}/{ty['loss']}/{ty['tie']}"])
    out.append(table(["tool", f"pass {a}", f"pass {b}", f"avg {a}", f"avg {b}", "B win/loss/tie"], rows))

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
