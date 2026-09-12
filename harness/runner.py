"""Run one agent over the golden set and persist the result as JSON.

Cases are executed tier by tier (``unit`` -> ``complex`` -> ``external`` ->
``dynamic``). A *gate* such as ``unit:0.9`` is a per-tier target. In the
default ``target`` mode the run records whether each target was met and
still runs the later tiers; in ``strict`` mode an unmet target stops the
run and the remaining tiers are recorded as skipped with a reason rather
than silently omitted. Targets can also live in an optional ``harness.yaml``
at the repo root (``targets: {unit: 0.8, complex: 0.8}``); ``--gate`` on the
command line overrides them per tier.
"""
from __future__ import annotations

import copy
import importlib
import importlib.util
import json
import re
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from dataclasses import replace

from harness import __version__ as HARNESS_VERSION
from harness import judge as judge_mod
from harness.cases import TIERS, Case, load_cases, set_files, set_labels, set_provenance, set_version, tier_index
from harness.scorers import run_check

AgentFn = Callable[[str, dict], dict]
DEFAULT_RUNS_DIR = Path("runs")
DEFAULT_CONFIG = Path("harness.yaml")
UNSUPPORTED_REASON = "unsupported"
GATE_MODES = ("target", "strict")
DEFAULT_GATE_MODE = "target"


def load_agent_module(name: str):
    """Import an agent module by bare name, dotted path or ``.py`` file path."""
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
    return module


def load_agent(name: str) -> AgentFn:
    """Resolve an agent by name.

    Accepts a bare name (``baseline`` -> ``agents/baseline.py``), a dotted
    module path (``mypkg.agent``) or a file path (``path/to/agent.py``).
    The module must expose ``answer(prompt: str, context: dict) -> dict``.
    """
    fn = getattr(load_agent_module(name), "answer", None)
    if not callable(fn):
        raise AttributeError(f"agent {name!r} has no callable answer(prompt, context)")
    return fn


def agent_version(name: str) -> str:
    """The module's ``VERSION`` attribute, or ``"unversioned"``."""
    try:
        return str(getattr(load_agent_module(name), "VERSION", None) or "unversioned")
    except Exception:  # noqa: BLE001 - version lookup must never break a run
        return "unversioned"


PLACEHOLDER_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


def today_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def substitute(value: Any, values: dict[str, Any]) -> Any:
    """Replace ``{name}`` placeholders (known names only) in strings, recursively in dicts/lists."""
    if isinstance(value, str):
        return PLACEHOLDER_RE.sub(lambda m: str(values[m.group(1)]) if m.group(1) in values else m.group(0), value)
    if isinstance(value, dict):
        return {k: substitute(v, values) for k, v in value.items()}
    if isinstance(value, list):
        return [substitute(v, values) for v in value]
    return value


def load_resolver(spec: str) -> Callable[..., dict]:
    """``"agents.resolvers:earnings_date"`` -> the function."""
    if ":" not in spec:
        raise ValueError(f"resolver {spec!r}: expected module.path:function")
    module_name, func_name = spec.split(":", 1)
    sys.path.insert(0, str(Path.cwd()))
    fn = getattr(importlib.import_module(module_name), func_name, None)
    if not callable(fn):
        raise AttributeError(f"resolver {spec!r}: no callable {func_name!r} in {module_name}")
    return fn


def materialise(case: Case, as_of: Optional[str] = None) -> tuple[Case, dict[str, Any]]:
    """Fill ``{today}``/``{as_of}``/resolver placeholders. Returns (concrete case, resolved values).

    Non-dynamic cases pass through untouched unless they use a placeholder.
    Dynamic-tier cases additionally get ``as_of`` injected into their context.
    """
    as_of = as_of or today_utc()
    values: dict[str, Any] = {"today": today_utc(), "as_of": as_of}
    if case.resolver:
        values.update(load_resolver(case.resolver)(as_of, **substitute(case.resolver_args, values)))
    prompt = substitute(case.prompt, values)
    context = substitute(copy.deepcopy(case.context), values)
    checks = substitute(copy.deepcopy(case.checks), values)
    if case.tier == "dynamic":
        context.setdefault("as_of", as_of)
    resolved = {k: v for k, v in values.items() if k != "today"} if (case.resolver or case.tier == "dynamic") else {}
    return replace(case, prompt=prompt, context=context, checks=checks), resolved


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


def validate_gate_mode(mode: Optional[str]) -> str:
    mode = str(mode or DEFAULT_GATE_MODE).strip().lower()
    if mode not in GATE_MODES:
        raise ValueError(f"gate mode {mode!r}: expected one of {list(GATE_MODES)}")
    return mode


def load_config(path: Path | str | None = DEFAULT_CONFIG) -> dict[str, Any]:
    """The optional ``harness.yaml`` (``targets``, ``gate_mode``); ``{}`` when absent or ``None``."""
    if path is None:
        return {}
    path = Path(path)
    if not path.exists():
        return {}
    import yaml
    with open(path, encoding="utf-8") as fh:
        doc = yaml.safe_load(fh) or {}
    if not isinstance(doc, dict):
        raise ValueError(f"{path}: expected a mapping")
    return doc


def config_targets(config: dict[str, Any]) -> dict[str, float]:
    """``targets: {unit: 0.8, complex: 0.8}`` -> validated ``{tier: rate}``."""
    targets = config.get("targets") or {}
    if not isinstance(targets, dict):
        raise ValueError("harness.yaml: 'targets' must be a mapping of tier -> minimum pass rate")
    return parse_gates([f"{tier}:{rate}" for tier, rate in targets.items()])


