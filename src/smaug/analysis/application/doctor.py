"""Coverage report over the persisted analysis (#47 — the M0 gate).

Answers *"what is true right now"* about the derived data (RULES_DOCS: that
answer must come from a command, never from prose). For every persisted
exercise of every ticker it reports, per indicator, a **known status**: a
value, a null with a named cause (the ``NullReason`` vocabulary of #30/ADR
0008, attributed upstream by the calculator), or an *unclassified* null — a
reportable status of its own, never a silent omission.

Read-only: it reads back through the ``AnalysisRepository`` port and never
recomputes or persists (root ``AGENTS.md``: the ``analyze`` CLI is the only
write surface). The classification is not redone here — it reads the reason
each null was born with.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import cast

from smaug.analysis.domain.entities import AnalysisView, TickerAnalysis
from smaug.analysis.domain.financials import (
    Cpc41WindowProvenance,
    DebtBlocker,
    DebtCoverageEvidence,
    DebtEvidenceSnapshot,
)
from smaug.analysis.domain.indicators import (
    INDICATOR_CONTRACT,
    Indicators,
    NullDisposition,
    NullReason,
    indicator_names,
    null_disposition,
)
from smaug.analysis.domain.outcomes import AnalysisOutcome
from smaug.analysis.domain.ports import (
    AnalysisOutcomeReader,
    AnalysisRepository,
    AnalysisStorageScope,
    AnalysisStorageScopeReader,
)
from smaug.portfolio.domain.sectors import Sector

# A CVM-registry-backed resolver, injected at the composition root (cli.py) —
# no per-ticker override exists to default to (#212).
SectorResolver = Callable[[str], Sector]


# These are the CPC 41 fields whose strict null can be explained by the
# separately persisted market-convention pair.  ``eps`` is the historical
# compatibility alias for ``eps_basic`` and is deliberately retained as a
# separate cell in the coverage report.  Row-level summaries below deduplicate
# that alias explicitly.
_MARKET_ALTERNATIVE_BY_INDICATOR = {
    "eps": "eps_basic_market",
    "eps_basic": "eps_basic_market",
    "pe_basic": "pe_basic_market",
}
_STRICT_CPC41_INDICATORS = (
    "eps",
    "eps_basic",
    "eps_diluted",
    "pe_basic",
    "pe_diluted",
)
_STRICT_BASIC_EPS_NULL_REASONS = frozenset(
    {
        NullReason.MISSING_CPC41_DISCLOSURE,
        NullReason.MISSING_ECONOMIC_RIGHTS,
        NullReason.MISSING_WEIGHTED_AVERAGE_SHARES,
    }
)
_LEGITIMATE_NULL_DISPOSITIONS = frozenset(
    {
        NullDisposition.MATHEMATICALLY_UNDEFINED,
        NullDisposition.HISTORICAL_PERIOD_DOES_NOT_EXIST,
    }
)


@dataclass(frozen=True)
class MarketAlternative:
    """The persisted market-convention companion to a strict indicator."""

    indicator: str
    has_value: bool
    basis: str
    provenance: tuple[str, ...]


@dataclass(frozen=True)
class IndicatorCoverage:
    """The status of one indicator in one exercise.

    ``has_value`` → a value was computed. Otherwise ``reason`` names the cause,
    or is ``None`` for an *unclassified* null (e.g. a zero denominator).
    """

    indicator: str
    has_value: bool
    reason: NullReason | None
    market_alternative: MarketAlternative | None = None

    @property
    def is_unclassified(self) -> bool:
        return not self.has_value and self.reason is None

    @property
    def status(self) -> str:
        """A single enumerable label: ``value``, a ``NullReason``, ``unclassified``."""
        if self.has_value:
            return "value"
        return self.reason.value if self.reason is not None else "unclassified"

    @property
    def disposition(self) -> NullDisposition | None:
        """The stable top-level disposition of this named null, if any."""
        if self.has_value or self.reason is None:
            return None
        return null_disposition(self.reason)


@dataclass(frozen=True)
class ExerciseCoverage:
    """Coverage of every indicator for one ticker/view/period."""

    view: AnalysisView
    reference_date: date
    indicators: tuple[IndicatorCoverage, ...]
    debt_evidence: DebtCoverageEvidence | None = None
    debt_evidence_snapshot: DebtEvidenceSnapshot | None = None
    cpc41_window_provenance: Cpc41WindowProvenance | None = None
    price_source_code: str | None = None
    price_source_session: date | None = None

    @property
    def values(self) -> int:
        return sum(1 for c in self.indicators if c.has_value)

    @property
    def named_nulls(self) -> int:
        return sum(
            1 for c in self.indicators if not c.has_value and c.reason is not None
        )

    @property
    def unclassified(self) -> int:
        return sum(1 for c in self.indicators if c.is_unclassified)


@dataclass(frozen=True)
class DebtCoverageSummary:
    """Reconciled counts for one debt decision versus its dependent cells."""

    universe: str
    views: tuple[AnalysisView, ...]
    period_definition: str
    cell_definition: str
    persisted_decisions: int
    incomplete_decisions: int
    inapplicable_decisions: int
    incomplete_indicator_cells: int
    legacy_snapshots: int
    unclassified_blockers: int


@dataclass(frozen=True)
class CoverageScope:
    """The population behind a doctor report.

    Ticker counts describe the requested universe; row counts describe what the
    repository actually stores.  ``stale_rows`` and ``legacy_rows`` are read
    from all persisted rows, rather than inferred from the latest view.  A
    repository that only implements the historical read methods reports zero for
    those optional storage diagnostics (the real SQL repository implements the
    scope contract).
    """

    requested_tickers: int
    persisted_tickers: int
    no_analysis_tickers: int
    persisted_exercises: int
    # All rows selected for the request, including superseded rows.  The short
    # ``persisted_tickers`` name above is deliberately not reused for rows.
    persisted_rows: int = 0
    stale_rows: int = 0
    legacy_rows: int = 0

    @property
    def requested(self) -> int:
        """Short alias for consumers rendering a scope table."""
        return self.requested_tickers

    @property
    def persisted(self) -> int:
        """Short alias for consumers rendering a scope table."""
        return self.persisted_tickers

    @property
    def no_analysis(self) -> int:
        """Short alias for consumers rendering a scope table."""
        return self.no_analysis_tickers

    @property
    def stale(self) -> int:
        """Short alias for the count of superseded persisted rows."""
        return self.stale_rows

    @property
    def legacy(self) -> int:
        """Short alias for the count of rows without current provenance."""
        return self.legacy_rows

    @property
    def stored_rows(self) -> int:
        """Unambiguous alias for all rows selected from persistence."""
        return self.persisted_rows


@dataclass(frozen=True)
class CoverageTotals:
    """Counts and denominator-aware measures over indicator cells."""

    total_cells: int
    values: int
    nulls: int
    unclassified: int
    inapplicable: int
    mathematically_undefined: int
    primary_source_unavailable: int
    recoverable_gap: int
    historical_period_does_not_exist: int

    @property
    def named_nulls(self) -> int:
        """Null cells carrying a ``NullReason``."""
        return self.nulls - self.unclassified

    @property
    def genuine_inapplicability(self) -> int:
        """Nulls that are economically inapplicable to the filed regime."""
        return self.inapplicable

    @property
    def missing_or_recoverable(self) -> int:
        """Nulls that may be addressed by source or mapping work.

        Primary-source disclosure absence and definitive recoverable
        acquisition/mapping gaps are both missing calculable data. Inapplicable,
        mathematical, and legitimate historical outcomes are excluded.
        """
        return self.primary_source_unavailable + self.recoverable_gap

    @property
    def missing_data(self) -> int:
        """Alias for the report's missing-or-recoverable product measure."""
        return self.missing_or_recoverable

    @staticmethod
    def _percentage(count: int, denominator: int) -> float:
        return 100.0 * count / denominator if denominator else 0.0

    def percentage_of_cells(self, count: int) -> float:
        """Return ``count`` as a percentage of all indicator cells."""
        return self._percentage(count, self.total_cells)

    def percentage_of_nulls(self, count: int) -> float:
        """Return ``count`` as a percentage of null cells."""
        return self._percentage(count, self.nulls)

    @property
    def missing_or_recoverable_pct_of_cells(self) -> float:
        return self.percentage_of_cells(self.missing_or_recoverable)

    @property
    def missing_or_recoverable_pct_of_nulls(self) -> float:
        return self.percentage_of_nulls(self.missing_or_recoverable)

    @property
    def missing_data_pct_of_cells(self) -> float:
        """Missing/recoverable percentage using all indicator cells."""
        return self.missing_or_recoverable_pct_of_cells

    @property
    def missing_data_pct_of_nulls(self) -> float:
        """Missing/recoverable percentage using null cells only."""
        return self.missing_or_recoverable_pct_of_nulls

    @property
    def inapplicable_pct_of_cells(self) -> float:
        return self.percentage_of_cells(self.inapplicable)

    @property
    def inapplicable_pct_of_nulls(self) -> float:
        return self.percentage_of_nulls(self.inapplicable)

    @property
    def dispositions(self) -> Mapping[NullDisposition, int]:
        """All top-level buckets, including buckets whose count is zero."""
        return {
            NullDisposition.INAPPLICABLE: self.inapplicable,
            NullDisposition.MATHEMATICALLY_UNDEFINED: (self.mathematically_undefined),
            NullDisposition.PRIMARY_SOURCE_UNAVAILABLE: (
                self.primary_source_unavailable
            ),
            NullDisposition.RECOVERABLE_GAP: self.recoverable_gap,
            NullDisposition.HISTORICAL_PERIOD_DOES_NOT_EXIST: (
                self.historical_period_does_not_exist
            ),
        }


