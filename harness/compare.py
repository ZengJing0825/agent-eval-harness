"""Peer comparison: two runs on the same cases, side by side."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

EPS = 1e-9


def load_run(path: Path | str) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def outcome(score_a: float | None, score_b: float | None) -> str:
    """'win' if B beats A, 'loss' if A beats B, 'tie' otherwise, 'skip' if either is missing."""
    if score_a is None or score_b is None:
        return "skip"
    if score_b > score_a + EPS:
        return "win"
    if score_a > score_b + EPS:
        return "loss"
    return "tie"


def compare_runs(run_a: dict[str, Any], run_b: dict[str, Any]) -> dict[str, Any]:
    """Join two runs on case id and compute per-case outcomes for B vs A.

    Cases present in only one run are reported under ``unmatched`` rather
    than silently dropped - a grown golden set should be visible.
    """
    a_by_id = {c["id"]: c for c in run_a["cases"]}
    b_by_id = {c["id"]: c for c in run_b["cases"]}
    common = [cid for cid in a_by_id if cid in b_by_id]
    rows = []
    for cid in common:
        a, b = a_by_id[cid], b_by_id[cid]
        rows.append({
            "id": cid, "tool": a["tool"], "tier": a.get("tier", "unit"), "prompt": a["prompt"],
            "score_a": a["score"], "score_b": b["score"],
            "answer_a": a["answer"], "answer_b": b["answer"],
            "outcome": outcome(a["score"], b["score"]),
        })
    tally = {k: sum(1 for r in rows if r["outcome"] == k) for k in ("win", "loss", "tie", "skip")}
    per_tool: dict[str, dict[str, int]] = {}
    per_tier: dict[str, dict[str, int]] = {}
    per_tier_tool: dict[str, dict[str, dict[str, int]]] = {}
    empty = {"win": 0, "loss": 0, "tie": 0, "skip": 0}
    for r in rows:
        per_tool.setdefault(r["tool"], dict(empty))[r["outcome"]] += 1
        per_tier.setdefault(r["tier"], dict(empty))[r["outcome"]] += 1
        per_tier_tool.setdefault(r["tier"], {}).setdefault(r["tool"], dict(empty))[r["outcome"]] += 1
    return {
        "agent_a": run_a["agent"], "agent_b": run_b["agent"],
        "run_a_timestamp": run_a["timestamp"], "run_b_timestamp": run_b["timestamp"],
        "summary_a": run_a["summary"], "summary_b": run_b["summary"],
        "tally": tally, "per_tool": per_tool, "per_tier": per_tier, "per_tier_tool": per_tier_tool,
        "cases": rows,
        "unmatched": {"only_in_a": sorted(set(a_by_id) - set(b_by_id)),
                      "only_in_b": sorted(set(b_by_id) - set(a_by_id))},
    }
