"""Gate-first weighted rubrics: ``rubrics/<name>.yaml`` loading and grading maths.

A rubric has *gates* (checked first; a hit caps the grade at F or C) and
*dimensions* (weights summing to 100, each judged 0-5). The grading is
deterministic given the per-dimension scores; only the scores themselves
come from a judge. See ``rubrics/research_answer.yaml`` for the format.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

import yaml

RUBRICS_DIR = Path(__file__).resolve().parent.parent / "rubrics"
GRADES = ("A", "B", "C", "D", "F")  # best to worst
CAPS = ("F", "C")
DETERMINISTIC_GATE_KEYS = ("forbidden", "required_any", "required_all", "min_citations")


def load_rubric(name: str, rubrics_dir: Path | str = RUBRICS_DIR) -> dict[str, Any]:
    """Load and validate ``<rubrics_dir>/<name>.yaml`` (or a direct ``.yaml`` path)."""
    path = Path(name) if str(name).endswith((".yaml", ".yml")) else Path(rubrics_dir) / f"{name}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"rubric {name!r} not found at {path}")
    with open(path, encoding="utf-8") as fh:
        doc = yaml.safe_load(fh) or {}
    return validate_rubric(doc, str(path))


def validate_rubric(doc: dict[str, Any], where: str = "rubric") -> dict[str, Any]:
    if not doc.get("name"):
        raise ValueError(f"{where}: rubric needs a 'name'")
    dims = doc.get("dimensions") or []
    if not dims:
        raise ValueError(f"{where}: rubric needs at least one dimension")
    total = 0.0
    seen: set[str] = set()
    for d in dims:
        if not d.get("id") or "weight" not in d:
            raise ValueError(f"{where}: every dimension needs 'id' and 'weight'")
        if d["id"] in seen:
            raise ValueError(f"{where}: duplicate dimension id {d['id']!r}")
        seen.add(d["id"])
        total += float(d["weight"])
    if abs(total - 100.0) > 1e-6:
        raise ValueError(f"{where}: dimension weights must sum to 100, got {total:g}")
    for g in doc.get("gates") or []:
        if not g.get("id"):
            raise ValueError(f"{where}: every gate needs an 'id'")
        if str(g.get("cap", "F")) not in CAPS:
            raise ValueError(f"{where}: gate {g['id']!r}: cap must be one of {list(CAPS)}")
        if not any(k in g for k in DETERMINISTIC_GATE_KEYS) and not g.get("judge"):
            raise ValueError(f"{where}: gate {g['id']!r} needs a deterministic rule "
                             f"({', '.join(DETERMINISTIC_GATE_KEYS)}) or 'judge: true'")
    bands = doc.get("bands") or {"A": 90, "B": 75, "C": 60, "D": 40}
    for grade in ("A", "B", "C", "D"):
        if grade not in bands:
            raise ValueError(f"{where}: bands need lower bounds for A, B, C and D")
    doc["bands"] = {k: float(bands[k]) for k in ("A", "B", "C", "D")}
    doc["version"] = int(doc.get("version", 1))
    doc["pass_band"] = str(doc.get("pass_band", "C")).upper()
    if doc["pass_band"] not in GRADES:
        raise ValueError(f"{where}: pass_band must be one of {list(GRADES)}")
    return doc


def grade_for(points: float, bands: dict[str, float]) -> str:
    for grade in ("A", "B", "C", "D"):
        if points >= bands[grade]:
            return grade
    return "F"


def cap_points(points: float, cap: Optional[str], bands: dict[str, float]) -> float:
    """Apply a cap: F -> 0; C -> just below the B band."""
    if cap == "F":
        return 0.0
    if cap == "C":
        return min(points, bands["B"] - 1.0)
    return points


def worst_cap(caps: list[str]) -> Optional[str]:
    if "F" in caps:
        return "F"
    return "C" if "C" in caps else None


def total_points(dimensions: list[dict[str, Any]], scores: dict[str, float]) -> float:
    """Weighted 0-100 total from 0-5 dimension scores."""
    return sum(float(d["weight"]) * max(0.0, min(5.0, float(scores.get(d["id"], 0.0)))) / 5.0 for d in dimensions)


def grade_at_least(grade: str, minimum: str) -> bool:
    return GRADES.index(grade) <= GRADES.index(minimum)
