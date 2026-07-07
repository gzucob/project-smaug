"""Analysis use case: read fundamentals + price → compute → persist.

Orchestration only. It talks to the three domain ports and the pure calculator;
it owns no Mongo, no HTTP and no SQL. Resilience mirrors the ingestion use case:
a ticker with no CVM data is skipped, and a price failure degrades gracefully to
null market multiples instead of losing the accounting indicators.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from decimal import Decimal

from smaug.analysis.domain.calculator import compute
from smaug.analysis.domain.entities import TickerAnalysis
from smaug.analysis.domain.financials import MarketData, StandardizedFinancials
from smaug.analysis.domain.ports import (
    AnalysisRepository,
    FundamentalsReader,
    PriceProvider,
)
from smaug.portfolio.domain.sectors import sector_of
from smaug.shared.errors import BrapiError
from smaug.shared.logging import get_logger

logger = get_logger(__name__)

Clock = Callable[[], datetime]

# How the closed-year multiples are priced: the year's dividend-adjusted average.
_PRICE_BASIS = "adjusted_year_avg"


def _utc_now() -> datetime:
    return datetime.now(UTC)


class AnalyzePortfolioUseCase:
    """Compute and store indicators for a set of tickers."""

    def __init__(
        self,
        reader: FundamentalsReader,
        price_provider: PriceProvider,
        repository: AnalysisRepository,
        *,
        clock: Clock = _utc_now,
    ) -> None:
        self._reader = reader
        self._price_provider = price_provider
        self._repository = repository
        self._clock = clock

    async def execute(self, tickers: Iterable[str]) -> list[TickerAnalysis]:
        results: list[TickerAnalysis] = []
        for ticker in tickers:
            analysis = await self._analyze_ticker(ticker)
            if analysis is not None:
                results.append(analysis)
        return results

    async def _analyze_ticker(self, ticker: str) -> TickerAnalysis | None:
        history = await self._reader.history(ticker)
        if not history:
            logger.warning("No CVM fundamentals for %s; skipping", ticker)
            return None
        current = history[-1]
        previous = _prior_year(history, current)
        year = current.reference_date.year

        market, nominal_avg = await self._market_for_year(ticker, year)

        analysis = TickerAnalysis(
            ticker=ticker,
            sector=sector_of(ticker),
            reference_date=current.reference_date,
            computed_at=self._clock(),
            indicators=compute(current, previous, market),
            price=market.price,
            price_nominal=nominal_avg,
            price_basis=_PRICE_BASIS if market.price is not None else None,
        )
        await self._repository.save(analysis)
        logger.info("Analyzed %s (ref %s)", ticker, current.reference_date)
        return analysis

    async def _market_for_year(
        self, ticker: str, year: int
    ) -> tuple[MarketData, Decimal | None]:
        """Price the closed-year multiples on the year's dividend-adjusted average.

        P/E and P/B scale linearly with price, so repricing the *current* market
        cap onto the year's adjusted basis — ``current_cap × adjusted_avg /
        current_price`` — gives the historical multiple without needing a
        historical share count. A price failure degrades to null market multiples
        while keeping the accounting indicators. Returns the market inputs plus
        the year's nominal average (stored for reference, not used in multiples).
        """
        try:
            quote = await self._price_provider.get(ticker)
            prices = await self._price_provider.year_prices(ticker, year)
        except BrapiError as exc:
            logger.warning(
                "No price for %s (%s); market multiples will be null", ticker, exc
            )
            return MarketData(), None

        adjusted = prices.adjusted_avg
        effective_cap: Decimal | None = None
        if (
            adjusted is not None
            and quote.market_cap is not None
            and quote.price is not None
            and quote.price != 0
        ):
            effective_cap = quote.market_cap * adjusted / quote.price
        market = MarketData(
            price=adjusted, market_cap=effective_cap, shares=quote.shares
        )
        return market, prices.nominal_avg


def _prior_year(
    history: list[StandardizedFinancials], current: StandardizedFinancials
) -> StandardizedFinancials | None:
    """Same quarter, one year earlier — the only apples-to-apples YTD comparison."""
    for period in history:
        if (
            period.reference_date.month == current.reference_date.month
            and period.reference_date.year == current.reference_date.year - 1
        ):
            return period
    return None
