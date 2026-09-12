"""Experiment matrix: agents x tiers (x tools) on one golden-set version.

A single number per agent hides where it moved. The matrix keeps the three
version axes explicit (set x agent x judge) and reports, for every agent
after the first, which cases regressed against that first agent.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from harness import compare as compare_mod
from harness import runner
from harness.cases import set_label_summary, tier_index


def collect_runs(agents: list[str], golden_dir: str = "cases/golden", runs_dir: str = "runs",
                 tools: Optional[list[str]] = None, tiers: Optional[list[str]] = None,
                 gates: Optional[dict[str, float]] = None, reuse: bool = False,
                 include_unagreed: bool = False, as_of: Optional[str] = None,
                 gate_mode: Optional[str] = None, timeout: Optional[float] = runner.DEFAULT_TIMEOUT) -> list[dict[str, Any]]:
    """Run (or with ``reuse`` load the latest saved run of) every agent."""
    runs = []
    for agent in agents:
        if reuse:
            try:
                runs.append(compare_mod.load_run(runner.latest_run(agent, runs_dir)))
                continue
            except FileNotFoundError:
                pass  # nothing saved yet - run it
        result, _ = runner.run(agent, golden_dir, runs_dir, tools, tiers=tiers, gates=gates,
                               include_unagreed=include_unagreed, as_of=as_of, gate_mode=gate_mode, timeout=timeout)
        runs.append(result)
    return runs


def build_matrix(runs: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate several runs into one document; the first run is the reference."""
    if not runs:
        raise ValueError("matrix needs at least one run")
    tiers = sorted({t for r in runs for t in (r["summary"].get("per_tier") or {})}, key=tier_index)
    tools = sorted({t for r in runs for t in r["summary"]["per_tool"]})
    set_versions = {r.get("set_version") for r in runs}
    set_labels = {set_label_summary(r.get("set_labels")) for r in runs}
    judge_versions = {r.get("judge_version") for r in runs}
    warnings = []
    if len(set_versions) > 1:
        warnings.append(f"runs use different golden-set versions: {sorted(map(str, set_versions))}")
    if len(judge_versions) > 1:
        warnings.append(f"runs use different judge versions: {sorted(map(str, judge_versions))}")
    ref = runs[0]
    agents = []
    for r in runs:
        entry = {
            "agent": r["agent"], "agent_version": r.get("agent_version"), "timestamp": r["timestamp"],
            "set_version": r.get("set_version"), "set_label": set_label_summary(r.get("set_labels")),
            "label": r.get("label"), "judge_version": r.get("judge_version"),
            "overall": r["summary"]["overall"],
            "per_tier": r["summary"].get("per_tier") or {},
            "per_tool": r["summary"]["per_tool"],
            "per_tier_tool": r["summary"].get("per_tier_tool") or {},
            "gates": r.get("gates") or [],
            "gate_mode": r.get("gate_mode", "target"),
            "skipped_tiers": r.get("skipped_tiers") or {},
            "regressions": [], "improvements": [],
        }
        if r is not ref:
            cmp = compare_mod.compare_runs(ref, r)
            entry["regressions"] = compare_mod.regressions(cmp)
            entry["improvements"] = [c["id"] for c in cmp["cases"] if c["outcome"] == "win"]
            entry["tally"] = cmp["tally"]
        agents.append(entry)
    return {"reference": ref["agent"], "tiers": tiers, "tools": tools, "agents": agents,
            "set_versions": sorted(map(str, set_versions)), "set_labels": sorted(set_labels),
            "judge_versions": sorted(map(str, judge_versions)), "warnings": warnings}


def _cell(st: Optional[dict[str, Any]]) -> str:
    if not st or st.get("pass_rate") is None:
        return "n/a"
    return f"{st['pass_rate'] * 100:.0f}% / {st['avg_score']:.2f}"


