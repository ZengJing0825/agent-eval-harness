"""Validation fields: the rubric format answer authors actually write.

In the workflow this harness comes from, the person who writes a case does
not write scorer configuration. They write a *rubric*: a JSON array with one
entry per **validation field**, in the words of the business::

    [
      {"validation field": "range",
       "criteria": {"value": 6.611, "tolerance": 0.05, "unit": "USD per share"}},
      {"validation field": "keyword",
       "criteria": ["free cash flow", "outstanding shares", "per share"]},
      {"validation field": "requirement",
       "requirement": "The answer must state ... and show the division ..."}
    ]

:func:`checks_from_rubric` turns that array into harness checks, so a case
can carry the rubric verbatim (``validation_fields:`` in a case file, or
``--rubric-column`` on ``harness import``) instead of being rewritten by
hand. The mapping:

===================  ==========================================  =========================
validation field     criteria shape                              check
===================  ==========================================  =========================
``correctness``      a number or a short token                   ``correctness``
``correctness``      a sentence ("The answer must state ...")    ``requirement`` (judged)
``range``            ``{value, tolerance, unit?}``               ``range``
``range``            ``"... 41.87% with +/-2% tolerance"``       ``range`` (parsed)
``keyword``          a list of scoring points                    ``keyword``
``keyword``          one string                                  ``keyword`` (one point)
``requirement``      the requirement text                        ``requirement`` (judged)
``boolean``          ``{statement: "OKB burned: Yes"}``          ``requirement`` (judged)
``tolerance``        ``{expected, abs | rel}``                   ``tolerance``
===================  ==========================================  =========================

Two rules of the original workflow are kept here because they change the
numbers: a rubric holds **one** ``requirement`` (checked by
:func:`check_one_requirement`), and the calculation is folded into that
requirement rather than being its own field. A ``calculation`` entry is
therefore attached to the requirement check, not turned into a check of its
own.
"""
from __future__ import annotations

import json
import re
from typing import Any, Iterable, Optional

FIELD_KEY = "validation field"
#: Validation fields an author may write.
FIELDS = ("correctness", "range", "keyword", "requirement", "boolean", "tolerance", "calculation")

#: A "sentence" (-> judged requirement) rather than a value to compare.
_SENTENCE_RE = re.compile(r"\s")
_APPROX_RE = re.compile(r"(?:≈|~|=|:)\s*([-+]?\$?\d[\d,]*(?:\.\d+)?)\s*(%|pp|bps)?")
_TOL_RE = re.compile(r"(?:±|\+/-|\+-)\s*(\d[\d,]*(?:\.\d+)?)\s*(%|pp|bps)?")
_NUMBER_RE = re.compile(r"^[-+]?\$?\d[\d,]*(?:\.\d+)?%?$")


class RubricError(ValueError):
    """The rubric could not be turned into checks."""


def _num(text: Any) -> float:
    return float(str(text).replace("$", "").replace(",", "").rstrip("%"))


def looks_like_value(criteria: Any) -> bool:
    """True when ``criteria`` is a value to compare, not a sentence to judge."""
    if isinstance(criteria, (int, float)) and not isinstance(criteria, bool):
        return True
    text = str(criteria).strip()
    return bool(_NUMBER_RE.match(text)) or (len(text) <= 24 and not _SENTENCE_RE.search(text))


def parse_range_text(text: str) -> Optional[dict[str, Any]]:
    """``"... ratio on 2025/08/01 = 41.87% with +/-2% tolerance"`` -> value/tolerance.

    A percentage tolerance next to a percentage value is read as percentage
    points (the convention the authors used: "+/-2pp"), i.e. an absolute
    tolerance on the number as written. Returns ``None`` when no value can
    be found, so the caller can fall back to a judged requirement.
    """
    m = _APPROX_RE.search(str(text))
    if not m:
        return None
    out: dict[str, Any] = {"value": _num(m.group(1))}
    if m.group(2):
        out["unit"] = m.group(2)
    t = _TOL_RE.search(str(text))
    if t:
        out["tolerance"] = _num(t.group(1))
    return out


def _range_check(criteria: Any, where: str) -> dict[str, Any]:
    if isinstance(criteria, dict):
        if "value" not in criteria and not {"lo", "hi"} <= set(criteria):
            raise RubricError(f"{where}: range criteria needs 'value' (or 'lo' and 'hi'), got {sorted(criteria)}")
        check = {"type": "range", **{k: v for k, v in criteria.items() if k in ("value", "tolerance", "lo", "hi",
                                                                               "unit", "relative", "field")}}
        return check
    parsed = parse_range_text(str(criteria))
    if parsed is None:
        return {"type": "requirement", "text": str(criteria), "from_field": "range"}
    return {"type": "range", **parsed, "from_text": str(criteria)}


