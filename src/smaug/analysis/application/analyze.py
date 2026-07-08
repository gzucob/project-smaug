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
from smaug.analysis.domain.ttm import build_ttm
from smaug.portfolio.domain.sectors import sector_of
from smaug.shared.errors import BrapiError
from smaug.shared.logging import get_logger

logger = get_logger(__name__)

Clock = Callable[[], datetime]

# The live TTM view is priced on the current nominal quote.
_PRICE_BASIS = "ttm_current_nominal"


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _prior_year_annual(
    annuals: list[StandardizedFinancials], year: int
) -> StandardizedFinancials | None:
    """The closed-year DFP one year before ``year`` — the YoY growth base.

    Revenue/net-income growth compare the current 12-month TTM against the prior
    closed year. When the TTM ends in December this is a clean year-over-year;
    for a mid-year rolling window it is the nearest available comparable. Returns
    ``None`` when that year was not ingested, so growth degrades to null.
    """
    for annual in annuals:
        if annual.reference_date.year == year - 1:
            return annual
    return None


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
        quarters = await self._reader.history(ticker)
        annuals = await self._reader.annuals(ticker)
        current = build_ttm(quarters, annuals[-1] if annuals else None)
        if current is None:
            logger.warning("No TTM fundamentals for %s; skipping", ticker)
            return None
        previous = _prior_year_annual(annuals, current.reference_date.year)

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
            price_nominal=market.price,
            price_basis=_PRICE_BASIS if market.price is not None else None,
        )
        await self._repository.save(analysis)
        logger.info("Analyzed %s TTM (ref %s)", ticker, current.reference_date)
        return analysis
