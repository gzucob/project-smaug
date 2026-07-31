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
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import StrEnum

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
from smaug.analysis.domain.indicators import NullReason
from smaug.analysis.domain.market_cap import capitalize
from smaug.analysis.domain.ports import (
    AnalysisRepository,
    FundamentalsReader,
    PriceProvider,
    SharesReader,
)
from smaug.analysis.domain.ttm import build_ttm
from smaug.portfolio.domain.share_classes import ShareClass, listed_classes
from smaug.portfolio.domain.taxonomy import Classification, classify
from smaug.shared.errors import BrapiError, UnknownTickerError
from smaug.shared.logging import get_logger

logger = get_logger(__name__)

Clock = Callable[[], datetime]


class AnalysisStatus(StrEnum):
    """How one ticker fared in a run."""

    ANALYZED = "analyzed"
    SKIPPED = "skipped"  # nothing mirrored for it — not an error
    ERROR = "error"


@dataclass(frozen=True)
class TickerOutcome:
    """One ticker's result: its views, or why there are none."""

    ticker: str
    status: AnalysisStatus
    analyses: tuple[TickerAnalysis, ...]
    detail: str = ""


@dataclass(frozen=True)
class AnalysisRun:
    """Everything one run produced, per ticker."""

    outcomes: tuple[TickerOutcome, ...]

    @property
    def analyses(self) -> list[TickerAnalysis]:
        """Every view computed, flattened — what the CLI renders."""
        return [a for outcome in self.outcomes for a in outcome.analyses]

    @property
    def failed(self) -> tuple[TickerOutcome, ...]:
        return tuple(o for o in self.outcomes if o.status is AnalysisStatus.ERROR)


# How a ticker's B3 ``Classification`` is resolved for the stored analysis.
# Defaults to the committed snapshot; the CLI passes a registry-backed resolver
# so an on-demand ticker outside it degrades to the CVM single level (ADR 0024).
ClassificationResolver = Callable[[str], Classification]

# How a ticker's listed share classes are resolved for the cap (ADR 0014).
# Defaults to the curated nine (``listed_classes``); the CLI passes a
# registry-backed resolver so an on-demand ticker is capitalized too (#110).
ClassesResolver = Callable[[str], tuple[ShareClass, ...]]

# When a ticker was admitted to listing (``portfolio.domain.listings``). A closed
# year that ends before it cannot have a price in any source, which is a fact
# about the world rather than a gap of ours (#153). Defaults to "unknown", which
# simply never makes the claim.
ListedSinceResolver = Callable[[str], date | None]


def _unknown_listed_since(ticker: str) -> date | None:
    return None


def _default_classification(ticker: str) -> Classification:
    """Snapshot-only resolver: the committed B3 taxonomy, or an unknown ticker."""
    classification = classify(ticker, cvm_sector=None)
    if classification is None:
        raise UnknownTickerError(ticker)
    return classification


# Both views are priced on what the shares actually traded at: the live TTM on the
# current quote, each closed year on that year's nominal average (ADR 0018). The
# dividend-adjusted average is kept alongside as the total-return reference, but it
# is not what a valuation multiple divides by.
_TTM_BASIS = "ttm_current_nominal"
_CLOSED_YEAR_BASIS = "nominal_year_avg"


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