def _keyword_check(criteria: Any, where: str, min_hit: Any = None) -> dict[str, Any]:
    if isinstance(criteria, str):
        points = [p.strip() for p in criteria.split(";") if p.strip()] or [criteria]
    elif isinstance(criteria, Iterable):
        points = [str(p).strip() for p in criteria if str(p).strip()]
    else:
        raise RubricError(f"{where}: keyword criteria must be a list or a string")
    if not points:
        raise RubricError(f"{where}: keyword criteria is empty")
    check: dict[str, Any] = {"type": "keyword", "points": points}
    if min_hit is not None:
        check["min_hit"] = int(min_hit)
    return check


def _entry_text(entry: dict[str, Any]) -> Any:
    for key in ("requirement", "criteria", "statement", "text"):
        if entry.get(key) not in (None, ""):
            return entry[key]
    return None


def checks_from_rubric(rubric: Any, case_id: str = "") -> list[dict[str, Any]]:
    """Turn a validation-field rubric (JSON array, or its text) into checks.

    Raises :class:`RubricError` on an unparseable rubric, an unknown
    validation field, or more than one ``requirement`` (a rubric holds one).
    """
    where = f"case {case_id!r}" if case_id else "rubric"
    if isinstance(rubric, str):
        text = rubric.strip()
        if not text:
            raise RubricError(f"{where}: empty rubric")
        try:
            rubric = json.loads(text)
        except ValueError as exc:
            raise RubricError(f"{where}: rubric is not valid JSON ({exc})") from None
    if isinstance(rubric, dict):
        rubric = [rubric]
    if not isinstance(rubric, list) or not rubric:
        raise RubricError(f"{where}: rubric must be a non-empty JSON array")

    checks: list[dict[str, Any]] = []
    calculation: Optional[str] = None
    for i, entry in enumerate(rubric):
        if not isinstance(entry, dict):
            raise RubricError(f"{where}: rubric entry {i} must be an object")
        field = str(entry.get(FIELD_KEY) or entry.get("field") or "").strip().lower()
        if field not in FIELDS:
            raise RubricError(f"{where}: entry {i}: unknown validation field {field!r}; known: {list(FIELDS)}")
        criteria = _entry_text(entry)
        if criteria in (None, ""):
            raise RubricError(f"{where}: entry {i} ({field}) has no criteria")
        pos = f"{where} entry {i}"
        if field == "calculation":  # folded into the requirement, never its own check
            calculation = str(criteria)
        elif field == "requirement":
            checks.append({"type": "requirement", "text": str(criteria)})
        elif field == "boolean":
            statement = criteria.get("statement") if isinstance(criteria, dict) else criteria
            checks.append({"type": "requirement", "text": f"The answer states: {statement}", "from_field": "boolean"})
        elif field == "range":
            checks.append(_range_check(criteria, pos))
        elif field == "keyword":
            checks.append(_keyword_check(criteria, pos, entry.get("min_hit")))
        elif field == "tolerance":
            if not isinstance(criteria, dict) or "expected" not in criteria:
                raise RubricError(f"{pos}: tolerance criteria needs 'expected' with 'abs' or 'rel'")
            checks.append({"type": "tolerance", **{k: v for k, v in criteria.items() if k in ("expected", "abs", "rel")}})
        else:  # correctness
            if looks_like_value(criteria):
                expected = _num(criteria) if _NUMBER_RE.match(str(criteria).strip()) else str(criteria)
                checks.append({"type": "correctness", "expected": expected})
            else:  # a sentence: the judge decides, as the original evaluator did
                checks.append({"type": "requirement", "text": str(criteria), "from_field": "correctness"})
    check_one_requirement(checks, where)
    if calculation:
        for check in checks:
            if check["type"] == "requirement":
                check["calculation"] = calculation
                break
    return checks


def check_one_requirement(checks: list[dict[str, Any]], where: str = "rubric") -> None:
    """Enforce "one rubric, one requirement" - the rule the authors worked to."""
    written = [c for c in checks if c["type"] == "requirement" and not c.get("from_field")]
    if len(written) > 1:
        raise RubricError(f"{where}: {len(written)} 'requirement' fields; a rubric holds one "
                          "(fold the rest into it, or split the case)")


def requirement_text(checks: list[dict[str, Any]]) -> Optional[str]:
    for check in checks:
        if check.get("type") == "requirement":
            return str(check.get("text") or "")
    return None
