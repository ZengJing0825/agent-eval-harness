"""Resolvers for the dynamic tier: expected values that move with the date.

A resolver is any callable ``fn(as_of: str, **args) -> dict``; a case names
it as ``resolver: agents.resolvers:earnings_date`` and passes ``resolver_args``.
The returned keys become ``{placeholders}`` in the case's checks, e.g.
``expected: "{next_earnings}"``. This one reads the bundled fixture; a real
one would query whatever system of record the agent is supposed to track.
"""
from __future__ import annotations

from datetime import date

from agents._common import DATA


def _parse(day: str) -> date:
    return date.fromisoformat(str(day)[:10])


def earnings_date(as_of: str, ticker: str) -> dict:
    """Next and previous earnings dates for ``ticker`` relative to ``as_of``."""
    calendar = sorted(DATA.get("earnings_calendar", {}).get(ticker, []))
    if not calendar:
        raise ValueError(f"no earnings calendar for {ticker!r}")
    today = _parse(as_of)
    future = [d for d in calendar if _parse(d) >= today]
    past = [d for d in calendar if _parse(d) < today]
    if not future:
        raise ValueError(f"calendar for {ticker!r} ends before {as_of}")
    nxt = future[0]
    return {
        "ticker": ticker,
        "next_earnings": nxt,
        "previous_earnings": past[-1] if past else "",
        "days_until": (_parse(nxt) - today).days,
    }