@dataclass(frozen=True)
class AlternativeFieldCoverage:
    """Coverage of one persisted market-convention alternative field."""

    indicator: str
    rows: int
    fields: int
    basis: str
    provenance: tuple[str, ...]


@dataclass(frozen=True)
class MarketAlternativeCoverage:
    """Disjoint strict CPC 41 null groups and deduplicated basic-EPS rows.

    The first three counters are indicator cells and form a partition of all
    null cells in the strict CPC 41 family.  The row counters are a separate
    view over persisted analysis rows: ``eps`` and ``eps_basic`` are aliases in
    that view and therefore count once per row, while ``fields`` deliberately
    preserves both cells for auditability.
    """

    strict_null_fields_with_market_alternative: int
    strict_null_fields_without_market_alternative: int
    legitimate_mathematical_or_historical_null_fields: int
    basic_eps_blocked_rows: int
    basic_eps_rows_with_any_market_alternative: int
    basic_eps_rows_without_market_alternative: int
    basic_eps_rows_with_eps_basic_market: int
    basic_eps_rows_with_pe_basic_market: int
    basic_eps_fields_with_market_alternative: int
    basic_eps_fields_without_market_alternative: int
    alternatives: tuple[AlternativeFieldCoverage, ...]

    @property
    def strict_null_fields(self) -> int:
        """All strict CPC 41 null cells, including legitimate nulls."""
        return (
            self.strict_null_fields_with_market_alternative
            + self.strict_null_fields_without_market_alternative
            + self.legitimate_mathematical_or_historical_null_fields
        )

    @property
    def basic_eps_null_fields(self) -> int:
        """Basic-EPS family cells considered in the deduplicated row view."""
        return (
            self.basic_eps_fields_with_market_alternative
            + self.basic_eps_fields_without_market_alternative
        )


