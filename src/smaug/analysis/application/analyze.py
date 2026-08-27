"""Analysis use case: read fundamentals + price → compute → persist.

Orchestration only. It talks to the three domain ports and the pure calculator;
it owns no Mongo, no HTTP and no SQL. Resilience mirrors the ingestion use case:
a ticker with no CVM data is skipped, and a price failure degrades gracefully to
null market multiples instead of losing the accounting indicators.

Each ticker yields **two perspectives** (see ``analysis-two-views`` design):

* the **live TTM** view — the trailing twelve months priced on the current
  B3 close ("how is it valued now"); and
* one **closed-year** view per ingested annual DFP — that year's fundamentals
  priced on B3's last available close in the fiscal year.

Both the share counts and the market cap come from CVM's filed capital
composition, per fiscal year, so a closed year is priced on the shares that
existed *that* year. The cap is summed over the company's listed share classes,
each on its own quote (ADR 0014) — so the two views differ only in *which* price
each class is summed at: the latest current-year close, or the fiscal-year close.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import cast

from smaug.analysis.domain.calculator import compute
from smaug.analysis.domain.dividends import cash_distributions
from smaug.analysis.domain.entities import (
    VIEW_CLOSED_YEAR,
    VIEW_TTM,
    TickerAnalysis,
)
from smaug.analysis.domain.financials import (
    ClassMarketValue,
    DebtEvidenceSnapshot,
    MarketData,
    ShareCountProvenance,
    ShareCounts,
    StandardizedFinancials,
    YearPrices,
)
from smaug.analysis.domain.indicators import NullReason
from smaug.analysis.domain.market_cap import capitalize
from smaug.analysis.domain.ports import (
    AnalysisRepository,
    CapitalProvenanceReader,
    CashEventReader,
    CountReasonReader,
    FundamentalsReader,
    PriceProvider,
    SharesReader,
)
from smaug.analysis.domain.ttm import build_ttm, build_ttm_as_of
from smaug.portfolio.domain.share_classes import (
    EconomicRightsStatus,
    PerShareClass,
    ShareClass,
    ShareClassMapping,
    ShareClassMappingStatus,
    UnitComponent,
)
from smaug.portfolio.domain.taxonomy import Classification, classify
from smaug.shared.errors import (
    CvmDownloadError,
    SourceError,
    SourceMalformedError,
    SourceTimeoutError,
    UnknownTickerError,
)
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


def _price_null_reason(error: SourceError) -> NullReason:
    """Map a B3 acquisition failure to a stable indicator null cause."""
    if isinstance(error, SourceTimeoutError):
        return NullReason.PRICE_SOURCE_TIMEOUT
    if isinstance(error, SourceMalformedError):
        return NullReason.PRICE_SOURCE_MALFORMED
    if isinstance(error, CvmDownloadError):
        return NullReason.PRICE_SOURCE_UNAVAILABLE
    return NullReason.PRICE_SOURCE_UNAVAILABLE


# How a ticker's B3 ``Classification`` is resolved for the stored analysis.
# Defaults to the committed snapshot; the CLI passes a registry-backed resolver
# so an on-demand ticker outside it degrades to the CVM single level (ADR 0024).
ClassificationResolver = Callable[[str], Classification]

# How a ticker's listed share classes are resolved for the cap (ADR 0014). The
# CLI passes a registry-backed resolver, unconditionally, for every ticker
# (#110, #212).
ClassesResolver = Callable[[str], tuple[ShareClass, ...]]
ClassMappingsResolver = Callable[[str], tuple[ShareClassMapping, ...]]
PerShareResolver = Callable[[str], tuple[UnitComponent, ...]]


def _default_classification(ticker: str) -> Classification:
    """Snapshot-only resolver: the committed B3 taxonomy, or an unknown ticker."""
    classification = classify(ticker, cvm_sector=None)
    if classification is None:
        raise UnknownTickerError(ticker)
    return classification


def _no_per_share_components(_ticker: str) -> tuple[UnitComponent, ...]:
    return ()


def _no_class_mappings(_ticker: str) -> tuple[ShareClassMapping, ...]:
    return ()


# Both views are point-in-time valuations (ADR 0057): B3's latest available close
# for the live view and its last close of the fiscal year for a closed exercise.
# The dividend-adjusted average stays alongside as a total-return reference, but
# never reaches valuation arithmetic.
_TTM_BASIS = "b3_latest_close"
_CLOSED_YEAR_BASIS = "b3_year_end_close"
_TTM_SHARE_BASIS = "cvm_latest_filed_outstanding_current_base"
_CLOSED_YEAR_SHARE_BASIS = "cvm_year_end_outstanding_current_base"
_LIQUIDITY_BASIS = "cpc03_cash_and_cash_equivalents"
_DEBT_BASIS = "cvm_bpp_explicit_interest_bearing"
_ROIC_TAX_BASIS = "br_statutory_34pct"


def _mapping_null_reason(
    mappings: tuple[ShareClassMapping, ...],
) -> NullReason | None:
    """Translate class-evidence status into a cap blocker."""
    if any(
        mapping.status is ShareClassMappingStatus.UNRESOLVED for mapping in mappings
    ):
        return NullReason.UNRESOLVED_SHARE_CLASS
    if any(
        mapping.economic_rights is EconomicRightsStatus.UNRESOLVED
        for mapping in mappings
    ):
        return NullReason.MISSING_ECONOMIC_RIGHTS
    return None


def _class_market_values(
    classes: tuple[ShareClass, ...],
    mappings: tuple[ShareClassMapping, ...],
    counts: ShareCounts | None,
    prices: Mapping[str, Decimal | None],
    price_reasons: Mapping[str, NullReason],
    *,
    price_basis: str,
    share_basis: str,
    count_reason: NullReason | None,
) -> tuple[ClassMarketValue, ...]:
    """Build the persisted class-by-class market-cap ledger."""
    by_symbol = {mapping.symbol: mapping for mapping in mappings}
    values: list[ClassMarketValue] = []
    for share_class in classes:
        mapping = by_symbol.get(share_class.symbol)
        class_id = (
            mapping.class_id
            if mapping is not None
            else f"ticker:{share_class.symbol}:{share_class.per_share_class.value}"
        )
        shares = None if counts is None else counts.of(share_class.per_share_class)
        price = prices.get(share_class.symbol)
        reason: NullReason | None = count_reason
        if reason is None and shares is None:
            reason = NullReason.MISSING_SHARE_COUNT
        if reason is None and price is None:
            reason = price_reasons.get(share_class.symbol, NullReason.MISSING_PRICE)
        values.append(
            ClassMarketValue(
                class_id=class_id,
                symbol=share_class.symbol,
                per_share_class=share_class.per_share_class,
                price=price,
                shares=shares,
                value=(
                    None
                    if reason is not None or price is None or shares is None
                    else price * shares
                ),
                price_basis=price_basis,
                share_basis=share_basis,
                null_reason=reason,
            )
        )
    return tuple(values)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _prior_year_annual(
    annuals: list[StandardizedFinancials], year: int
) -> StandardizedFinancials | None:
    """The closed-year DFP one year before ``year`` — closed-view YoY base."""
    for annual in annuals:
        if annual.reference_date.year == year - 1:
            return annual
    return None


def _prior_year_end(end: date) -> date:
    """The same fiscal endpoint one year earlier, including leap day."""
    try:
        return end.replace(year=end.year - 1)
    except ValueError:
        return end.replace(year=end.year - 1, day=28)


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
        classes_resolver: ClassesResolver,
        class_mapping_resolver: ClassMappingsResolver = _no_class_mappings,
        cash_event_reader: CashEventReader | None = None,
        per_share_resolver: PerShareResolver = _no_per_share_components,
    ) -> None:
        self._reader = reader
        self._price_provider = price_provider
        self._repository = repository
        self._shares_reader = shares_reader
        self._clock = clock
        self._classification_resolver = classification_resolver
        self._classes_resolver = classes_resolver
        self._class_mappings = class_mapping_resolver
        self._cash_events = cash_event_reader
        self._per_share = per_share_resolver

    async def execute(self, tickers: Iterable[str]) -> AnalysisRun:
        """Analyze each ticker, and never let one of them end the run.

        In an exchange-wide run, an unmapped account or a malformed payload for
        one ticker must not discard the successful work for every other ticker.
        A ticker's failure is recorded and the run continues, which is the shape
        the ingestion use case has always had.
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
        filed = await self._reader.annuals(ticker)
        if not quarters and not filed:
            logger.warning("No CVM fundamentals for %s; skipping", ticker)
            return []
        # Ahead of everything else: a fiscal year filed before the ticker's own
        # first B3 session is not a row this analysis produces at all (ADR 0048),
        # and filtering here — rather than per closed-year row — keeps a
        # suppressed year out of the TTM's growth/CAGR comparisons too.
        annuals = await self._traded_annuals(ticker, filed)

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

    async def _traded_annuals(
        self, ticker: str, annuals: list[StandardizedFinancials]
    ) -> list[StandardizedFinancials]:
        """The closed exercises ``ticker`` was actually trading in, oldest first.

        A fiscal year CVM filed before the security's own first B3 session is not
        a row this analysis produces — not with a null price, not with nothing
        (ADR 0048): the B3 tape cannot price a security before its first session.

        Walked oldest → newest so a year already known to have priced settles the
        question for every year after it (``seen_priced``): once true, a later
        empty year is an ordinary transient gap, not a pre-listing one, and the
        row stays. Only a year that has never yet priced asks the tape whether
        some *later* year does — the same read ``_market_for_year`` already
        performs for the rows that survive, so this costs nothing new. A ticker
        that never prices at all, in any direction, is left in rather than
        guessed at: the absence has no later year to explain it, so it stays a
        plain transient gap instead of a claim this analysis cannot back.
        """
        traded: list[StandardizedFinancials] = []
        seen_priced = False
        for annual in annuals:
            year = annual.reference_date.year
            if (await self._year_prices(ticker, year)).closing is not None:
                seen_priced = True
                traded.append(annual)
                continue
            if seen_priced or not await self._not_yet_traded(ticker, year):
                traded.append(annual)
        return traded

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
        prior_end = _prior_year_end(current.reference_date)
        previous = build_ttm_as_of(quarters, annuals, prior_end)
        distribution_end = computed_at.date()
        distribution_start = _prior_year_end(distribution_end) + timedelta(days=1)
        market = await self._market_now(
            ticker,
            year,
            quote,
            distribution_start=distribution_start,
            distribution_end=distribution_end,
        )
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
            price_source_code=quote.price_source_code,
            price_source_session=quote.price_source_session,
            # A live quote has no adjusted counterpart: nothing has been paid out
            # since it, so there is nothing to adjust it by.
            price_adjusted=None,
            price_basis=_TTM_BASIS,
            share_count_basis=_TTM_SHARE_BASIS,
            liquidity_basis=_LIQUIDITY_BASIS,
            debt_basis=_DEBT_BASIS,
            roic_tax_basis=_ROIC_TAX_BASIS,
            view=VIEW_TTM,
            filed_regime=current.filed_regime,
            regime_source=current.regime_source,
            issuer_name=current.issuer_name,
            cd_cvm=current.cd_cvm,
            cnpj=current.cnpj,
            debt_evidence=current.debt_evidence,
            debt_evidence_snapshot=DebtEvidenceSnapshot.CURRENT,
            share_class_mappings=self._class_mappings(ticker),
            class_market_values=market.class_market_values,
            capital_provenance=market.capital_provenance,
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
            price_source_code=market.price_source_code,
            price_source_session=market.price_source_session,
            price_adjusted=adjusted_avg,
            price_basis=_CLOSED_YEAR_BASIS,
            share_count_basis=_CLOSED_YEAR_SHARE_BASIS,
            liquidity_basis=_LIQUIDITY_BASIS,
            debt_basis=_DEBT_BASIS,
            roic_tax_basis=_ROIC_TAX_BASIS,
            view=VIEW_CLOSED_YEAR,
            filed_regime=annual.filed_regime,
            regime_source=annual.regime_source,
            issuer_name=annual.issuer_name,
            cd_cvm=annual.cd_cvm,
            cnpj=annual.cnpj,
            debt_evidence=annual.debt_evidence,
            debt_evidence_snapshot=DebtEvidenceSnapshot.HISTORICAL,
            share_class_mappings=self._class_mappings(ticker),
            class_market_values=market.class_market_values,
            capital_provenance=market.capital_provenance,
        )

    def _counts_null_reason(self, ticker: str, year: int) -> NullReason | None:
        """Read an optional class-count blocker without widening old fakes."""
        if not hasattr(self._shares_reader, "counts_null_reason"):
            return None
        reader = cast(CountReasonReader, self._shares_reader)
        return reader.counts_null_reason(ticker, year)

    async def _capital_provenance(
        self, ticker: str, year: int
    ) -> ShareCountProvenance | None:
        """Read optional capital evidence from the concrete share adapter."""
        if not hasattr(self._shares_reader, "capital_provenance"):
            return None
        reader = cast(CapitalProvenanceReader, self._shares_reader)
        return await reader.capital_provenance(ticker, year)

    async def _counts(self, ticker: str, year: int) -> ShareCounts | None:
        """Read class counts through the ADR 0017 fallback contract."""
        return await self._shares_reader.counts(ticker, year)

    async def _outstanding(self, ticker: str, year: int) -> Decimal | None:
        """Read the closing count through the ADR 0017 fallback contract."""
        return await self._shares_reader.outstanding(ticker, year)

    def _shares_null_reason(
        self,
        ticker: str,
        year: int,
        provenance: ShareCountProvenance | None,
        shares: Decimal | None,
    ) -> NullReason | None:
        """Carry treasury/filing blockers to the closing-share denominator."""
        reason = self._shares_reader.outstanding_null_reason(ticker, year)
        if reason is not None:
            return reason
        if provenance is None:
            return None
        if provenance.status == "missing_filing":
            return NullReason.MISSING_SHARE_COUNT
        if provenance.status == "missing_treasury_composition" and shares is None:
            return NullReason.MISSING_TREASURY_COMPOSITION
        return None

    def _market_count_null_reason(
        self,
        ticker: str,
        year: int,
        counts: ShareCounts | None,
        provenance: ShareCountProvenance | None,
        mappings: tuple[ShareClassMapping, ...],
    ) -> NullReason | None:
        """Name a cap blocker without turning the issued fallback into a null.

        ``SharesReader.counts`` follows ADR 0017: when treasury evidence is
        unreadable, it serves the filed issued count as an explicit approximation.
        That count is usable for the cap, while the provenance still records why it
        is not a proven outstanding count. Only an actually unavailable count may
        make ``missing_treasury_composition`` block the cap.
        """
        reason = self._counts_null_reason(ticker, year)
        if provenance is not None and provenance.status == "missing_filing":
            reason = NullReason.MISSING_SHARE_COUNT
        elif (
            counts is None
            and provenance is not None
            and provenance.status == "missing_treasury_composition"
        ):
            reason = NullReason.MISSING_TREASURY_COMPOSITION
        return reason or _mapping_null_reason(mappings)

    async def _market_now(
        self,
        ticker: str,
        year: int,
        quote: MarketData,
        *,
        distribution_start: date,
        distribution_end: date,
    ) -> MarketData:
        """The live market inputs: the ticker's quote + the company's current cap.

        The cap sums each listed class at its own current quote (ADR 0014), so a
        dual-class company (PETR4/PETR3) is no longer priced as if every share
        traded at the analyzed ticker's price, and a unit (SAPR11) gets a cap at
        all. The analyzed ticker's own quote is already in hand; only its sibling
        classes cost an extra call.
        """
        counts = await self._counts(ticker, year)
        classes = self._classes_resolver(ticker)
        mappings = self._class_mappings(ticker)
        provenance = await self._capital_provenance(ticker, year)
        count_reason = self._market_count_null_reason(
            ticker, year, counts, provenance, mappings
        )
        prices: dict[str, Decimal | None] = {}
        price_reasons: dict[str, NullReason] = {}
        for share_class in classes:
            class_quote = (
                quote
                if share_class.symbol == ticker
                else await self._current_quote(share_class.symbol)
            )
            prices[share_class.symbol] = class_quote.price
            if class_quote.price is None and class_quote.price_null_reason is not None:
                price_reasons[share_class.symbol] = class_quote.price_null_reason
        cap, cap_null_reason = capitalize(
            classes,
            counts,
            prices,
            price_null_reasons=price_reasons,
            count_null_reason=count_reason,
        )
        distributions, distributions_reason = await self._cash_distributions(
            ticker, distribution_start, distribution_end
        )
        shares = await self._outstanding(ticker, year)
        return MarketData(
            price=quote.price,
            price_source_code=quote.price_source_code,
            price_source_session=quote.price_source_session,
            market_cap=cap,
            shares=shares,
            cash_distributions=distributions,
            price_null_reason=quote.price_null_reason,
            cap_null_reason=cap_null_reason,
            shares_null_reason=self._shares_null_reason(
                ticker, year, provenance, shares
            ),
            cash_distributions_null_reason=distributions_reason,
            class_price_null_reasons=price_reasons,
            class_market_values=_class_market_values(
                classes,
                mappings,
                counts,
                prices,
                price_reasons,
                price_basis=_TTM_BASIS,
                share_basis=_TTM_SHARE_BASIS,
                count_reason=count_reason,
            ),
            capital_provenance=provenance,
        )

    async def _cash_distributions(
        self, ticker: str, start: date, end: date
    ) -> tuple[Decimal | None, NullReason | None]:
        """B3 cash rights per analyzed security over an explicit ex-date window."""
        if self._cash_events is None:
            return None, NullReason.MISSING_CASH_DISTRIBUTIONS
        components = self._per_share(ticker)
        if not components:
            return None, NullReason.MISSING_ECONOMIC_RIGHTS

        quantities: dict[PerShareClass, int] = {}
        for component in components:
            quantities[component.per_share_class] = (
                quantities.get(component.per_share_class, 0) + component.quantity
            )

        timeline = await self._shares_reader.restatement_timeline(ticker)
        total = Decimal(0)
        for per_share_class, quantity in quantities.items():
            events = await self._cash_events.cash_events(
                ticker, per_share_class=per_share_class
            )
            if events is None:
                return None, NullReason.MISSING_CASH_DISTRIBUTIONS
            amount = cash_distributions(events, start, end, timeline)
            if amount is None:
                return None, NullReason.MISSING_CASH_DISTRIBUTION_VALUE
            total += Decimal(quantity) * amount
        return total, None

    async def _sibling_not_yet_traded(
        self,
        classes: tuple[ShareClass, ...],
        prices: Mapping[str, Decimal | None],
        year: int,
    ) -> bool:
        """Whether the cap is null because a *sibling* class had not started
        trading yet — never the analysed ticker itself, which ``_traded_annuals``
        has already kept out of this year's row if it applied (ADR 0048).
        """
        for share_class in classes:
            if prices.get(share_class.symbol) is None and await self._not_yet_traded(
                share_class.symbol, year
            ):
                return True
        return False

    async def _not_yet_traded(self, symbol: str, year: int) -> bool:
        """Whether ``symbol`` prints its first B3 session only after ``year``.

        Reads B3's own tape forward from ``year``, never the FCA's
        ``Data_Inicio_Listagem`` (ADR 0048): that column is provably wrong at
        exchange scale in both directions — it reads 2006 for TAEE4 (#164), same
        as TAEE11, and it reads 2025 for Natura (NATU3), which B3 shows trading
        since at least 2012. ``symbol`` is either the analysed ticker itself
        (``_traded_annuals``) or a sibling class in its cap
        (``_sibling_not_yet_traded``) — the question is the same either way.
        Bounded at the current year, so a symbol that never trades again reads as
        a plain gap rather than a claim about a debut that has not happened.
        """
        limit = self._clock().year
        for candidate_year in range(year + 1, limit + 1):
            if (await self._year_prices(symbol, candidate_year)).closing is not None:
                return True
        return False

    async def _current_quote(self, ticker: str) -> MarketData:
        try:
            return await self._price_provider.get(ticker)
        except SourceError as exc:
            logger.warning(
                "No price for %s (%s); market multiples will be null", ticker, exc
            )
            return MarketData(price_null_reason=_price_null_reason(exc))

    async def _year_prices(self, symbol: str, year: int) -> YearPrices:
        try:
            return await self._price_provider.year_prices(symbol, year)
        except SourceError as exc:
            logger.warning(
                "No %d prices for %s (%s); year multiples will be null",
                year,
                symbol,
                exc,
            )
            return YearPrices(null_reason=_price_null_reason(exc))

    async def _market_for_year(
        self, ticker: str, year: int
    ) -> tuple[MarketData, Decimal | None]:
        """Price a closed exercise at the fiscal cut-off.

        The cap sums each listed class at B3's last available close in the fiscal
        year times that class's CVM year-end outstanding count (ADR 0014/0017/0057).
        This is a point-in-time stock paired with another point-in-time stock: an
        issuance or buyback no longer gets the closing count multiplied across the
        whole year's mean price. A missing class price or class count nulls the
        complete cap. The year's dividend-adjusted average remains a separate
        total-return reference and never reaches valuation arithmetic.
        """
        counts = await self._counts(ticker, year)
        own = await self._year_prices(ticker, year)
        prices: dict[str, Decimal | None] = {}
        price_reasons: dict[str, NullReason] = {}
        classes = self._classes_resolver(ticker)
        mappings = self._class_mappings(ticker)
        provenance = await self._capital_provenance(ticker, year)
        count_reason = self._market_count_null_reason(
            ticker, year, counts, provenance, mappings
        )
        for share_class in classes:
            symbol = share_class.symbol
            year_prices = (
                own if symbol == ticker else await self._year_prices(symbol, year)
            )
            prices[symbol] = year_prices.closing
            if year_prices.closing is None and year_prices.null_reason is not None:
                price_reasons[symbol] = year_prices.null_reason
        cap, cap_null_reason = capitalize(
            classes,
            counts,
            prices,
            price_null_reasons=price_reasons,
            count_null_reason=count_reason,
        )
        if cap is None and own.null_reason is not None:
            # The history chain knows *why* there is no price: the symbol is unknown
            # everywhere (delisted/renamed). Prefer that structural cause over the
            # generic MISSING_PRICE the cap reports, so the null is non-transient in
            # ``smaug doctor`` (#64).
            cap_null_reason = own.null_reason
        if (
            cap is None
            and cap_null_reason is NullReason.MISSING_PRICE
            and await self._sibling_not_yet_traded(classes, prices, year)
        ):
            # The analysed ticker itself was trading this year (``_traded_annuals``
            # would have dropped the row otherwise) but a *sibling* class was not:
            # a unit's component can carry an FCA listing date from the company's
            # IPO while B3 prints no session for it for years, because nearly
            # every share moves bundled in the unit until enough free float trades
            # loose (#164) — TAEE4 files 2006, its first B3 session is 2017-05-10.
            # The FCA cannot tell this apart from a class that is simply illiquid,
            # so the tape does (ADR 0048).
            cap_null_reason = NullReason.NOT_YET_LISTED
        class_values = _class_market_values(
            classes,
            mappings,
            counts,
            prices,
            price_reasons,
            price_basis=_CLOSED_YEAR_BASIS,
            share_basis=_CLOSED_YEAR_SHARE_BASIS,
            count_reason=count_reason,
        )
        if cap_null_reason is NullReason.NOT_YET_LISTED:
            class_values = tuple(
                replace(value, null_reason=NullReason.NOT_YET_LISTED)
                if value.value is None
                else value
                for value in class_values
            )
        distributions, distributions_reason = await self._cash_distributions(
            ticker, date(year, 1, 1), date(year, 12, 31)
        )
        shares = await self._outstanding(ticker, year)
        market = MarketData(
            price=own.closing,
            price_source_code=own.closing_code,
            price_source_session=own.closing_session,
            market_cap=cap,
            shares=shares,
            cash_distributions=distributions,
            price_null_reason=own.null_reason,
            cap_null_reason=cap_null_reason,
            shares_null_reason=self._shares_null_reason(
                ticker, year, provenance, shares
            ),
            cash_distributions_null_reason=distributions_reason,
            class_price_null_reasons=price_reasons,
            class_market_values=class_values,
            capital_provenance=provenance,
        )
        return market, own.adjusted_avg
