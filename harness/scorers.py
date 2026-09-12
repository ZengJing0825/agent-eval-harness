"""Tool-level objective scorers.

Every scorer has the signature ``scorer(answer: dict, check: dict) -> Score``.

``answer`` is what the agent returned: ``{"answer": str, "citations": list}``
(extra keys are allowed and ignored). ``check`` is one entry from a case's
``checks`` list; its ``type`` picks the scorer and the other keys are the
scorer's arguments.

Scores are deterministic wherever possible so that a regression is a fact,
not an opinion. The judged scorers (``llm_judge``, ``requirement`` without a
deterministic fallback) are opt-in and are *skipped* (not failed) when no
judge is configured - see :mod:`harness.judge`.

The five *validation fields* used by answer authors map onto scorers as
follows (one requirement per check):

    correctness -> ``correctness``  exact string / zero-tolerance number
    range       -> ``range``        any number (or ``field``) within [lo, hi]
    keyword     -> ``keyword``      partial credit hits/len(points), pass at min_hit
    requirement -> ``requirement``  one yes/no requirement judged by the LLM judge
    tolerance   -> ``tolerance``    explicit numeric {expected, abs | rel}
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable

from harness import judge as judge_mod

NUMBER_RE = re.compile(r"[-+]?\$?\d[\d,]*(?:\.\d+)?%?")


@dataclass
class Score:
    score: float | None  # 0..1, or None when skipped
    passed: bool | None  # None when skipped
    detail: str = ""
    extra: dict = field(default_factory=dict)  # structured payload (judge reason, hits, dimensions ...)

    @property
    def skipped(self) -> bool:
        return self.score is None


def _text(answer: dict[str, Any]) -> str:
    return str(answer.get("answer", "") or "")


def _norm(s: str, case_sensitive: bool) -> str:
    s = " ".join(s.split())  # collapse whitespace
    return s if case_sensitive else s.casefold()


# --- deterministic scorers -------------------------------------------------

def exact(answer: dict, check: dict) -> Score:
    """Whole-answer equality after whitespace/case normalisation."""
    got = _norm(_text(answer), check.get("case_sensitive", False))
    want = _norm(str(check["expected"]), check.get("case_sensitive", False))
    ok = got == want
    return Score(float(ok), ok, f"expected {want!r}, got {got!r}")


def contains(answer: dict, check: dict) -> Score:
    """Substring presence. ``expected`` may be a string or a list (all required)."""
    cs = check.get("case_sensitive", False)
    got = _norm(_text(answer), cs)
    wants = check["expected"] if isinstance(check["expected"], list) else [check["expected"]]
    missing = [w for w in wants if _norm(str(w), cs) not in got]
    ok = not missing
    return Score(float(ok), ok, "missing " + repr(missing) if missing else "all substrings present")


def regex(answer: dict, check: dict) -> Score:
    """``re.search`` with the given pattern (flags: ``ignore_case``, default on)."""
    flags = re.IGNORECASE if check.get("ignore_case", True) else 0
    ok = re.search(check["pattern"], _text(answer), flags) is not None
    return Score(float(ok), ok, f"pattern {check['pattern']!r} {'matched' if ok else 'not found'}")


def _parse_numbers(text: str) -> list[float]:
    out = []
    for m in NUMBER_RE.findall(text):
        try:
            out.append(float(m.replace("$", "").replace(",", "").rstrip("%")))
        except ValueError:
            pass
    return out


def numeric(answer: dict, check: dict) -> Score:
    """Any number in the answer is within ``tolerance`` (absolute) of ``expected``.

    Set ``relative: true`` to treat the tolerance as a fraction of ``expected``.
    """
    want = float(check["expected"])
    tol = float(check.get("tolerance", 1e-6))
    if check.get("relative"):
        tol = abs(want) * tol
    nums = _parse_numbers(_text(answer))
    if not nums:
        return Score(0.0, False, "no number found in answer")
    best = min(nums, key=lambda n: abs(n - want))
    ok = abs(best - want) <= tol
    return Score(float(ok), ok, f"expected {want} +/- {tol}, closest number {best}")


def _walk(obj: Any, path: str) -> Any:
    """Follow a dotted path such as ``data.items.0.ticker``."""
    for part in path.split("."):
        if isinstance(obj, list):
            obj = obj[int(part)]
        elif isinstance(obj, dict):
            obj = obj[part]
        else:
            raise KeyError(part)
    return obj


def json_key(answer: dict, check: dict) -> Score:
    """Equality of a value at ``path`` in structured output.

    Looks first at ``answer["data"]`` (an agent may return structured data
    alongside its text), then tries to parse the text answer as JSON.
    """
    payload = answer.get("data")
    if payload is None:
        try:
            payload = json.loads(_text(answer))
        except (TypeError, ValueError):
            return Score(0.0, False, "answer is not JSON and has no 'data'")
    try:
        got = _walk(payload, check["path"])
    except (KeyError, IndexError, ValueError):
        return Score(0.0, False, f"path {check['path']!r} not found")
    ok = got == check["expected"]
    return Score(float(ok), ok, f"path {check['path']!r}: expected {check['expected']!r}, got {got!r}")


def policy(answer: dict, check: dict) -> Score:
    """Compliance: none of the ``forbidden`` phrases may appear in the answer."""
    got = _norm(_text(answer), False)
    hits = [p for p in check.get("forbidden", []) if _norm(str(p), False) in got]
    ok = not hits
    return Score(float(ok), ok, "forbidden phrases found: " + repr(hits) if hits else "no forbidden phrases")


def citation(answer: dict, check: dict) -> Score:
    """At least ``min`` (default 1) non-empty citations were returned."""
    cites = [c for c in (answer.get("citations") or []) if c]
    need = int(check.get("min", 1))
    ok = len(cites) >= need
    return Score(float(ok), ok, f"{len(cites)} citation(s), need {need}")


# --- validation-field scorers ---------------------------------------------

def _is_number(x: Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def correctness(answer: dict, check: dict) -> Score:
    """Alias for the objective case: ``exact`` for strings, zero-tolerance ``numeric`` for numbers."""
    if _is_number(check.get("expected")):
        return numeric(answer, {**check, "tolerance": 1e-9, "relative": False})
    return exact(answer, check)


def value_range(answer: dict, check: dict) -> Score:
    """Some number in the answer (or the value at ``field`` in ``data``) lies within ``[lo, hi]``."""
    lo, hi = float(check["lo"]), float(check["hi"])
    if lo > hi:
        raise ValueError(f"range check: lo {lo} > hi {hi}")
    fld = check.get("field")
    if fld:
        try:
            val = float(_walk(answer.get("data") or {}, str(fld)))
        except (KeyError, IndexError, ValueError, TypeError):
            return Score(0.0, False, f"field {fld!r} missing or not numeric in data")
        nums = [val]
    else:
        nums = _parse_numbers(_text(answer))
    if not nums:
        return Score(0.0, False, "no number found in answer")
    inside = [n for n in nums if lo <= n <= hi]
    ok = bool(inside)
    what = f"field {fld!r}" if fld else "answer"
    return Score(float(ok), ok, f"[{lo:g}, {hi:g}]: {'contains' if ok else 'no number of the'} "
                 f"{what} {inside[0] if ok else nums}", {"numbers": nums, "in_range": inside})


def keyword(answer: dict, check: dict) -> Score:
    """Partial credit: ``hits / len(points)``; passes when ``hits >= min_hit`` (default: all)."""
    points = list(check.get("points") or [])
    if not points:
        raise ValueError("keyword check needs a non-empty 'points' list")
    cs = check.get("case_sensitive", False)
    got = _norm(_text(answer), cs)
    hit = [p for p in points if _norm(str(p), cs) in got]
    min_hit = check.get("min_hit")
    need = len(points) if min_hit is None else int(min_hit)
    ok = len(hit) >= need
    missing = [p for p in points if p not in hit]
    return Score(len(hit) / len(points), ok, f"{len(hit)}/{len(points)} keywords (need {need})"
                 + (f", missing {missing!r}" if missing else ""), {"hits": hit, "missing": missing, "min_hit": need})


def _judge_extra(res: dict) -> dict:
    return {"judge": res.get("judge"), "backend": res.get("backend"), "reason": res.get("reason", "")}


def requirement(answer: dict, check: dict) -> Score:
    """One yes/no requirement, judged by the configured judge.

    ``text`` is the requirement, ``calculation`` an optional reference the
    judge may use. Without a judge, ``must_contain_any`` (list of strings)
    is used as a deterministic fallback; with neither the check is skipped.
    """
    text = check.get("text")
    if not text:
        raise ValueError("requirement check needs 'text'")
    j = judge_mod.current()
    if j is not None:
        res = j.evaluate("requirement", question=check.get("prompt", ""), answer=_text(answer),
                         requirement=text, calculation=check.get("calculation") or "")
        if "error" in res:
            return Score(0.0, False, res["error"], _judge_extra(res))
        ok = str(res.get("verdict", "")).strip().lower() in ("yes", "true", "1")
        return Score(float(ok), ok, f"judge {res['judge']}: {'yes' if ok else 'no'} - {res.get('reason', '')}",
                     _judge_extra(res))
    fallback = check.get("must_contain_any")
    if fallback:
        got = _norm(_text(answer), False)
        hit = [p for p in fallback if _norm(str(p), False) in got]
        ok = bool(hit)
        return Score(float(ok), ok, f"no judge; fallback must_contain_any {'hit ' + repr(hit) if ok else 'missed'}",
                     {"fallback": True, "hits": hit})
    return Score(None, None, "judge skipped: no judge configured and no must_contain_any fallback")


def tolerance(answer: dict, check: dict) -> Score:
    """Explicit numeric tolerance: ``expected`` with ``abs`` or ``rel`` (one of them, default abs 0)."""
    if "abs" in check and "rel" in check:
        raise ValueError("tolerance check: give either 'abs' or 'rel', not both")
    if "rel" in check:
        return numeric(answer, {"expected": check["expected"], "tolerance": float(check["rel"]), "relative": True})
    return numeric(answer, {"expected": check["expected"], "tolerance": float(check.get("abs", 0.0))})


# --- optional LLM judge ----------------------------------------------------

def llm_judge(answer: dict, check: dict) -> Score:
    """Free-text rubric grading (0-10, scaled to 0-1) via the configured judge.

    Skipped cleanly (``score=None``) when no judge is configured, so the
    default demo never needs the network. Prompt: ``judges/rubric.v<N>.md``.
    """
    j = judge_mod.current()
    if j is None:
        return Score(None, None, "judge skipped: ANTHROPIC_API_KEY not set (or --judge none)")
    res = j.evaluate("rubric", question=check.get("prompt", ""), answer=_text(answer), rubric=check["rubric"])
    if "error" in res:
        return Score(0.0, False, res["error"], _judge_extra(res))
    try:
        score = max(0.0, min(10.0, float(res["score"]))) / 10.0
    except (KeyError, TypeError, ValueError):
        return Score(0.0, False, f"judge returned no numeric score: {res!r}"[:120], _judge_extra(res))
    threshold = float(check.get("threshold", 0.7))
    return Score(score, score >= threshold, f"judge {res['judge']}: {score * 10:.0f}/10 - {res.get('reason', '')}",
                 _judge_extra(res))


SCORERS: dict[str, Callable[[dict, dict], Score]] = {
    "exact": exact,
    "contains": contains,
    "regex": regex,
    "numeric": numeric,
    "json_key": json_key,
    "policy": policy,
    "citation": citation,
    "correctness": correctness,
    "range": value_range,
    "keyword": keyword,
    "requirement": requirement,
    "tolerance": tolerance,
    "llm_judge": llm_judge,
}

#: Scorers that may call the LLM judge (used for judge coverage in reports).
JUDGED = {"llm_judge", "requirement"}
NEEDS_PROMPT = {"llm_judge", "requirement"}


def run_check(answer: dict, check: dict, prompt: str = "") -> Score:
    """Dispatch one check to its scorer. Unknown types raise ``KeyError``."""
    if check["type"] not in SCORERS:
        raise KeyError(f"unknown scorer type {check['type']!r}; known: {sorted(SCORERS)}")
    if check["type"] in NEEDS_PROMPT:
        check = {**check, "prompt": prompt}  # the judge needs the question too
    return SCORERS[check["type"]](answer, check)