@dataclass(frozen=True)
class TickerCoverage:
    """Every persisted exercise for one ticker (TTM first, then closed years).

    An empty ``exercises`` means nothing is persisted for the ticker — itself a
    reportable state, not a silent gap.
    """

    ticker: str
    sector: Sector
    exercises: tuple[ExerciseCoverage, ...]
    outcome: AnalysisOutcome | None = None

    @property
    def latest_outcome(self) -> AnalysisOutcome | None:
        """The latest run status, kept separate from indicator-cell coverage."""
        return self.outcome


@dataclass(frozen=True)
class DoctorReport:
    """The full coverage report: one entry per requested ticker."""

    tickers: tuple[TickerCoverage, ...]
    scope: CoverageScope | None = None

    def __post_init__(self) -> None:
        """Give direct report fixtures the same scope contract as the use case."""
        if self.scope is None:
            persisted = sum(bool(ticker.exercises) for ticker in self.tickers)
            object.__setattr__(
                self,
                "scope",
                CoverageScope(
                    requested_tickers=len(self.tickers),
                    persisted_tickers=persisted,
                    no_analysis_tickers=len(self.tickers) - persisted,
                    persisted_exercises=sum(
                        len(ticker.exercises) for ticker in self.tickers
                    ),
                    persisted_rows=sum(
                        len(ticker.exercises) for ticker in self.tickers
                    ),
                ),
            )

    @property
    def coverage_scope(self) -> CoverageScope:
        """The explicit population metadata used by the CLI report."""
        assert self.scope is not None
        return self.scope

    @property
    def market_alternative_coverage(self) -> MarketAlternativeCoverage:
        """Coverage of strict CPC 41 nulls and their persisted alternatives."""
        return _market_alternative_coverage(self.tickers)

    @property
    def totals(self) -> CoverageTotals:
        """Aggregate cell counts grouped by the stable null disposition."""
        counts: dict[NullDisposition, int] = dict.fromkeys(NullDisposition, 0)
        values = unclassified = total = 0
        for ticker in self.tickers:
            for exercise in ticker.exercises:
                for cell in exercise.indicators:
                    total += 1
                    if cell.has_value:
                        values += 1
                    elif cell.disposition is None:
                        unclassified += 1
                    else:
                        disposition = cell.disposition
                        assert disposition is not None
                        counts[disposition] += 1
        nulls = total - values
        return CoverageTotals(
            total_cells=total,
            values=values,
            nulls=nulls,
            unclassified=unclassified,
            inapplicable=counts[NullDisposition.INAPPLICABLE],
            mathematically_undefined=counts[NullDisposition.MATHEMATICALLY_UNDEFINED],
            primary_source_unavailable=counts[
                NullDisposition.PRIMARY_SOURCE_UNAVAILABLE
            ],
            recoverable_gap=counts[NullDisposition.RECOVERABLE_GAP],
            historical_period_does_not_exist=counts[
                NullDisposition.HISTORICAL_PERIOD_DOES_NOT_EXIST
            ],
        )

    @property
    def disposition_counts(self) -> Mapping[NullDisposition, int]:
        """Every disposition count, including zero-valued categories."""
        return self.totals.dispositions

    @property
    def total_cells(self) -> int:
        """Total indicator cells in persisted exercises."""
        return self.totals.total_cells

    @property
    def values(self) -> int:
        """Indicator cells containing a computed value."""
        return self.totals.values

    @property
    def nulls(self) -> int:
        """Indicator cells without a computed value."""
        return self.totals.nulls

    @property
    def genuine_inapplicability(self) -> int:
        """Null cells in the inapplicable top-level disposition."""
        return self.totals.genuine_inapplicability

    @property
    def missing_or_recoverable(self) -> int:
        """Null cells in either missing-primary-source or recoverable-gap."""
        return self.totals.missing_or_recoverable

    @property
    def unclassified(self) -> int:
        """The exchange-scale coverage gate (#169): a cell with no named cause.

        Every other null already carries a ``NullReason`` an ADR put a name to;
        one that does not is either a mapping bug or a cause not yet vocabularied
        (ADR 0008) — either way, the one finding here that asks for work, not a
        state of the world already explained.
        """
        return sum(e.unclassified for t in self.tickers for e in t.exercises)

    @property
    def debt_coverage(self) -> DebtCoverageSummary:
        """Count one raw-BPP decision separately from dependent indicator cells."""
        exercises = [e for t in self.tickers for e in t.exercises]
        evidence = [e.debt_evidence for e in exercises]
        return DebtCoverageSummary(
            universe="requested tickers with persisted analysis rows",
            views=("ttm_live", "closed_year"),
            period_definition=(
                "reference_date of each persisted TTM or closed-year row"
            ),
            cell_definition=(
                "one debt decision per persisted row; dependent indicator cells "
                "are counted separately"
            ),
            persisted_decisions=sum(item is not None for item in evidence),
            incomplete_decisions=sum(
                item is not None
                and item.primary_blocker is DebtBlocker.INCOMPLETE_DEBT_COVERAGE
                for item in evidence
            ),
            inapplicable_decisions=sum(
                item is not None
                and item.primary_blocker is DebtBlocker.INAPPLICABLE_REGIME
                for item in evidence
            ),
            incomplete_indicator_cells=sum(
                cell.reason is NullReason.INCOMPLETE_DEBT_COVERAGE
                for exercise in exercises
                for cell in exercise.indicators
            ),
            legacy_snapshots=sum(
                item is None
                or exercise.debt_evidence_snapshot is DebtEvidenceSnapshot.LEGACY
                for exercise, item in zip(exercises, evidence, strict=False)
            ),
            unclassified_blockers=sum(
                item.unclassified_blockers for item in evidence if item is not None
            ),
        )


