"""Run one agent over the golden set and persist the result as JSON.

Cases are executed tier by tier (``unit`` -> ``complex`` -> ``external`` ->
``dynamic``). A *gate* such as ``unit:0.9`` stops the run after the ``unit``
tier when its pass rate is below 0.9; the remaining tiers are recorded as
skipped with a reason rather than silently omitted.
"""
from __future__ import annotations

import importlib
import importlib.util
import json
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from harness.cases import TIERS, Case, load_cases, tier_index
from harness.scorers import run_check

AgentFn = Callable[[str, dict], dict]
DEFAULT_RUNS_DIR = Path("runs")


def load_agent(name: str) -> AgentFn:
    """Resolve an agent by name.

    Accepts a bare name (``baseline`` -> ``agents/baseline.py``), a dotted
    module path (``mypkg.agent``) or a file path (``path/to/agent.py``).
    The module must expose ``answer(prompt: str, context: dict) -> dict``.
    """
    if name.endswith(".py"):
        spec = importlib.util.spec_from_file_location(Path(name).stem, name)
        module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
        assert spec and spec.loader
        spec.loader.exec_module(module)
    else:
        sys.path.insert(0, str(Path.cwd()))
        try:
            module = importlib.import_module(name)
        except ModuleNotFoundError:
            module = importlib.import_module(f"agents.{name}")
    fn = getattr(module, "answer", None)
    if not callable(fn):
        raise AttributeError(f"agent {name!r} has no callable answer(prompt, context)")
    return fn


def parse_gates(specs: Optional[list[str]]) -> dict[str, float]:
    """``["unit:0.9", "complex:0.8,external:0.5"]`` -> ``{"unit": 0.9, ...}``."""
    gates: dict[str, float] = {}
    for spec in specs or []:
        for part in str(spec).split(","):
            part = part.strip()
            if not part:
                continue
            if ":" not in part:
                raise ValueError(f"gate {part!r}: expected <tier>:<min pass rate>, e.g. unit:0.9")
            tier, threshold = part.split(":", 1)
            tier = tier.strip()
            if tier not in TIERS:
                raise ValueError(f"gate {part!r}: unknown tier {tier!r}; known: {list(TIERS)}")
            try:
                value = float(threshold)
            except ValueError:
                raise ValueError(f"gate {part!r}: threshold must be a number between 0 and 1") from None
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"gate {part!r}: threshold must be between 0 and 1")
            gates[tier] = value
    return gates


def score_case(case: Case, answer: dict[str, Any]) -> dict[str, Any]:
    """Apply every check of a case and aggregate: score = mean, pass = all."""
    results = []
    for check in case.checks:
        s = run_check(answer, check, case.prompt)
        results.append({"type": check["type"], "score": s.score, "passed": s.passed, "detail": s.detail})
    scored = [r for r in results if r["score"] is not None]
    if not scored:  # every check was skipped (e.g. judge-only case without a key)
        return {"score": None, "passed": None, "checks": results}
    return {
        "score": sum(r["score"] for r in scored) / len(scored),
        "passed": all(r["passed"] for r in scored),
        "checks": results,
    }


def _base_row(case: Case) -> dict[str, Any]:
    return {"id": case.id, "tool": case.tool, "tier": case.tier, "prompt": case.prompt, "tags": case.tags}


def skipped_row(case: Case, reason: str) -> dict[str, Any]:
    """A row for a case that was not executed (gate, status, ...)."""
    row = _base_row(case)
    row.update({"answer": "", "citations": [], "latency_ms": 0.0, "error": None,
                "score": None, "passed": None, "checks": [], "skip_reason": reason})
    return row


def execute_case(agent: AgentFn, case: Case) -> dict[str, Any]:
    """Run the agent on one case and score it; an agent crash is a failed case."""
    t0 = time.perf_counter()
    try:
        out = agent(case.prompt, dict(case.context))
        if not isinstance(out, dict) or "answer" not in out:
            raise TypeError("agent must return a dict with an 'answer' key")
        error = None
    except Exception:  # noqa: BLE001 - an agent crash is a scored failure, not a harness crash
        out, error = {"answer": "", "citations": []}, traceback.format_exc(limit=2)
    latency_ms = round((time.perf_counter() - t0) * 1000, 2)
    row = _base_row(case)
    row.update({"answer": out.get("answer", ""), "citations": list(out.get("citations") or []),
                "latency_ms": latency_ms, "error": error})
    row.update(score_case(case, out))
    return row


