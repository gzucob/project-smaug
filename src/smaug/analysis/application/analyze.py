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

Both the share counts and the market cap come from CVM's filed capital
composition, per fiscal year, so a closed year is priced on the shares that
existed *that* year. The cap is summed over the company's listed share classes,
each on its own quote (ADR 0014) — so the two views differ only in *which* price
each class is summed at: the current quote, or that year's adjusted average.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from decimal import Decimal

from smaug.analysis.domain.calculator import compute
from smaug.analysis.domain.entities import (
    VIEW_CLOSED_YEAR,
    VIEW_TTM,
    TickerAnalysis,
)
from smaug.analysis.domain.financials import (
    MarketData,
    StandardizedFinancials,
    YearPrices,
)
from smaug.analysis.domain.market_cap import capitalize
from smaug.analysis.domain.ports import (
    AnalysisRepository,
    FundamentalsReader,
    PriceProvider,
    SharesReader,
)
from smaug.analysis.domain.ttm import build_ttm
from smaug.portfolio.domain.sectors import Sector, sector_of
from smaug.portfolio.domain.share_classes import listed_classes
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
        # The live quote prices the TTM view only; each closed year prices on its
        # own year history (ADR 0012), so it is not needed there at all.
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
                    ticker, sector, annual, annuals, computed_at
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
        market = await self._market_now(ticker, year, quote)
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
        computed_at: datetime,
    ) -> TickerAnalysis:
        """One closed fiscal year, priced on its dividend-adjusted average."""
        year = annual.reference_date.year
        previous = _prior_year_annual(annuals, year)
        market, nominal_avg = await self._market_for_year(ticker, year)
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

    async def _market_now(
        self, ticker: str, year: int, quote: MarketData
    ) -> MarketData:
        """The live market inputs: the ticker's quote + the company's current cap.

        The cap sums each listed class at its own current quote (ADR 0014), so a
        dual-class company (PETR4/PETR3) is no longer priced as if every share
        traded at the analyzed ticker's price, and a unit (SAPR11) gets a cap at
        all. The analyzed ticker's own quote is already in hand; only its sibling
        classes cost an extra call.
        """
        counts = await self._shares_reader.counts(ticker, year)
        prices = {
            share_class.symbol: (
                quote.price
                if share_class.symbol == ticker
                else (await self._current_quote(share_class.symbol)).price
            )
            for share_class in listed_classes(ticker)
        }
        cap, cap_null_reason = capitalize(ticker, counts, prices)
        return MarketData(
            price=quote.price,
            market_cap=cap,
            shares=await self._shares_reader.outstanding(ticker, year),
            cap_null_reason=cap_null_reason,
        )

    async def _current_quote(self, ticker: str) -> MarketData:
        try:
            return await self._price_provider.get(ticker)
        except BrapiError as exc:
            logger.warning(
                "No price for %s (%s); market multiples will be null", ticker, exc
            )
            return MarketData()

    async def _year_prices(self, symbol: str, year: int) -> YearPrices:
        try:
            return await self._price_provider.year_prices(symbol, year)
        except BrapiError as exc:
            logger.warning(
                "No %d prices for %s (%s); year multiples will be null",
                year,
                symbol,
                exc,
            )
            return YearPrices()

    async def _market_for_year(
        self, ticker: str, year: int
    ) -> tuple[MarketData, Decimal | None]:
        """Price the closed-year multiples on that year's dividend-adjusted average.

        The market cap is built from that year's own facts — each listed class at
        its own adjusted average for the year, times the shares filed for that
        class (ADR 0014) — rather than repriced from the live quote (superseding
        ADR 0001). A closed-year row is therefore reproducible from the database
        and independent of the current quote: the year's prices come from Yahoo
        (ADR 0011) and the counts from CVM's filed capital for that year (ADR
        0004). A missing class price or class count degrades the cap to null; the
        per-share indicators (which need only the filed total) are unaffected.
        Returns the market inputs plus the year's nominal average (for reference).
        """
        counts = await self._shares_reader.counts(ticker, year)
        own = await self._year_prices(ticker, year)
        prices: dict[str, Decimal | None] = {}
        for share_class in listed_classes(ticker):
            symbol = share_class.symbol
            year_prices = (
                own if symbol == ticker else await self._year_prices(symbol, year)
            )
            prices[symbol] = year_prices.adjusted_avg
        cap, cap_null_reason = capitalize(ticker, counts, prices)
        market = MarketData(
            price=own.adjusted_avg,
            market_cap=cap,
            shares=await self._shares_reader.outstanding(ticker, year),
            cap_null_reason=cap_null_reason,
        )
        return market, own.nominal_avg
