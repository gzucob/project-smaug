"""Analysis use case: read fundamentals + price → compute → persist.

Orchestration only. It talks to the three domain ports and the pure calculator;
it owns no Mongo, no HTTP and no SQL. Resilience mirrors the ingestion use case:
a ticker with no CVM data is skipped, and a price failure degrades gracefully to
null market multiples instead of losing the accounting indicators.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import UTC, datetime

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

        try:
            market = await self._price_provider.get(ticker)
        except BrapiError as exc:
            logger.warning(
                "No price for %s (%s); market multiples will be null", ticker, exc
            )
            market = MarketData()

        analysis = TickerAnalysis(
            ticker=ticker,
            sector=sector_of(ticker),
            reference_date=current.reference_date,
            computed_at=self._clock(),
            indicators=compute(current, previous, market),
            price=market.price,
        )
        await self._repository.save(analysis)
        logger.info("Analyzed %s (ref %s)", ticker, current.reference_date)
        return analysis


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
