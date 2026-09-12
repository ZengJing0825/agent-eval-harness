"""Run one agent over the golden set and persist the result as JSON."""
from __future__ import annotations

import importlib
import importlib.util
import json
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from harness.cases import Case, load_cases
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


def run_agent(agent_name: str, agent: AgentFn, cases: list[Case]) -> dict[str, Any]:
    """Execute the agent on each case, catching exceptions as failed cases."""
    rows = []
    for case in cases:
        t0 = time.perf_counter()
        try:
            out = agent(case.prompt, dict(case.context))
            if not isinstance(out, dict) or "answer" not in out:
                raise TypeError("agent must return a dict with an 'answer' key")
            error = None
        except Exception:  # noqa: BLE001 - an agent crash is a scored failure, not a harness crash
            out, error = {"answer": "", "citations": []}, traceback.format_exc(limit=2)
        latency_ms = round((time.perf_counter() - t0) * 1000, 2)
        row = {
            "id": case.id, "tool": case.tool, "prompt": case.prompt, "tags": case.tags,
            "answer": out.get("answer", ""), "citations": list(out.get("citations") or []),
            "latency_ms": latency_ms, "error": error,
        }
        row.update(score_case(case, out))
        rows.append(row)
    return {
        "agent": agent_name,
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "n_cases": len(rows),
        "summary": summarise(rows),
        "cases": rows,
    }


def summarise(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Per-tool and overall pass rate / average score (skipped cases excluded)."""
    by_tool: dict[str, list[dict]] = {}
    for r in rows:
        by_tool.setdefault(r["tool"], []).append(r)

    def stats(group: list[dict]) -> dict[str, Any]:
        scored = [r for r in group if r["score"] is not None]
        return {
            "n": len(group),
            "skipped": len(group) - len(scored),
            "pass_rate": (sum(1 for r in scored if r["passed"]) / len(scored)) if scored else None,
            "avg_score": (sum(r["score"] for r in scored) / len(scored)) if scored else None,
        }

    return {"overall": stats(rows), "per_tool": {t: stats(g) for t, g in sorted(by_tool.items())}}


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
        runs_dir: Path | str = DEFAULT_RUNS_DIR, tools: list[str] | None = None) -> tuple[dict, Path]:
    """Convenience: load agent + cases, run, save. Returns (run, path)."""
    agent = load_agent(agent_name)
    cases = load_cases(golden_dir, tools)
    result = run_agent(Path(agent_name).stem, agent, cases)
    return result, save_run(result, runs_dir)
