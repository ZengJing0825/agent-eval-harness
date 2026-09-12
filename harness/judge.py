"""LLM judge plumbing: versioned prompt files, backend selection, a fake judge.

Prompts live in ``judges/<name>.v<N>.md`` and are ``string.Template`` files
(``${question}``, ``${answer}``, ...). The highest ``N`` is used unless a
version is pinned. Every judgement returns a ``reason`` so it can be audited
against a human label later (``harness audit``).

Three judge rules apply to every judged check (:data:`RULES`); every prompt
file states them and ``${rules}`` is available to custom prompts:

1. the case rubric / requirement takes precedence over general instructions
2. an unmet ``requirement`` scores 0 for that check - no partial credit
3. an answer that contains the reference answer and adds correct extra
   detail is not penalised ("richer than reference is fine")

Backends (``--judge`` / ``HARNESS_JUDGE``):

* ``auto``      - Anthropic adapter when importable and ``ANTHROPIC_API_KEY`` is set, else none
* ``anthropic`` - force the adapter (errors if unavailable)
* ``fake``      - deterministic offline judge for tests and demos; it grades by
                  word overlap between the rubric and the answer and must never
                  be mistaken for a real evaluation
* ``none``      - judged checks are skipped
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from string import Template
from typing import Any, Optional

JUDGES_DIR = Path(__file__).resolve().parent.parent / "judges"
ENV_VAR = "HARNESS_JUDGE"
BACKENDS = ("auto", "anthropic", "fake", "none")

#: The three rules every judge prompt must state (see judges/README.md).
RULES = (
    "The case rubric or requirement takes precedence over these general instructions.",
    "An unmet requirement scores 0 for that check; there is no partial credit.",
    "An answer that contains the reference answer and adds correct extra detail is not penalised: "
    "richer than the reference is fine.",
)


def rules_text() -> str:
    """The rules as a numbered list, for ``${rules}`` in prompt templates."""
    return "\n".join(f"{i}. {rule}" for i, rule in enumerate(RULES, 1))


def prompt_states_rules(text: str) -> list[str]:
    """Rules missing from a prompt's text (empty list when all three are stated)."""
    return [rule for rule in RULES if rule not in text]

_STOPWORDS = {"the", "and", "that", "with", "this", "from", "have", "must", "should", "answer", "does",
              "than", "were", "into", "your", "what", "when", "which", "each", "either", "then", "there",
              "mentions", "mention", "include", "includes", "equivalent", "correct", "correctly", "fewer",
              "sentences", "sentence", "errors", "error", "factual", "states", "state", "explains", "explain"}


# --- prompt files ----------------------------------------------------------

def list_prompt_versions(judges_dir: Path | str = JUDGES_DIR) -> dict[str, list[int]]:
    """``{"requirement": [1, 2], "rubric": [1]}`` from the files on disk."""
    out: dict[str, list[int]] = {}
    for p in sorted(Path(judges_dir).glob("*.v*.md")):
        m = re.fullmatch(r"(.+)\.v(\d+)\.md", p.name)
        if m:
            out.setdefault(m.group(1), []).append(int(m.group(2)))
    return {k: sorted(v) for k, v in out.items()}


def load_prompt(name: str, version: Optional[int] = None,
                judges_dir: Path | str = JUDGES_DIR) -> tuple[str, Template]:
    """Return ``("<name>.v<N>", Template)`` for the latest (or pinned) version."""
    versions = list_prompt_versions(judges_dir).get(name)
    if not versions:
        raise FileNotFoundError(f"no judge prompt {name!r} under {judges_dir} (expected {name}.v1.md)")
    if version is None:
        version = versions[-1]
    if version not in versions:
        raise FileNotFoundError(f"judge prompt {name}.v{version}.md not found; have {versions}")
    text = (Path(judges_dir) / f"{name}.v{version}.md").read_text(encoding="utf-8")
    return f"{name}.v{version}", Template(text)


def prompt_versions_string(judges_dir: Path | str = JUDGES_DIR) -> str:
    return ",".join(f"{n}.v{v[-1]}" for n, v in sorted(list_prompt_versions(judges_dir).items()))


# --- backends --------------------------------------------------------------

def _parse_json(raw: str) -> Optional[dict]:
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    try:
        parsed = json.loads(m.group(0) if m else raw)
    except (ValueError, TypeError):
        return None
    return parsed if isinstance(parsed, dict) else None