def _coverage_of(indicators: Indicators) -> tuple[IndicatorCoverage, ...]:
    """Classify every indicator cell as value / named-null / unclassified."""
    cells: list[IndicatorCoverage] = []
    for name in indicator_names():
        has_value = getattr(indicators, name) is not None
        reason = None if has_value else indicators.null_reasons.get(name)
        alternative_name = _MARKET_ALTERNATIVE_BY_INDICATOR.get(name)
        alternative = None
        if alternative_name is not None:
            contract = INDICATOR_CONTRACT[alternative_name]
            alternative = MarketAlternative(
                indicator=alternative_name,
                has_value=getattr(indicators, alternative_name) is not None,
                basis=contract.basis,
                provenance=contract.provenance,
            )
        cells.append(
            IndicatorCoverage(
                name,
                has_value=has_value,
                reason=reason,
                market_alternative=alternative,
            )
        )
    return tuple(cells)


def _market_alternative_for_cell(
    cell: IndicatorCoverage,
    cells: Mapping[str, IndicatorCoverage],
) -> MarketAlternative | None:
    """Resolve alternative metadata for persisted rows and direct report fixtures."""
    if cell.market_alternative is not None:
        return cell.market_alternative
    alternative_name = _MARKET_ALTERNATIVE_BY_INDICATOR.get(cell.indicator)
    if alternative_name is None:
        return None
    contract = INDICATOR_CONTRACT[alternative_name]
    alternative_cell = cells.get(alternative_name)
    return MarketAlternative(
        indicator=alternative_name,
        has_value=alternative_cell is not None and alternative_cell.has_value,
        basis=contract.basis,
        provenance=contract.provenance,
    )