def render_text(m: dict[str, Any], verbose: bool = False) -> str:
    from harness.report import table  # local import: report imports nothing from here

    out = [f"Matrix: reference={m['reference']}  set={','.join(m['set_versions'])} ({','.join(m.get('set_labels') or ['?'])})  "
           f"judge={','.join(m['judge_versions'])}"]
    for w in m["warnings"]:
        out.append(f"!!! WARNING: {w}")
    out.append("")
    headers = ["agent", "version", *m["tiers"], "ALL"]
    rows = []
    for a in m["agents"]:
        rows.append([a["agent"], str(a["agent_version"]), *[_cell(a["per_tier"].get(t)) for t in m["tiers"]],
                     _cell(a["overall"])])
    out.append("cells are pass rate / avg score")
    out.append(table(headers, rows))
    if verbose:
        out += ["", "Per tool:"]
        rows = []
        for tool in m["tools"]:
            for a in m["agents"]:
                per_tier = {t: a["per_tier_tool"].get(t, {}).get(tool) for t in m["tiers"]}
                rows.append([tool, a["agent"], *[_cell(per_tier[t]) for t in m["tiers"]], _cell(a["per_tool"].get(tool))])
        out.append(table(["tool", "agent", *m["tiers"], "ALL"], rows))
    for a in m["agents"][1:]:
        out.append("")
        t = a.get("tally", {})
        out.append(f"{a['agent']} vs {m['reference']}: wins {t.get('win', 0)}, losses {t.get('loss', 0)}, "
                   f"ties {t.get('tie', 0)}")
        out.append(f"  regressions: {', '.join(a['regressions']) or 'none'}")
    for a in m["agents"]:
        for g in a["gates"]:
            if not g["passed"]:
                rate = "n/a" if g["pass_rate"] is None else f"{g['pass_rate']:.1%}"
                out.append(f"  target missed: {a['agent']} {g['tier']} {g['threshold']:.0%} -> {rate}"
                           + (" (later tiers skipped)" if a.get("gate_mode") == "strict" else ""))
    return "\n".join(out)


def render_markdown(m: dict[str, Any], verbose: bool = False) -> str:
    out = ["# Experiment matrix", "",
           f"- reference agent: `{m['reference']}`",
           f"- golden-set version(s): `{', '.join(m['set_versions'])}` (label(s): `{', '.join(m.get('set_labels') or ['?'])}`)",
           f"- judge version(s): `{', '.join(m['judge_versions'])}`"]
    for w in m["warnings"]:
        out.append(f"- **WARNING:** {w}")
    out += ["", "Cells are pass rate / average score.", "",
            "| agent | version | " + " | ".join(m["tiers"]) + " | ALL |",
            "|---|---|" + "---|" * (len(m["tiers"]) + 1)]
    for a in m["agents"]:
        out.append(f"| {a['agent']} | {a['agent_version']} | "
                   + " | ".join(_cell(a["per_tier"].get(t)) for t in m["tiers"]) + f" | {_cell(a['overall'])} |")
    if verbose:
        out += ["", "## Per tool", "", "| tool | agent | " + " | ".join(m["tiers"]) + " | ALL |",
                "|---|---|" + "---|" * (len(m["tiers"]) + 1)]
        for tool in m["tools"]:
            for a in m["agents"]:
                out.append(f"| {tool} | {a['agent']} | "
                           + " | ".join(_cell(a["per_tier_tool"].get(t, {}).get(tool)) for t in m["tiers"])
                           + f" | {_cell(a['per_tool'].get(tool))} |")
    out += ["", "## Regressions vs reference", ""]
    for a in m["agents"][1:]:
        t = a.get("tally", {})
        out.append(f"- **{a['agent']}**: wins {t.get('win', 0)}, losses {t.get('loss', 0)}, ties {t.get('tie', 0)}; "
                   f"regressions: {', '.join(f'`{c}`' for c in a['regressions']) or 'none'}")
    hits = [(a, g) for a in m["agents"] for g in a["gates"] if not g["passed"]]
    if hits:
        out += ["", "## Targets missed", ""]
        for a, g in hits:
            rate = "n/a" if g["pass_rate"] is None else f"{g['pass_rate']:.1%}"
            out.append(f"- `{a['agent']}`: {g['tier']} target {g['threshold']:.0%}, actual {rate}"
                       + (" (later tiers skipped)" if a.get("gate_mode") == "strict" else ""))
    return "\n".join(out) + "\n"


def write(m: dict[str, Any], out: str, verbose: bool = False) -> Path:
    path = Path(out)
    if path.suffix.lower() == ".md":
        path.write_text(render_markdown(m, verbose), encoding="utf-8")
    else:
        path.write_text(json.dumps(m, indent=2, ensure_ascii=False), encoding="utf-8")
    return path
