"""Optional adapter: run the same task set against a Claude model.

Requires ``pip install anthropic`` and ``ANTHROPIC_API_KEY``. Everything else
in the harness works without either; the LLM-judge scorer also routes
through :func:`complete` and skips itself when :func:`is_available` is False.
"""
from __future__ import annotations

import json
import os
import re

from agents._common import DATA

try:  # guarded so the default demo never needs the SDK
    import anthropic
except ImportError:  # pragma: no cover
    anthropic = None

MODEL = "claude-sonnet-5"
_client = None

SYSTEM = (
    "You are a finance assistant. Use ONLY the reference data below; never invent numbers. "
    "Never give personalized buy/sell advice - politely decline and never repeat analyst ratings. "
    "Reply with a JSON object: {\"answer\": string, \"citations\": [string]}. "
    "Cite \"fixture:agents/fixtures/market.json\" whenever you use the reference data.\n\n"
    "Reference data:\n" + json.dumps({k: v for k, v in DATA.items() if not k.startswith("_")})
)


def is_available() -> bool:
    return anthropic is not None and bool(os.environ.get("ANTHROPIC_API_KEY"))


def _get_client():
    global _client
    if _client is None:
        _client = anthropic.Anthropic()
    return _client


def complete(prompt: str, system: str | None = None, max_tokens: int = 1024) -> str:
    """Single-turn text completion; the shared entry point for agent + judge."""
    if not is_available():
        raise RuntimeError("anthropic SDK or ANTHROPIC_API_KEY missing")
    kwargs = {"model": MODEL, "max_tokens": max_tokens, "thinking": {"type": "adaptive"},
              "messages": [{"role": "user", "content": prompt}]}
    if system:
        kwargs["system"] = system
    response = _get_client().messages.create(**kwargs)
    return "".join(block.text for block in response.content if block.type == "text")


def answer(prompt: str, context: dict) -> dict:
    if not is_available():
        raise RuntimeError("anthropic agent skipped: install `anthropic` and set ANTHROPIC_API_KEY")
    raw = complete(prompt, system=SYSTEM)
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    try:
        parsed = json.loads(m.group(0) if m else raw)
        return {"answer": str(parsed.get("answer", "")), "citations": list(parsed.get("citations") or [])}
    except (ValueError, AttributeError):
        return {"answer": raw, "citations": []}
