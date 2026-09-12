"""Tool-level objective scorers.

Every scorer has the signature ``scorer(answer: dict, check: dict) -> Score``.

``answer`` is what the agent returned: ``{"answer": str, "citations": list}``
(extra keys are allowed and ignored). ``check`` is one entry from a case's
``checks`` list; its ``type`` picks the scorer and the other keys are the
scorer's arguments.

Scores are deterministic wherever possible so that a regression is a fact,
not an opinion. The single non-deterministic scorer (``llm_judge``) is
opt-in and is *skipped* (not failed) when no API key is available.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Callable

NUMBER_RE = re.compile(r"[-+]?\$?\d[\d,]*(?:\.\d+)?%?")


@dataclass
class Score:
    score: float | None  # 0..1, or None when skipped
    passed: bool | None  # None when skipped
    detail: str = ""

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


# --- optional LLM judge ----------------------------------------------------

JUDGE_PROMPT = (
    "You are grading an AI assistant's answer against a rubric.\n\n"
    "Question:\n{prompt}\n\nAnswer:\n{answer}\n\nRubric:\n{rubric}\n\n"
    "Reply with ONLY a JSON object: {{\"score\": <0-10 integer>, \"reason\": \"<one sentence>\"}}"
)


def llm_judge(answer: dict, check: dict) -> Score:
    """Rubric-based grading via the optional Anthropic adapter.

    Skipped cleanly (``score=None``) when the adapter or API key is missing,
    so the default demo never needs the network.
    """
    try:
        from agents.anthropic_agent import complete, is_available
    except ImportError:
        return Score(None, None, "judge unavailable: anthropic adapter not importable")
    if not is_available():
        return Score(None, None, "judge skipped: ANTHROPIC_API_KEY not set")
    prompt = JUDGE_PROMPT.format(
        prompt=check.get("prompt", ""), answer=_text(answer), rubric=check["rubric"]
    )
    raw = complete(prompt, max_tokens=256)
    try:
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        parsed = json.loads(m.group(0) if m else raw)
        score = max(0.0, min(10.0, float(parsed["score"]))) / 10.0
    except (ValueError, KeyError, TypeError):
        return Score(0.0, False, f"judge returned unparseable output: {raw[:80]!r}")
    threshold = float(check.get("threshold", 0.7))
    return Score(score, score >= threshold, str(parsed.get("reason", "")))


SCORERS: dict[str, Callable[[dict, dict], Score]] = {
    "exact": exact,
    "contains": contains,
    "regex": regex,
    "numeric": numeric,
    "json_key": json_key,
    "policy": policy,
    "citation": citation,
    "llm_judge": llm_judge,
}


def run_check(answer: dict, check: dict, prompt: str = "") -> Score:
    """Dispatch one check to its scorer. Unknown types raise ``KeyError``."""
    if check["type"] not in SCORERS:
        raise KeyError(f"unknown scorer type {check['type']!r}; known: {sorted(SCORERS)}")
    if check["type"] == "llm_judge":
        check = {**check, "prompt": prompt}  # the judge needs the question too
    return SCORERS[check["type"]](answer, check)
