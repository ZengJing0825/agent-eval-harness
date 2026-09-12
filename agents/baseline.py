"""Baseline demo agent (v1): rule-based, offline, deliberately imperfect.

Known weaknesses (so the comparison against ``v2`` is interesting):
* only recognises canonical company names, no aliases ("Google" -> ?)
* earnings lookup needs an explicit ticker symbol, not a company name
* percentage maths breaks on thousands separators ("1,200")
* cites a source only for earnings answers
"""
from __future__ import annotations

import re

from agents._common import DATA, SOURCE, find_company, find_ticker_symbol, intent

REFUSAL = "I can't give personalized buy or sell advice. I can share reported data such as tickers or earnings dates if that helps."


def answer(prompt: str, context: dict) -> dict:
    kind = intent(prompt)
    if kind == "advice":
        return {"answer": REFUSAL, "citations": []}

    if kind == "ticker":
        ticker = find_company(prompt, include_aliases=False)
        if ticker:
            return {"answer": f"The ticker symbol is {ticker}.", "citations": [], "data": {"ticker": ticker}}
        return {"answer": "I could not find that company.", "citations": []}

    if kind == "earnings":
        ticker = find_ticker_symbol(prompt)  # v1 needs the symbol spelled out
        if ticker and ticker in DATA["earnings"]:
            date = DATA["earnings"][ticker]
            return {"answer": f"{ticker} reports earnings on {date}.", "citations": [SOURCE],
                    "data": {"ticker": ticker, "date": date}}
        return {"answer": "I could not find an earnings date for that.", "citations": []}

    if kind == "pct_change":
        nums = [float(n) for n in re.findall(r"\d+(?:\.\d+)?", prompt)]  # bug: no comma handling
        if len(nums) >= 2 and nums[0] != 0:
            change = (nums[1] - nums[0]) / nums[0] * 100
            return {"answer": f"That is a {change:.2f}% change.", "citations": [], "data": {"pct": round(change, 2)}}
        return {"answer": "I need two numbers to compute a change.", "citations": []}

    return {"answer": "Sorry, I can't help with that.", "citations": []}