def run_agent(agent_name: str, agent: AgentFn, cases: list[Case],
              gates: Optional[dict[str, float]] = None) -> dict[str, Any]:
    """Execute the agent tier by tier, honouring gates.

    Returns the run document. ``run["gates"]`` lists every gate that was
    evaluated (tier, threshold, observed pass rate, passed) and
    ``run["skipped_tiers"]`` names the tiers that were not executed.
    """
    gates = gates or {}
    ordered = sorted(cases, key=lambda c: (tier_index(c.tier), cases.index(c)))
    rows: list[dict[str, Any]] = []
    gate_log: list[dict[str, Any]] = []
    skipped_tiers: dict[str, str] = {}
    blocked_by: Optional[str] = None

    for tier in TIERS:
        tier_cases = [c for c in ordered if c.tier == tier]
        if not tier_cases:
            continue
        if blocked_by:
            skipped_tiers[tier] = blocked_by
            rows.extend(skipped_row(c, blocked_by) for c in tier_cases)
            continue
        tier_rows = [execute_case(agent, c) for c in tier_cases]
        rows.extend(tier_rows)
        if tier in gates:
            scored = [r for r in tier_rows if r["score"] is not None]
            rate = (sum(1 for r in scored if r["passed"]) / len(scored)) if scored else None
            ok = rate is None or rate >= gates[tier]
            gate_log.append({"tier": tier, "threshold": gates[tier], "pass_rate": rate, "passed": ok,
                             "n_scored": len(scored)})
            if not ok:
                blocked_by = f"gate {tier}:{gates[tier]:g} failed (pass rate {rate:.1%})"

    return {
        "agent": agent_name,
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "n_cases": len(rows),
        "gates": gate_log,
        "skipped_tiers": skipped_tiers,
        "summary": summarise(rows),
        "cases": rows,
    }


def _stats(group: list[dict]) -> dict[str, Any]:
    scored = [r for r in group if r["score"] is not None]
    return {
        "n": len(group),
        "skipped": len(group) - len(scored),
        "pass_rate": (sum(1 for r in scored if r["passed"]) / len(scored)) if scored else None,
        "avg_score": (sum(r["score"] for r in scored) / len(scored)) if scored else None,
    }


def summarise(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Overall, per-tool, per-tier and tier x tool stats (skipped cases excluded from rates)."""
    by_tool: dict[str, list[dict]] = {}
    by_tier: dict[str, list[dict]] = {}
    by_tier_tool: dict[str, dict[str, list[dict]]] = {}
    reasons: dict[str, int] = {}
    for r in rows:
        tier = r.get("tier", "unit")
        by_tool.setdefault(r["tool"], []).append(r)
        by_tier.setdefault(tier, []).append(r)
        by_tier_tool.setdefault(tier, {}).setdefault(r["tool"], []).append(r)
        if r["score"] is None:
            reason = r.get("skip_reason") or _skip_reason_from_checks(r)
            reasons[reason] = reasons.get(reason, 0) + 1
    tiers_sorted = sorted(by_tier, key=tier_index)
    return {
        "overall": _stats(rows),
        "per_tool": {t: _stats(g) for t, g in sorted(by_tool.items())},
        "per_tier": {t: _stats(by_tier[t]) for t in tiers_sorted},
        "per_tier_tool": {t: {tool: _stats(g) for tool, g in sorted(by_tier_tool[t].items())} for t in tiers_sorted},
        "skip_reasons": dict(sorted(reasons.items())),
    }


def _skip_reason_from_checks(row: dict[str, Any]) -> str:
    details = [c.get("detail", "") for c in row.get("checks", []) if c.get("score") is None]
    return details[0] if details else "skipped"


def save_run(run: dict[str, Any], runs_dir: Path | str = DEFAULT_RUNS_DIR) -> Path:
    runs_dir = Path(runs_dir)
    runs_dir.mkdir(parents=True, exist_ok=True)
    stamp = run["timestamp"].replace(":", "").replace("-", "")
    path = runs_dir / f"{run['agent']}-{stamp}.json"
    path.write_text(json.dumps(run, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def latest_run(agent: str, runs_dir: Path | str = DEFAULT_RUNS_DIR) -> Path:
    """Most recent saved run for ``agent`` (by filename timestamp)."""
    matches = sorted(Path(runs_dir).glob(f"{agent}-*.json"))
    if not matches:
        raise FileNotFoundError(f"no runs for agent {agent!r} in {runs_dir}")
    return matches[-1]


def run(agent_name: str, golden_dir: Path | str = "cases/golden",
        runs_dir: Path | str = DEFAULT_RUNS_DIR, tools: Optional[list[str]] = None,
        tiers: Optional[list[str]] = None, gates: Optional[dict[str, float]] = None) -> tuple[dict, Path]:
    """Convenience: load agent + cases, run, save. Returns (run, path)."""
    agent = load_agent(agent_name)
    cases = load_cases(golden_dir, tools, tiers)
    result = run_agent(Path(agent_name).stem, agent, cases, gates=gates)
    return result, save_run(result, runs_dir)