def score_case(case: Case, answer: dict[str, Any]) -> dict[str, Any]:
    """Apply every check of a case and aggregate: score = mean, pass = all."""
    results = []
    for check in case.checks:
        s = run_check(answer, check, case.prompt)
        row = {"type": check["type"], "score": s.score, "passed": s.passed, "detail": s.detail}
        if s.extra:
            row["extra"] = s.extra
        results.append(row)
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


def execute_case(agent: AgentFn, case: Case, as_of: Optional[str] = None) -> dict[str, Any]:
    """Run the agent on one case and score it; an agent or resolver crash is a failed case."""
    t0 = time.perf_counter()
    resolved: dict[str, Any] = {}
    try:
        case, resolved = materialise(case, as_of)
    except Exception:  # noqa: BLE001 - a broken resolver/placeholder is a failed case with a traceback
        row = _base_row(case)
        row.update({"answer": "", "citations": [], "latency_ms": 0.0, "error": traceback.format_exc(limit=2),
                    "score": 0.0, "passed": False,
                    "checks": [{"type": "materialise", "score": 0.0, "passed": False,
                                "detail": "could not resolve the case's placeholders (see error)"}]})
        return row
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
    if resolved:
        row["resolved"] = resolved
    row.update(score_case(case, out))
    return row


def run_agent(agent_name: str, agent: AgentFn, cases: list[Case],
              gates: Optional[dict[str, float]] = None, include_unagreed: bool = False,
              meta: Optional[dict[str, Any]] = None, as_of: Optional[str] = None,
              gate_mode: str = DEFAULT_GATE_MODE) -> dict[str, Any]:
    """Execute the agent tier by tier, honouring gates and answer status.

    ``gate_mode`` is ``target`` (record met / not met, keep running) or
    ``strict`` (an unmet gate skips the later tiers).

    Cases whose ``answer.status`` is ``draft`` or ``disputed`` are recorded as
    skipped unless ``include_unagreed`` is set; they never count towards a
    gate. ``skipped_unsupported`` cases are always skipped (reason
    ``"unsupported"``) and counted separately in the summary. Returns the run document. ``run["gates"]`` lists every gate that was
    evaluated (tier, threshold, observed pass rate, passed) and
    ``run["skipped_tiers"]`` names the tiers that were not executed.
    ``meta`` (set/agent/judge versions, selection) is merged into the document.
    ``as_of`` (YYYY-MM-DD, default today) drives the dynamic tier.
    """
    gates = gates or {}
    gate_mode = validate_gate_mode(gate_mode)
    as_of = as_of or today_utc()
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
        tier_rows = [skipped_row(c, UNSUPPORTED_REASON) if c.unsupported
                     else execute_case(agent, c, as_of) if (include_unagreed or c.agreed)
                     else skipped_row(c, f"status={c.status} (not agreed; use --include-unagreed)")
                     for c in tier_cases]
        rows.extend(tier_rows)
        if tier in gates:
            scored = [r for r in tier_rows if r["score"] is not None]
            rate = (sum(1 for r in scored if r["passed"]) / len(scored)) if scored else None
            ok = rate is None or rate >= gates[tier]
            gate_log.append({"tier": tier, "threshold": gates[tier], "pass_rate": rate, "passed": ok, "met": ok,
                             "n_scored": len(scored), "mode": gate_mode})
            if not ok and gate_mode == "strict":
                blocked_by = f"gate {tier}:{gates[tier]:g} failed (pass rate {rate:.1%})"

    return {
        "agent": agent_name,
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "harness_version": HARNESS_VERSION,
        "agent_version": "unversioned",
        "set_version": None,
        "set_labels": {},
        "label": None,
        "judge_version": judge_mod.version_string(),
        "as_of": as_of,
        **(meta or {}),
        "n_cases": len(rows),
        "gate_mode": gate_mode,
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
        "unsupported": sum(1 for r in group if r.get("skip_reason") == UNSUPPORTED_REASON),
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
        tiers: Optional[list[str]] = None, gates: Optional[dict[str, float]] = None,
        include_unagreed: bool = False, as_of: Optional[str] = None,
        gate_mode: Optional[str] = None, config: Path | str | None = DEFAULT_CONFIG,
        label: Optional[str] = None) -> tuple[dict, Path]:
    """Convenience: load agent + cases, run, save. Returns (run, path).

    ``harness.yaml`` (``config``) supplies default ``targets`` and
    ``gate_mode``; explicit ``gates`` / ``gate_mode`` override them.
    ``label`` is free text stored on the run (experiment naming: set + date).
    """
    agent = load_agent(agent_name)
    cases = load_cases(golden_dir, tools, tiers)
    cfg = load_config(config)
    merged_gates = {**config_targets(cfg), **(gates or {})}
    gate_mode = validate_gate_mode(gate_mode or cfg.get("gate_mode"))
    meta = {
        "agent_version": agent_version(agent_name),
        "set_version": set_version(golden_dir),
        "set_labels": set_labels(golden_dir),
        "set_files": set_files(golden_dir),
        "set_provenance": set_provenance(golden_dir),
        "label": label or None,
        "selection": {"tools": list(tools or []), "tiers": list(tiers or []), "gates": merged_gates,
                      "gate_mode": gate_mode, "targets_from_config": config_targets(cfg),
                      "include_unagreed": include_unagreed},
    }
    result = run_agent(Path(agent_name).stem, agent, cases, gates=merged_gates, include_unagreed=include_unagreed,
                       meta=meta, as_of=as_of, gate_mode=gate_mode)
    return result, save_run(result, runs_dir)
