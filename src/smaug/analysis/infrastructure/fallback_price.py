"""Primary → fallback chains for the price sources (ADR 0013).

Yahoo is the primary for both the live quote and the year history; brapi is the
fallback for each. A chain tries the primary and, only if it fails at the
transport/HTTP layer (a ``BrapiError``) or returns no usable value, consults the
fallback. The use case still depends on the single ``PriceProvider`` port — the
chains sit behind it, wired at the composition root.
"""

from __future__ import annotations

from smaug.analysis.domain.financials import MarketData, YearPrices
from smaug.analysis.domain.ports import CurrentQuoteProvider, PriceHistoryProvider
from smaug.shared.errors import BrapiError
from smaug.shared.logging import get_logger

logger = get_logger(__name__)


class FallbackQuoteProvider:
    """A ``CurrentQuoteProvider`` that falls back when the primary yields no price."""

    def __init__(
        self, primary: CurrentQuoteProvider, fallback: CurrentQuoteProvider
    ) -> None:
        self._primary = primary
        self._fallback = fallback

    async def get(self, ticker: str) -> MarketData:
        try:
            data = await self._primary.get(ticker)
            if data.price is not None:
                return data
        except BrapiError as exc:
            logger.warning(
                "Primary quote for %s failed (%s); trying fallback", ticker, exc
            )
        return await self._fallback.get(ticker)


class FallbackPriceHistory:
    """A ``PriceHistoryProvider`` that falls back when the primary has no year price."""

    def __init__(
        self, primary: PriceHistoryProvider, fallback: PriceHistoryProvider
    ) -> None:
        self._primary = primary
        self._fallback = fallback

    async def year_prices(self, ticker: str, year: int) -> YearPrices:
        try:
            prices = await self._primary.year_prices(ticker, year)
            if prices.adjusted_avg is not None:
                return prices
        except BrapiError as exc:
            logger.warning(
                "Primary history %d for %s failed (%s); trying fallback",
                year,
                ticker,
                exc,
            )
        return await self._fallback.year_prices(ticker, year)
