"""Shared helpers for the offline demo agents (fixture access, intent detection)."""
from __future__ import annotations

import json
import re
from pathlib import Path

FIXTURE = Path(__file__).parent / "fixtures" / "market.json"
DATA = json.loads(FIXTURE.read_text(encoding="utf-8"))
SOURCE = "fixture:agents/fixtures/market.json"

ADVICE_RE = re.compile(r"\b(should i|is it (a good )?time to|which stocks?|buy now|sell now|double my money)\b", re.I)
EARNINGS_RE = re.compile(r"\b(earnings|report)\b", re.I)
TICKER_RE = re.compile(r"\bticker\b", re.I)
PCT_RE = re.compile(r"\b(percent|percentage|% change|change)\b", re.I)
DAYS_UNTIL_RE = re.compile(r"\bhow many days\b", re.I)


def intent(prompt: str) -> str:
    """Very small keyword router - deliberately naive; this is a demo agent."""
    if ADVICE_RE.search(prompt):
        return "advice"
    if PCT_RE.search(prompt) and re.search(r"\d", prompt):
        return "pct_change"
    if TICKER_RE.search(prompt):
        return "ticker"
    if EARNINGS_RE.search(prompt):
        return "earnings"
    return "unknown"


def find_company(prompt: str, include_aliases: bool) -> str | None:
    """Return the ticker for the first company name found in the prompt."""
    names = dict(DATA["companies"])
    if include_aliases:
        names.update(DATA["aliases"])
    low = prompt.casefold()
    # Longest names first so "Meta Platforms" wins over "Meta".
    for name in sorted(names, key=len, reverse=True):
        if re.search(rf"\b{re.escape(name.casefold())}\b", low):
            return names[name]
    return None


def find_ticker_symbol(prompt: str) -> str | None:
    """Return the first upper-case token that is a known ticker."""
    for tok in re.findall(r"\b[A-Z]{2,5}\b", prompt):
        if tok in DATA["earnings"]:
            return tok
    return None


def next_earnings(ticker: str, as_of: str | None) -> str | None:
    """Next calendar date on/after ``as_of``; without a date fall back to the static map."""
    if not as_of:
        return DATA["earnings"].get(ticker)
    from datetime import date
    today = date.fromisoformat(str(as_of)[:10])
    for day in sorted(DATA.get("earnings_calendar", {}).get(ticker, [])):
        if date.fromisoformat(day) >= today:
            return day
    return None
