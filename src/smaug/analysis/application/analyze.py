"""Analysis use case: read fundamentals + price → compute → persist.

Orchestration only. It talks to the three domain ports and the pure calculator;
it owns no Mongo, no HTTP and no SQL. Resilience mirrors the ingestion use case:
a ticker with no CVM data is skipped, and a price failure degrades gracefully to
null market multiples instead of losing the accounting indicators.

Each ticker yields **two perspectives** (see ``analysis-two-views`` design):

* the **live TTM** view — the trailing twelve months priced on the current
  nominal quote ("how is it valued now"); and
* one **closed-year** view per ingested annual DFP — that year's fundamentals
  priced on its dividend-adjusted average ("how it was priced during that year"),
  which is the basis the reference platforms use for historical multiples.

The share count behind the per-share indicators comes from CVM's filed capital
composition, per fiscal year, so a closed year divides by the shares that
existed *that* year. brapi's quote is only a fallback (see ``_shares_for``).
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from decimal import Decimal

from smaug.analysis.domain.calculator import compute
from smaug.analysis.domain.entities import (
    VIEW_CLOSED_YEAR,
    VIEW_TTM,
    TickerAnalysis,
)
from smaug.analysis.domain.financials import MarketData, StandardizedFinancials
from smaug.analysis.domain.ports import (
    AnalysisRepository,
    FundamentalsReader,
    PriceProvider,
    SharesReader,
)
from smaug.analysis.domain.ttm import build_ttm
from smaug.portfolio.domain.sectors import Sector, sector_of
from smaug.shared.errors import BrapiError
from smaug.shared.logging import get_logger

logger = get_logger(__name__)

Clock = Callable[[], datetime]

# The live TTM view is priced on the current nominal quote; each closed year is
# priced on its dividend-adjusted average (the platforms' historical basis).
_TTM_BASIS = "ttm_current_nominal"
_CLOSED_YEAR_BASIS = "adjusted_year_avg"


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _prior_year_annual(
    annuals: list[StandardizedFinancials], year: int
) -> StandardizedFinancials | None:
    """The closed-year DFP one year before ``year`` — the YoY growth base.

    Revenue/net-income growth compare a period against the prior closed year.
    For the TTM this is a clean year-over-year when the window ends in December;
    for a closed year it is simply the year before. Returns ``None`` when that
    year was not ingested, so growth degrades to null.
    """
    for annual in annuals:
        if annual.reference_date.year == year - 1:
            return annual
    return None


class AnalyzePortfolioUseCase:
    """Compute and store the TTM + closed-year indicators for a set of tickers."""

    def __init__(
        self,
        reader: FundamentalsReader,
        price_provider: PriceProvider,
        repository: AnalysisRepository,
        shares_reader: SharesReader,
        *,
        clock: Clock = _utc_now,
    ) -> None:
        self._reader = reader
        self._price_provider = price_provider
        self._repository = repository
        self._shares_reader = shares_reader
        self._clock = clock

    async def execute(self, tickers: Iterable[str]) -> list[TickerAnalysis]:
        results: list[TickerAnalysis] = []
        for ticker in tickers:
            results.extend(await self._analyze_ticker(ticker))
        return results

    async def _analyze_ticker(self, ticker: str) -> list[TickerAnalysis]:
        quarters = await self._reader.history(ticker)
        annuals = await self._reader.annuals(ticker)
        if not quarters and not annuals:
            logger.warning("No CVM fundamentals for %s; skipping", ticker)
            return []

        sector = sector_of(ticker)
        computed_at = self._clock()
        # One quote drives the TTM price and every closed-year repricing.
        quote = await self._current_quote(ticker)

        analyses: list[TickerAnalysis] = []
        ttm = await self._ttm_analysis(
            ticker, sector, quarters, annuals, quote, computed_at
        )
        if ttm is not None:
            analyses.append(ttm)
        for annual in annuals:  # oldest → newest
            analyses.append(
                await self._closed_year_analysis(
                    ticker, sector, annual, annuals, quote, computed_at
                )
            )

        for analysis in analyses:
            await self._repository.save(analysis)
        logger.info("Analyzed %s: %d view(s)", ticker, len(analyses))
        return analyses

    async def _ttm_analysis(
        self,
        ticker: str,
        sector: Sector,
        quarters: list[StandardizedFinancials],
        annuals: list[StandardizedFinancials],
        quote: MarketData,
        computed_at: datetime,
    ) -> TickerAnalysis | None:
        """The live view: trailing twelve months on the current nominal quote."""
        current = build_ttm(quarters, annuals[-1] if annuals else None)
        if current is None:
            logger.info("No TTM window for %s (needs 4 quarters)", ticker)
            return None
        year = current.reference_date.year
        previous = _prior_year_annual(annuals, year)
        market = dataclasses.replace(
            quote, shares=await self._shares_for(ticker, year, quote)
        )
        return TickerAnalysis(
            ticker=ticker,
            sector=sector,
            reference_date=current.reference_date,
            computed_at=computed_at,
            indicators=compute(current, previous, market),
            price=quote.price,
            price_nominal=quote.price,
            price_basis=_TTM_BASIS if quote.price is not None else None,
            view=VIEW_TTM,
        )

    async def _closed_year_analysis(
        self,
        ticker: str,
        sector: Sector,
        annual: StandardizedFinancials,
        annuals: list[StandardizedFinancials],
        quote: MarketData,
        computed_at: datetime,
    ) -> TickerAnalysis:
        """One closed fiscal year, priced on its dividend-adjusted average."""
        year = annual.reference_date.year
        previous = _prior_year_annual(annuals, year)
        market, nominal_avg = await self._market_for_year(ticker, year, quote)
        return TickerAnalysis(
            ticker=ticker,
            sector=sector,
            reference_date=annual.reference_date,
            computed_at=computed_at,
            indicators=compute(annual, previous, market),
            price=market.price,
            price_nominal=nominal_avg,
            price_basis=_CLOSED_YEAR_BASIS if market.price is not None else None,
            view=VIEW_CLOSED_YEAR,
        )

    async def _shares_for(
        self, ticker: str, year: int, quote: MarketData
    ) -> Decimal | None:
        """The share count for ``year``: CVM's filed capital, else brapi's quote.

        CVM is authoritative and per-year. The brapi fallback divides the market
        cap by the price, which only holds for a single share class — the cap is
        company-wide, so a dual-class ticker (PETR4, BBDC4) lands a few percent
        off. It is still better than no per-share indicator; see F12.
        """
        shares = await self._shares_reader.outstanding(ticker, year)
        if shares is not None:
            return shares
        logger.info("No CVM share count for %s %d; falling back to brapi", ticker, year)
        return quote.shares

    async def _current_quote(self, ticker: str) -> MarketData:
        try:
            return await self._price_provider.get(ticker)
        except BrapiError as exc:
            logger.warning(
                "No price for %s (%s); market multiples will be null", ticker, exc
            )
            return MarketData()

    async def _market_for_year(
        self, ticker: str, year: int, quote: MarketData
    ) -> tuple[MarketData, Decimal | None]:
        """Price the closed-year multiples on the year's dividend-adjusted average.

        P/E and P/B scale linearly with price, so repricing the *current* market
        cap onto the year's adjusted basis — ``current_cap × adjusted_avg /
        current_price`` — gives the historical multiple without needing a
        historical share count. A price failure degrades to null market multiples
        while keeping the accounting indicators — including the per-share ones,
        which need only the share count. Returns the market inputs plus the
        year's nominal average (stored for reference, not used in multiples).
        """
        shares = await self._shares_for(ticker, year, quote)
        if quote.price is None or quote.price == 0:
            return MarketData(shares=shares), None
        try:
            prices = await self._price_provider.year_prices(ticker, year)
        except BrapiError as exc:
            logger.warning(
                "No %d prices for %s (%s); year multiples will be null",
                year,
                ticker,
                exc,
            )
            return MarketData(shares=shares), None

        adjusted = prices.adjusted_avg
        effective_cap: Decimal | None = None
        if adjusted is not None and quote.market_cap is not None:
            effective_cap = quote.market_cap * adjusted / quote.price
        market = MarketData(price=adjusted, market_cap=effective_cap, shares=shares)
        return market, prices.nominal_avg