def _alternative_has_value(
    alternative_name: str,
    cells: Mapping[str, IndicatorCoverage],
) -> bool:
    """Read an alternative value, including metadata-only direct fixtures."""
    alternative_cell = cells.get(alternative_name)
    if alternative_cell is not None:
        return alternative_cell.has_value
    return any(
        cell.market_alternative is not None
        and cell.market_alternative.indicator == alternative_name
        and cell.market_alternative.has_value
        for cell in cells.values()
    )


def _is_basic_eps_blocked_row(cells: Mapping[str, IndicatorCoverage]) -> bool:
    """Identify one persisted row in the CPC 41 basic-EPS baseline."""
    basic = cells.get("eps_basic")
    return (
        basic is not None
        and not basic.has_value
        and basic.reason in _STRICT_BASIC_EPS_NULL_REASONS
    )


def _market_alternative_coverage(
    tickers: Sequence[TickerCoverage],
) -> MarketAlternativeCoverage:
    """Aggregate strict alternatives without counting aliases as extra rows."""
    with_alternative = without_alternative = legitimate = 0
    basic_rows = rows_with_any = rows_without = 0
    rows_with_eps_market = rows_with_pe_market = 0
    basic_fields_with = basic_fields_without = 0
    alternative_counts: dict[str, int] = dict.fromkeys(
        _MARKET_ALTERNATIVE_BY_INDICATOR.values(), 0
    )

    for ticker in tickers:
        for exercise in ticker.exercises:
            cells = {cell.indicator: cell for cell in exercise.indicators}
            for name in _STRICT_CPC41_INDICATORS:
                cell = cells.get(name)
                if cell is None or cell.has_value:
                    continue
                alternative = _market_alternative_for_cell(cell, cells)
                if cell.disposition in _LEGITIMATE_NULL_DISPOSITIONS:
                    legitimate += 1
                elif alternative is not None and alternative.has_value:
                    with_alternative += 1
                else:
                    without_alternative += 1

            if not _is_basic_eps_blocked_row(cells):
                continue
            basic_rows += 1
            eps_market = _alternative_has_value("eps_basic_market", cells)
            pe_market = _alternative_has_value("pe_basic_market", cells)
            if eps_market:
                rows_with_eps_market += 1
            if pe_market:
                rows_with_pe_market += 1
            if eps_market or pe_market:
                rows_with_any += 1
            else:
                rows_without += 1

            for name, alternative_name in _MARKET_ALTERNATIVE_BY_INDICATOR.items():
                cell = cells.get(name)
                if cell is None or cell.has_value:
                    continue
                if _alternative_has_value(alternative_name, cells):
                    basic_fields_with += 1
                    alternative_counts[alternative_name] += 1
                else:
                    basic_fields_without += 1

    alternatives = tuple(
        AlternativeFieldCoverage(
            indicator=name,
            rows=(
                rows_with_eps_market
                if name == "eps_basic_market"
                else rows_with_pe_market
            ),
            fields=alternative_counts[name],
            basis=INDICATOR_CONTRACT[name].basis,
            provenance=INDICATOR_CONTRACT[name].provenance,
        )
        for name in alternative_counts
    )
    return MarketAlternativeCoverage(
        strict_null_fields_with_market_alternative=with_alternative,
        strict_null_fields_without_market_alternative=without_alternative,
        legitimate_mathematical_or_historical_null_fields=legitimate,
        basic_eps_blocked_rows=basic_rows,
        basic_eps_rows_with_any_market_alternative=rows_with_any,
        basic_eps_rows_without_market_alternative=rows_without,
        basic_eps_rows_with_eps_basic_market=rows_with_eps_market,
        basic_eps_rows_with_pe_basic_market=rows_with_pe_market,
        basic_eps_fields_with_market_alternative=basic_fields_with,
        basic_eps_fields_without_market_alternative=basic_fields_without,
        alternatives=alternatives,
    )


