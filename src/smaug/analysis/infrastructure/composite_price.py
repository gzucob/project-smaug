"""Compose the current-quote and history sources into one ``PriceProvider``.

The live quote and the closed-year history come from different vendors now
(ADR 0011): brapi serves the current quote for every ticker on the free plan,
while the multi-year daily history it withholds is sourced from Yahoo Finance.
The analysis use case still depends on a single ``PriceProvider``; this adapter
routes each call to the source that serves it, so the split stays invisible to
the use case and confined to the composition root.
"""

from __future__ import annotations

from smaug.analysis.domain.financials import MarketData, YearPrices
from smaug.analysis.domain.ports import CurrentQuoteProvider, PriceHistoryProvider


class CompositePriceProvider:
    """A ``PriceProvider`` delegating the quote and the year history separately."""

    def __init__(
        self, quote: CurrentQuoteProvider, history: PriceHistoryProvider
    ) -> None:
        self._quote = quote
        self._history = history

    async def get(self, ticker: str) -> MarketData:
        return await self._quote.get(ticker)

    async def year_prices(self, ticker: str, year: int) -> YearPrices:
        return await self._history.year_prices(ticker, year)
