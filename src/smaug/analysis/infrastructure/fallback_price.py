"""Primary → fallback chains for the price sources (ADR 0013).

Yahoo is the primary for both the live quote and the year history; brapi is the
fallback for each. A chain tries the primary and, only if it fails at the
transport/HTTP layer (a ``BrapiError``) or returns no usable value, consults the
fallback. The use case still depends on the single ``PriceProvider`` port — the
chains sit behind it, wired at the composition root.
"""

from __future__ import annotations

from collections.abc import Sequence

from smaug.analysis.domain.financials import MarketData, YearPrices
from smaug.analysis.domain.indicators import NullReason
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
    """A ``PriceHistoryProvider`` chain: try each source until one has a price.

    Takes an ordered list of providers (Yahoo first, then any contracted source —
    ADR 0013 / #67); the contracted slot is wired in only when its key is set, so
    the chain is one, two, or three deep without any code change here. Each source
    is tried in turn and the first with a usable year price wins; a source that
    raises at the transport layer (a ``BrapiError``) is logged and skipped.

    When *no* source has a price, the empty result carries a reason only if every
    source that answered agreed the symbol is unknown
    (``PRICE_SYMBOL_NOT_FOUND``): that is a real delisting/rename (#64). If any
    source recognised the symbol but merely had no data for the year, the null
    stays a plain (transient) gap.
    """

    def __init__(self, providers: Sequence[PriceHistoryProvider]) -> None:
        if not providers:
            raise ValueError("FallbackPriceHistory needs at least one provider")
        self._providers = tuple(providers)

    async def year_prices(self, ticker: str, year: int) -> YearPrices:
        reasons: list[NullReason | None] = []
        for provider in self._providers:
            try:
                prices = await provider.year_prices(ticker, year)
            except BrapiError as exc:
                logger.warning(
                    "History source %d for %s failed (%s); trying next",
                    year,
                    ticker,
                    exc,
                )
                continue
            if prices.adjusted_avg is not None:
                return prices
            reasons.append(prices.null_reason)
        if reasons and all(r is NullReason.PRICE_SYMBOL_NOT_FOUND for r in reasons):
            return YearPrices(null_reason=NullReason.PRICE_SYMBOL_NOT_FOUND)
        return YearPrices()