def _exercise_of(analysis: TickerAnalysis) -> ExerciseCoverage:
    return ExerciseCoverage(
        view=analysis.view,
        reference_date=analysis.reference_date,
        indicators=_coverage_of(analysis.indicators),
        debt_evidence=analysis.debt_evidence,
        debt_evidence_snapshot=analysis.debt_evidence_snapshot,
        cpc41_window_provenance=analysis.indicators.cpc41_window_provenance,
        price_source_code=analysis.price_source_code,
        price_source_session=analysis.price_source_session,
    )


class DoctorUseCase:
    """Build the coverage report from the persisted analysis (read-only)."""

    def __init__(
        self,
        repository: AnalysisRepository,
        *,
        sector_resolver: SectorResolver,
        outcome_reader: AnalysisOutcomeReader | None = None,
    ) -> None:
        self._repository = repository
        self._sector_resolver = sector_resolver
        self._outcome_reader = outcome_reader

    async def execute(self, tickers: Iterable[str]) -> DoctorReport:
        # A repeated code must represent one requested ticker, otherwise both
        # the percentages and the no-analysis count depend on caller ordering.
        requested = tuple(dict.fromkeys(tickers))
        outcomes = await self._latest_outcomes(requested)
        coverages: list[TickerCoverage] = []
        for ticker in requested:
            exercises: list[ExerciseCoverage] = []
            ttm = await self._repository.latest(ticker)
            if ttm is not None:
                exercises.append(_exercise_of(ttm))
            for closed in await self._repository.history(ticker):
                exercises.append(_exercise_of(closed))
            coverages.append(
                TickerCoverage(
                    ticker,
                    self._sector_resolver(ticker),
                    tuple(exercises),
                    outcomes.get(ticker),
                )
            )

        storage = await self._storage_scope(requested)
        persisted_tickers = sum(bool(ticker.exercises) for ticker in coverages)
        return DoctorReport(
            tuple(coverages),
            scope=CoverageScope(
                requested_tickers=len(requested),
                persisted_tickers=persisted_tickers,
                no_analysis_tickers=len(requested) - persisted_tickers,
                persisted_exercises=sum(len(ticker.exercises) for ticker in coverages),
                persisted_rows=(
                    sum(len(ticker.exercises) for ticker in coverages)
                    if storage is None
                    else storage.persisted_rows
                ),
                stale_rows=0 if storage is None else storage.stale_rows,
                legacy_rows=0 if storage is None else storage.legacy_rows,
            ),
        )

    async def _latest_outcomes(
        self, tickers: Sequence[str]
    ) -> Mapping[str, AnalysisOutcome]:
        """Read optional run outcomes without requiring old repository fakes."""
        reader = self._outcome_reader
        if reader is None:
            if not hasattr(self._repository, "latest_outcomes"):
                return {}
            reader = cast(AnalysisOutcomeReader, self._repository)
        return await reader.latest_outcomes(tickers)

    async def _storage_scope(
        self, tickers: Sequence[str]
    ) -> AnalysisStorageScope | None:
        """Read optional all-row diagnostics without breaking old test fakes."""
        if not hasattr(self._repository, "storage_scope"):
            return None
        reader = cast(AnalysisStorageScopeReader, self._repository)
        return await reader.storage_scope(tickers)