class Judge:
    """Base class: ``evaluate(kind, fields)`` returns a dict with at least ``reason``."""

    backend = "none"

    def __init__(self, judges_dir: Path | str = JUDGES_DIR) -> None:
        self.judges_dir = Path(judges_dir)

    def version(self) -> str:
        return f"{self.backend}:{prompt_versions_string(self.judges_dir)}"

    def evaluate(self, kind: str, **fields: Any) -> dict[str, Any]:
        prompt_version, template = load_prompt(kind, judges_dir=self.judges_dir)
        safe = {k: ("" if v is None else str(v)) for k, v in fields.items()}
        safe.setdefault("rules", rules_text())
        prompt = template.safe_substitute(safe)
        result = self._judge(kind, prompt, fields)
        result.setdefault("reason", "")
        result["judge"] = prompt_version
        result["backend"] = self.backend
        return result

    def _judge(self, kind: str, prompt: str, fields: dict[str, Any]) -> dict[str, Any]:  # pragma: no cover
        raise NotImplementedError


class AnthropicJudge(Judge):
    backend = "anthropic"

    def _judge(self, kind: str, prompt: str, fields: dict[str, Any]) -> dict[str, Any]:
        from agents.anthropic_agent import complete  # imported lazily: optional dependency
        raw = complete(prompt, max_tokens=300)
        parsed = _parse_json(raw)
        if parsed is None:
            return {"error": f"unparseable judge output: {raw[:80]!r}", "reason": raw[:200]}
        return parsed


def content_words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]{4,}", str(text).casefold()) if w not in _STOPWORDS}


class FakeJudge(Judge):
    """Deterministic stand-in: grades by word overlap between rubric and answer.

    Rules (documented so tests can rely on them):
    * empty answer -> lowest score / verdict "no"
    * ``overlap`` = number of rubric content words (>= 4 letters, minus stop
      words) that appear in the answer
    * requirement: verdict "yes" iff overlap >= 1, or the answer shares a
      content word with the *question* (so "on topic"-style gates pass)
    * rubric (0-10): 2 if no overlap, else 6 + min(4, overlap)
    * dimension (0-5): 3 if the answer shares a content word with the
      question (it is on topic) else 1, plus 1 per rubric-word overlap, max 5
    """

    backend = "fake"

    def _judge(self, kind: str, prompt: str, fields: dict[str, Any]) -> dict[str, Any]:
        answer = str(fields.get("answer") or "").strip()
        criteria = " ".join(str(fields.get(k) or "") for k in ("requirement", "rubric", "dimension", "description"))
        words = content_words(criteria)
        overlap = len(words & content_words(answer)) if answer else 0
        reason = f"fake judge: {overlap}/{len(words)} rubric words found in answer"
        if kind == "requirement":
            q_overlap = len(content_words(fields.get("question") or "") & content_words(answer)) if answer else 0
            ok = overlap >= 1 or q_overlap >= 1
            return {"verdict": "yes" if ok else "no",
                    "reason": reason + (f"; {q_overlap} question words" if q_overlap else "")}
        if kind == "dimension":
            q_overlap = len(content_words(fields.get("question") or "") & content_words(answer)) if answer else 0
            base = 0 if not answer else (3 if q_overlap else 1)
            return {"score": min(5, base + overlap) if answer else 0,
                    "reason": reason + (f"; {q_overlap} question words" if q_overlap else "")}
        return {"score": 0 if not answer else (2 if overlap == 0 else 6 + min(4, overlap)), "reason": reason}


# --- selection -------------------------------------------------------------

_current: Optional[Judge] = None
_configured = False


def anthropic_available() -> bool:
    try:
        from agents.anthropic_agent import is_available
    except ImportError:
        return False
    return is_available()


def make_judge(spec: Optional[str] = None, judges_dir: Path | str = JUDGES_DIR) -> Optional[Judge]:
    """Build a judge from a spec (``auto``/``anthropic``/``fake``/``none``)."""
    spec = (spec or os.environ.get(ENV_VAR) or "auto").strip().lower()
    if spec not in BACKENDS:
        raise ValueError(f"unknown judge {spec!r}; known: {list(BACKENDS)}")
    if spec == "none":
        return None
    if spec == "fake":
        return FakeJudge(judges_dir)
    if spec == "anthropic":
        if not anthropic_available():
            raise ValueError("judge 'anthropic' requested but the SDK or ANTHROPIC_API_KEY is missing")
        return AnthropicJudge(judges_dir)
    return AnthropicJudge(judges_dir) if anthropic_available() else None


def configure(spec: Optional[str] = None, judges_dir: Path | str = JUDGES_DIR) -> Optional[Judge]:
    """Set the process-wide judge used by judged scorers. Returns it."""
    global _current, _configured
    _current = make_judge(spec, judges_dir)
    _configured = True
    return _current


def current() -> Optional[Judge]:
    """The configured judge, or the ``auto`` choice when nothing was configured."""
    if not _configured:
        configure()
    return _current


def version_string() -> str:
    j = current()
    return j.version() if j else "none"