def _annuals_through(
    annuals: list[StandardizedFinancials], year: int
) -> list[StandardizedFinancials]:
    """The closed exercises up to and including ``year``, oldest → newest.

    A compounded rate looks backwards from the row it belongs to (#144). Handing
    the whole series to every closed year would give the 2020 row a rate computed
    partly from 2021–2024 — a figure nobody could have read in 2020, and one that
    would change every time a newer year is ingested.
    """
    return [a for a in annuals if a.reference_date.year <= year]


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
        classification_resolver: ClassificationResolver = _default_classification,
        classes_resolver: ClassesResolver = listed_classes,
        listed_since_resolver: ListedSinceResolver = _unknown_listed_since,
    ) -> None:
        self._reader = reader
        self._price_provider = price_provider
        self._repository = repository
        self._shares_reader = shares_reader
        self._clock = clock
        self._classification_resolver = classification_resolver
        self._classes_resolver = classes_resolver
        self._listed_since_resolver = listed_since_resolver

    async def execute(self, tickers: Iterable[str]) -> AnalysisRun:
        """Analyze each ticker, and never let one of them end the run.

        Nine tickers could afford to fail loudly and together: whatever broke was
        going to be looked at immediately anyway. Five hundred cannot — an
        unmapped account or a malformed payload on the two-hundredth would throw
        away the four hundred that had nothing wrong with them, and each is a
        minute of price requests. So a ticker's failure is recorded and the run
        continues, which is the shape the ingestion use case has always had.
        """
        outcomes: list[TickerOutcome] = []
        for ticker in tickers:
            outcomes.append(await self._analyze_guarded(ticker))
        return AnalysisRun(tuple(outcomes))

    async def _analyze_guarded(self, ticker: str) -> TickerOutcome:
        try:
            analyses = await self._analyze_ticker(ticker)
        except Exception as exc:  # noqa: BLE001 - one ticker must not end the run
            logger.exception("Analysis failed for %s", ticker)
            return TickerOutcome(
                ticker, AnalysisStatus.ERROR, (), f"{type(exc).__name__}: {exc}"
            )
        if not analyses:
            return TickerOutcome(
                ticker, AnalysisStatus.SKIPPED, (), "no CVM fundamentals"
            )
        return TickerOutcome(ticker, AnalysisStatus.ANALYZED, tuple(analyses))

    async def _analyze_ticker(self, ticker: str) -> list[TickerAnalysis]:
        quarters = await self._reader.history(ticker)
        annuals = await self._reader.annuals(ticker)
        if not quarters and not annuals:
            logger.warning("No CVM fundamentals for %s; skipping", ticker)
            return []

        classification = self._classification_resolver(ticker)
        computed_at = self._clock()
        # The live quote prices the TTM view only; each closed year prices on its
        # own year history (ADR 0012), so it is not needed there at all.
        quote = await self._current_quote(ticker)

        analyses: list[TickerAnalysis] = []
        ttm = await self._ttm_analysis(
            ticker, classification, quarters, annuals, quote, computed_at
        )
        if ttm is not None:
            analyses.append(ttm)
        for annual in annuals:  # oldest → newest
            analyses.append(
                await self._closed_year_analysis(
                    ticker, classification, annual, annuals, computed_at
                )
            )

        for analysis in analyses:
            await self._repository.save(analysis)
        logger.info("Analyzed %s: %d view(s)", ticker, len(analyses))
        return analyses

    async def _ttm_analysis(
        self,
        ticker: str,
        classification: Classification,
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
            classification=classification,
            reference_date=current.reference_date,
            computed_at=computed_at,
            # The whole closed series: a compounded rate runs over exercises, and
            # the TTM window is not one (#144), so its CAGR is the one the last
            # closed year carries.
            indicators=compute(current, previous, market, annuals),
            price=quote.price,
            # A live quote has no adjusted counterpart: nothing has been paid out
            # since it, so there is nothing to adjust it by.
            price_adjusted=None,
            price_basis=_TTM_BASIS if quote.price is not None else None,
            view=VIEW_TTM,
        )

    async def _closed_year_analysis(
        self,
        ticker: str,
        classification: Classification,
        annual: StandardizedFinancials,
        annuals: list[StandardizedFinancials],
        computed_at: datetime,
    ) -> TickerAnalysis:
        """One closed fiscal year, priced on what the shares traded at that year."""
        year = annual.reference_date.year
        previous = _prior_year_annual(annuals, year)
        market, adjusted_avg = await self._market_for_year(ticker, year)
        # Only the exercises up to and including this one: a 2020 row must be
        # computed from what was knowable in 2020, or its compounded rate would
        # be built from years that had not happened yet (#144).
        elapsed = _annuals_through(annuals, year)
        return TickerAnalysis(
            ticker=ticker,
            classification=classification,
            reference_date=annual.reference_date,
            computed_at=computed_at,
            indicators=compute(annual, previous, market, elapsed),
            price=market.price,
            price_adjusted=adjusted_avg,
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
        classes = self._classes_resolver(ticker)
        prices = {
            share_class.symbol: (
                quote.price
                if share_class.symbol == ticker
                else (await self._current_quote(share_class.symbol)).price
            )
            for share_class in classes
        }
        cap, cap_null_reason = capitalize(classes, counts, prices)
        return MarketData(
            price=quote.price,
            market_cap=cap,
            shares=await self._shares_reader.outstanding(ticker, year),
            cap_null_reason=cap_null_reason,
        )

    def _not_yet_listed(self, ticker: str, year: int) -> bool:
        """Whether ``year`` ends before ``ticker`` was listed.

        Only ever consulted once the cap has already come out null, so the claim
        needs *both* that no source had a price and that the registry puts the
        listing later. The date alone would not be enough: it records admission as
        the FCA now states it, and a filer that migrated segments can carry a date
        later than its real debut.
        """
        listed_since = self._listed_since_resolver(ticker)
        return listed_since is not None and listed_since.year > year

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
        """Price the closed-year multiples on what the shares traded at that year.

        The market cap is built from that year's own facts — each listed class at
        its own **nominal** average for the year, times the shares outstanding for
        that class (ADR 0014/0017) — rather than repriced from the live quote
        (superseding ADR 0001). The nominal average, not the dividend-adjusted one:
        a valuation multiple asks what the market paid for the company *that year*,
        and nobody bought PETR4 in 2022 at the R$13.15 the adjusted series now shows
        (ADR 0018). A closed-year row is therefore reproducible from the database and
        independent of the current quote: the year's prices come from Yahoo (ADR
        0011) and the counts from CVM's filed capital for that year (ADR 0004). A
        missing class price or class count degrades the cap to null; the per-share
        indicators (which need only the total) are unaffected. Returns the market
        inputs plus the year's adjusted average, kept as the total-return reference.
        """
        counts = await self._shares_reader.counts(ticker, year)
        own = await self._year_prices(ticker, year)
        prices: dict[str, Decimal | None] = {}
        classes = self._classes_resolver(ticker)
        for share_class in classes:
            symbol = share_class.symbol
            year_prices = (
                own if symbol == ticker else await self._year_prices(symbol, year)
            )
            prices[symbol] = year_prices.nominal_avg
        cap, cap_null_reason = capitalize(classes, counts, prices)
        if cap is None and own.null_reason is not None:
            # The history chain knows *why* there is no price: the symbol is unknown
            # everywhere (delisted/renamed). Prefer that structural cause over the
            # generic MISSING_PRICE the cap reports, so the null is non-transient in
            # ``smaug doctor`` (#64).
            cap_null_reason = own.null_reason
        if cap is None and self._not_yet_listed(ticker, year):
            # Outranks both: the year ends before the instrument existed, so there
            # was never a price to miss. Taken from the FCA, never from a price
            # vendor — a vendor's earliest datum is its own coverage, and for an
            # illiquid class it lands years late (#153).
            cap_null_reason = NullReason.NOT_YET_LISTED
        market = MarketData(
            price=own.nominal_avg,
            market_cap=cap,
            shares=await self._shares_reader.outstanding(ticker, year),
            cap_null_reason=cap_null_reason,
        )
        return market, own.adjusted_avg
