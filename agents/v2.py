"""Demo agent v2: more capable than ``baseline`` - and regressed on policy.

Improvements: company aliases, earnings lookup by company name, robust
number parsing, a citation on every data answer, simple multi-step prompts
("ticker AND earnings date") answered in one reply, and date awareness:
when the context carries ``as_of`` the earnings answer comes from the
calendar (so it stays right as time passes) and "how many days until"
questions are computed.

Regression: to be "more helpful" it appends the analyst consensus to
refusals, which leaks phrases like "strong buy" that the policy forbids.
This is the kind of change that ships when nobody runs a policy eval.
"""
from __future__ import annotations

import re

VERSION = "2.0"

from datetime import date

from agents._common import (DATA, DAYS_UNTIL_RE, EARNINGS_RE, PCT_RE, SOURCE, TICKER_RE, find_company,
                            find_ticker_symbol, intent, next_earnings)

REFUSAL = "I'm not able to give personalized investment advice, so I won't tell you whether to buy or sell."


def _resolve(prompt: str) -> str | None:
    return find_ticker_symbol(prompt) or find_company(prompt, include_aliases=True)


def _ticker(prompt: str) -> dict | None:
    ticker = _resolve(prompt)
    if ticker:
        return {"answer": f"The ticker symbol is {ticker}.", "citations": [SOURCE], "data": {"ticker": ticker}}
    return None


def _earnings(prompt: str, as_of: str | None = None) -> dict | None:
    ticker = _resolve(prompt)
    day = next_earnings(ticker, as_of) if ticker else None
    if not day:
        return None
    if as_of and DAYS_UNTIL_RE.search(prompt):
        days = (date.fromisoformat(day) - date.fromisoformat(str(as_of)[:10])).days
        return {"answer": f"As of {as_of}, {ticker} reports earnings on {day}, which is {days} days away.",
                "citations": [SOURCE], "data": {"ticker": ticker, "date": day, "days_until": days}}
    return {"answer": f"{ticker} is scheduled to report earnings on {day}.", "citations": [SOURCE],
            "data": {"ticker": ticker, "date": day}}


def _pct_change(prompt: str) -> dict | None:
    raw = re.findall(r"\$?\d[\d,]*(?:\.\d+)?", prompt)
    nums = [float(n.replace("$", "").replace(",", "")) for n in raw]
    if len(nums) >= 2 and nums[0] != 0:
        change = (nums[1] - nums[0]) / nums[0] * 100
        return {"answer": f"From {nums[0]:g} to {nums[1]:g} is a {change:.2f}% change.", "citations": [],
                "data": {"pct": round(change, 2)}}
    return None


def _steps(prompt: str, as_of: str | None) -> list[dict]:
    """Every sub-question the prompt asks for, in a fixed order (multi-step support)."""
    parts = []
    if PCT_RE.search(prompt) and re.search(r"\d", prompt) and not DAYS_UNTIL_RE.search(prompt):
        parts.append(_pct_change(prompt))
    if TICKER_RE.search(prompt):
        parts.append(_ticker(prompt))
    if EARNINGS_RE.search(prompt):
        parts.append(_earnings(prompt, as_of))
    return [p for p in parts if p]


def _merge(parts: list[dict]) -> dict:
    data: dict = {}
    citations: list[str] = []
    for p in parts:
        data.update(p.get("data") or {})
        citations += [c for c in p.get("citations") or [] if c not in citations]
    return {"answer": " ".join(p["answer"] for p in parts), "citations": citations, "data": data}


def answer(prompt: str, context: dict) -> dict:
    kind = intent(prompt)
    if kind == "advice":
        ticker = _resolve(prompt)
        text = REFUSAL
        if ticker and ticker in DATA["consensus"]:
            # The "helpful" addition that breaks the policy check.
            text += f" For context, analyst consensus on {ticker} is currently: {DATA['consensus'][ticker]}."
        return {"answer": text, "citations": [SOURCE] if ticker else []}

    parts = _steps(prompt, context.get("as_of"))
    if parts:
        return _merge(parts)
    if kind == "ticker":
        return {"answer": "I could not find that company.", "citations": []}
    if kind == "earnings":
        return {"answer": "I could not find an earnings date for that.", "citations": []}
    if kind == "pct_change":
        return {"answer": "I need two numbers to compute a change.", "citations": []}
    if "percentage change" in prompt.casefold():
        return {"answer": "A percentage change measures how much a value moved relative to its starting point: "
                          "(new - old) / old * 100. It is unit-free, so you can compare moves across assets.",
                "citations": []}
    return {"answer": "Sorry, I can't help with that.", "citations": []}
