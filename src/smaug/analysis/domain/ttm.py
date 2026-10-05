"""Trailing-twelve-months (TTM) assembly (Phase 2 domain, pure).

Rebuilds a 12-month period from CVM quarters. Two rules the CVM data forces:

* **Flow vs. stock.** Income-statement lines (revenue, net income, EBIT, D&A) are
  *flows*: the TTM value is the **sum** of the four trailing isolated quarters.
  Balance-sheet lines (equity, assets, cash equivalents, investments, debt) are
  *stocks*: the TTM value is the **latest** quarter, never a sum.
* **The missing Q4.** Companies file three ITRs (Q1–Q3) plus one annual DFP, so
  the isolated Q4 has no statement of its own — it is derived as
  ``annual − (Q1+Q2+Q3 isolated)``.

The ITR income statement is filed year-to-date, but a period may arrive as either
the isolated quarter or the accumulated figure. Rather than assume, each period is
normalised to an isolated quarter using its own span (``period_start`` →
``reference_date``): a ~3-month span is already isolated; a longer span is
year-to-date and becomes ``YTDₙ − YTDₙ₋₁``. When the span is unknown the value is
taken as already isolated (the observed CVM behaviour).

The catch: the DRE and the DFC do not share a period basis. In the real CVM
files the DRE arrives as isolated quarters while the DFC is always year-to-date,
so DFC-sourced flows (D&A and dividends) are isolated on the DFC's own span
(``dfc_period_start``), separately from the DRE flows.
"""

from __future__ import annotations

import calendar
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import date, timedelta
from decimal import Decimal
from itertools import pairwise
from typing import cast

from smaug.analysis.domain.financials import (
    Cpc41AccountEvidence,
    Cpc41EvidenceStatus,
    Cpc41PeriodProvenance,
    Cpc41SelectionStatus,
    Cpc41WindowProvenance,
    SourceAccountEvidence,
    SourceAccountStatus,
    StandardizedFinancials,
    expected_regime,
)
from smaug.analysis.domain.indicators import NullReason

# Flows summed over the window; EBITDA is recomposed from EBIT+D&A. DRE flows are
# isolated on the DRE span, DFC flows (D&A, dividends) on the DFC span — the two
# statements use different period bases in the CVM files.
_DRE_FLOW_FIELDS = (
    "revenue",
    "net_income",
    "net_income_total",
    "ebit",
    "gross_profit",
    # Regime-specific CVM DRE lines (ADR 0015) — flows like any other income
    # line, and left signed as filed: summing preserves the sign the CVM used.
    # Complete bank ratios are resolved separately from their own CVM roots
    # and exact stock/flow periods. These partial leaves remain statement facts.
    "loan_loss_provision",
    "fee_income",
    "personnel_expense",
    "admin_expense",
)
_DFC_FLOW_FIELDS = ("dep_amort", "dividends_paid", "cfo", "capex")
_FLOW_FIELDS = _DRE_FLOW_FIELDS + _DFC_FLOW_FIELDS
_TTM_QUARTERS = 4
_ISOLATED_SPAN_MONTHS = 3
_ONE_DAY = timedelta(days=1)

Flows = dict[str, Decimal | None]


@dataclass(frozen=True, slots=True)
class _WeightedPeriod:
    """One isolated period's numerator and weighted denominator in share-days."""

    profit: Decimal
    share_days: Decimal
    days: int
    start: date
    end: date
    multiplier: Decimal


@dataclass(frozen=True, slots=True)
class _WeightedRunning:
    """The validated cumulative denominator lineage for one fiscal year."""

    profit: Decimal
    share_days: Decimal
    days: int
    end: date | None
    multiplier: Decimal | None
    valid: bool
    count: int


def _months(start: date | None, end: date) -> int | None:
    if start is None:
        return None
    return (end.year - start.year) * 12 + (end.month - start.month) + 1


def _sub(a: Decimal | None, b: Decimal | None) -> Decimal | None:
    return None if a is None or b is None else a - b


def _add(a: Decimal | None, b: Decimal | None) -> Decimal | None:
    return None if a is None or b is None else a + b


def _period_days(period: StandardizedFinancials) -> int | None:
    """The inclusive day span that the issuer's filed EPS covers."""
    start = period.period_start
    if start is None or start > period.reference_date:
        return None
    days = (period.reference_date - start).days + 1
    return days if days > 0 else None


def _weighted_period(
    period: StandardizedFinancials, *, diluted: bool
) -> _WeightedPeriod | None:
    """Recover a filed period's weighted denominator without using closing shares.

    ``net_income / CPC41 EPS`` is a usable weighted denominator only after the
    mapper has proved either a common issuer base or a security-equivalent base
    for one period-identified preferred subclass. The domain object carries
    that proof in ``cpc41``; a missing or zero result is therefore a strict null
    rather than an estimate.
    """
    disclosure = period.cpc41
    if disclosure is None or disclosure.security_multiplier is None:
        return None
    base_eps = disclosure.diluted_base_eps if diluted else disclosure.basic_base_eps
    days = _period_days(period)
    start = period.period_start
    if (
        base_eps is None
        or base_eps == 0
        or days is None
        or start is None
        or period.net_income is None
        or disclosure.security_multiplier <= 0
    ):
        return None

    average_shares = period.net_income / base_eps
    if average_shares <= 0:
        return None
    return _WeightedPeriod(
        profit=period.net_income,
        share_days=average_shares * days,
        days=days,
        start=start,
        end=period.reference_date,
        multiplier=disclosure.security_multiplier,
    )


def _same_annual_eps_window(
    annual: StandardizedFinancials | None,
    refs: Sequence[date],
    profit: Decimal | None,
    quarters: Sequence[StandardizedFinancials],
) -> bool:
    if annual is None or not refs or profit is None or profit != annual.net_income:
        return False
    year = annual.reference_date.year
    regime = annual.filed_regime or expected_regime(annual.sector)
    if any(
        (period.filed_regime or expected_regime(period.sector)) != regime
        or (period.cd_cvm and annual.cd_cvm and period.cd_cvm != annual.cd_cvm)
        for period in quarters
        if period.reference_date in refs
    ):
        return False
    return (
        annual.reference_date == date(year, 12, 31)
        and annual.period_start == date(year, 1, 1)
        and set(refs)
        == {
            date(year, 3, 31),
            date(year, 6, 30),
            date(year, 9, 30),
            date(year, 12, 31),
        }
    )


def _annual_eps_sources(
    annual: StandardizedFinancials, fields: Sequence[str]
) -> tuple[SourceAccountEvidence, ...]:
    entries: list[SourceAccountEvidence] = []
    for field in fields:
        value = getattr(annual, field)
        source = _cpc41_source_entry(annual, diluted=field == "eps_diluted")
        dated = f"{field}[{annual.reference_date}]"
        if source is not None:
            entries.append(replace(source, field=dated))
        entries.append(
            SourceAccountEvidence(
                field=field,
                statement="derived",
                status=SourceAccountStatus.DERIVED,
                formula="filed EPS for the identical annual/TTM flow span",
                dependencies=(dated,) if source is not None else (),
                expected=(
                    "resolution=filed_same_ttm_span",
                    f"period_start={annual.period_start}",
                    f"reference_date={annual.reference_date}",
                    f"resolved_value={value}",
                ),
                consumer_indicators=(
                    field,
                    "pe_diluted" if field == "eps_diluted" else "pe_basic",
                ),
            )
        )
    return tuple(entries)


def _previous_quarter_end(value: date) -> date | None:
    """The prior calendar-quarter endpoint for a semiannual/9-month filing."""
    if value.month not in (3, 6, 9, 12):
        return None
    quarter = (value.month - 1) // 3
    previous_year = value.year if quarter else value.year - 1
    previous_month = quarter * 3 or 12
    return date(
        previous_year,
        previous_month,
        calendar.monthrange(previous_year, previous_month)[1],
    )


def _quarters_are_contiguous(refs: list[date]) -> bool:
    """Whether the selected reference dates form consecutive calendar quarters."""
    ordered = sorted(refs)
    return all(
        _previous_quarter_end(current) == previous
        for previous, current in pairwise(ordered)
    )


def _valid_ytd_prefix(running: _WeightedRunning, current: date) -> bool:
    """Whether the already-read quarters form the complete prefix before YTD."""
    if running.end is None or running.count == 0:
        return False
    if running.end != _previous_quarter_end(current):
        return False
    year_start = date(current.year, 1, 1)
    return running.days == (running.end - year_start).days + 1


def _weighted_difference(
    current: _WeightedPeriod,
    running: _WeightedRunning,
    *,
    start: date,
) -> _WeightedPeriod | None:
    """Isolate a YTD denominator from the preceding cumulative disclosure."""
    if running.multiplier != current.multiplier:
        return None
    days = current.days - running.days
    share_days = current.share_days - running.share_days
    if days <= 0 or share_days <= 0:
        return None
    return _WeightedPeriod(
        profit=current.profit - running.profit,
        share_days=share_days,
        days=days,
        start=start,
        end=current.end,
        multiplier=current.multiplier,
    )


def _isolate_weighted_year(
    periods: list[StandardizedFinancials], *, diluted: bool
) -> tuple[dict[date, _WeightedPeriod | None], _WeightedRunning]:
    """Isolate a year's weighted denominators on the same basis as its flows."""
    isolated: dict[date, _WeightedPeriod | None] = {}
    running = _WeightedRunning(Decimal(0), Decimal(0), 0, None, None, True, 0)

    for period in periods:
        measured = _weighted_period(period, diluted=diluted)
        span = _months(period.period_start, period.reference_date)
        is_ytd = span is not None and span > _ISOLATED_SPAN_MONTHS
        next_count = running.count + 1

        if measured is None or not running.valid:
            isolated[period.reference_date] = None
            running = _WeightedRunning(
                measured.profit if measured is not None else running.profit,
                measured.share_days if measured is not None else running.share_days,
                measured.days if measured is not None else running.days,
                period.reference_date,
                measured.multiplier if measured is not None else running.multiplier,
                False,
                next_count,
            )
            continue

        if is_ytd:
            # CVM's accumulated ITR columns must start on 1 January and follow
            # the immediately preceding quarter. Otherwise subtraction would
            # silently turn a missing or partial prefix into a quarter.
            valid_prefix = (
                period.period_start == date(period.reference_date.year, 1, 1)
                and period.reference_date.month in (6, 9)
                and _valid_ytd_prefix(running, period.reference_date)
            )
            start = (
                running.end + _ONE_DAY if running.end is not None else measured.start
            )
            candidate = (
                _weighted_difference(measured, running, start=start)
                if valid_prefix
                else None
            )
            isolated[period.reference_date] = candidate
            running = _WeightedRunning(
                measured.profit,
                measured.share_days,
                measured.days,
                period.reference_date,
                measured.multiplier,
                candidate is not None,
                next_count,
            )
            continue

        same_multiplier = (
            running.count == 0 or running.multiplier == measured.multiplier
        )
        candidate = measured if same_multiplier else None
        isolated[period.reference_date] = candidate
        if candidate is None:
            running = _WeightedRunning(
                running.profit,
                running.share_days,
                running.days,
                period.reference_date,
                running.multiplier,
                False,
                next_count,
            )
        else:
            running = _WeightedRunning(
                running.profit + measured.profit,
                running.share_days + measured.share_days,
                running.days + measured.days,
                period.reference_date,
                measured.multiplier,
                True,
                next_count,
            )

    return isolated, running


def _weighted_isolated(
    quarters: list[StandardizedFinancials],
    annual: StandardizedFinancials | None,
    *,
    diluted: bool,
) -> dict[date, _WeightedPeriod | None]:
    """Build isolated weighted periods, including a provable annual Q4."""
    by_year: dict[int, list[StandardizedFinancials]] = {}
    for quarter in quarters:
        by_year.setdefault(quarter.reference_date.year, []).append(quarter)

    isolated: dict[date, _WeightedPeriod | None] = {}
    cumulative: dict[int, _WeightedRunning] = {}
    for year, periods in by_year.items():
        year_isolated, running = _isolate_weighted_year(
            sorted(periods, key=lambda p: p.reference_date), diluted=diluted
        )
        isolated.update(year_isolated)
        cumulative[year] = running

    if annual is not None:
        annual_running = cumulative.get(annual.reference_date.year)
        measured = _weighted_period(annual, diluted=diluted)
        year_start = date(annual.reference_date.year, 1, 1)
        complete_prefix = (
            annual_running is not None
            and annual_running.valid
            and annual_running.count == 3
            and annual_running.end == date(annual.reference_date.year, 9, 30)
            and annual_running.days == (annual_running.end - year_start).days + 1
        )
        if (
            complete_prefix
            and measured is not None
            and annual_running is not None
            and annual_running.multiplier == measured.multiplier
        ):
            q4 = _weighted_difference(
                measured,
                annual_running,
                start=date(annual.reference_date.year, 10, 1),
            )
            isolated[annual.reference_date] = q4
        else:
            isolated[annual.reference_date] = None
    return isolated


def _ttm_weighted_eps(
    periods: dict[date, _WeightedPeriod | None],
    refs: list[date],
) -> Decimal | None:
    """Calculate TTM EPS from the four isolated, share-day-weighted periods."""
    selected = [periods.get(ref) for ref in refs]
    if any(period is None for period in selected):
        return None
    complete = [period for period in selected if period is not None]
    if len({period.multiplier for period in complete}) != 1:
        return None

    ordered = sorted(complete, key=lambda period: period.start)
    for previous, current in pairwise(ordered):
        if current.start != previous.end + _ONE_DAY:
            return None
    total_days = sum((period.days for period in complete), 0)
    share_days = sum((period.share_days for period in complete), Decimal(0))
    if total_days <= 0 or share_days <= 0:
        return None
    profit = sum((period.profit for period in complete), Decimal(0))
    base_eps = profit / (share_days / Decimal(total_days))
    return base_eps * complete[0].multiplier


_SPECIFIC_CPC41_REASONS = (
    NullReason.MISSING_ECONOMIC_RIGHTS,
    NullReason.UNRESOLVED_SHARE_CLASS,
    NullReason.MISSING_UNIT_COMPOSITION,
    NullReason.MISSING_CPC41_DISCLOSURE,
    NullReason.MISSING_WEIGHTED_AVERAGE_SHARES,
)


def _cpc41_period_reason(
    period: StandardizedFinancials, *, diluted: bool
) -> NullReason:
    """Retain the most specific CPC 41 blocker already known for one period."""
    explicit = (
        period.eps_diluted_null_reason if diluted else period.eps_basic_null_reason
    )
    if explicit in _SPECIFIC_CPC41_REASONS:
        assert explicit is not None
        return explicit
    disclosure = period.cpc41
    if disclosure is None:
        # A class-specific EPS can be valid for the annual view while the
        # complete class reconciliation required by TTM is not. Source evidence
        # proves that the filed leaf existed, so this is an economic-rights gap,
        # not an absent CPC 41 disclosure.
        entry = _cpc41_source_entry(period, diluted=diluted)
        if (
            entry is not None
            and entry.found
            and entry.status is SourceAccountStatus.MAPPED
        ):
            return NullReason.MISSING_ECONOMIC_RIGHTS
        return NullReason.MISSING_CPC41_DISCLOSURE
    base_eps = disclosure.diluted_base_eps if diluted else disclosure.basic_base_eps
    if base_eps is None or disclosure.security_multiplier is None:
        return NullReason.MISSING_CPC41_DISCLOSURE
    return NullReason.MISSING_WEIGHTED_AVERAGE_SHARES


def _cpc41_ttm_null_reason(
    periods: Sequence[StandardizedFinancials],
    weighted: dict[date, _WeightedPeriod | None],
    refs: list[date],
    *,
    diluted: bool,
) -> NullReason:
    """Name the root CPC 41 blocker instead of flattening it at the TTM edge."""
    by_date = {period.reference_date: period for period in periods}
    selected = [weighted.get(ref) for ref in refs]
    period_reasons = [
        _cpc41_period_reason(period, diluted=diluted)
        for ref in refs
        if (period := by_date.get(ref)) is not None
    ]
    for reason in _SPECIFIC_CPC41_REASONS[:-1]:
        if reason in period_reasons:
            return reason
    if _cpc41_multiplier_mismatch(periods, refs, diluted=diluted):
        return NullReason.MISSING_UNIT_COMPOSITION
    for ref, candidate in zip(refs, selected, strict=True):
        if candidate is None:
            period = by_date.get(ref)
            return (
                _cpc41_period_reason(period, diluted=diluted)
                if period is not None
                else NullReason.MISSING_WEIGHTED_AVERAGE_SHARES
            )
    complete = [candidate for candidate in selected if candidate is not None]
    if len({candidate.multiplier for candidate in complete}) != 1:
        return NullReason.MISSING_UNIT_COMPOSITION
    return NullReason.MISSING_WEIGHTED_AVERAGE_SHARES


_CPC41_CLASS_REASONS = frozenset(
    {
        NullReason.MISSING_ECONOMIC_RIGHTS,
        NullReason.MISSING_UNIT_COMPOSITION,
        NullReason.UNRESOLVED_SHARE_CLASS,
    }
)


def _cpc41_source_entry(
    period: StandardizedFinancials, *, diluted: bool
) -> SourceAccountEvidence | None:
    """Find the raw account inventory for one period and CPC 41 basis."""
    field = "eps_diluted" if diluted else "eps_basic"
    return next(
        (item for item in period.source_account_evidence if item.field == field),
        None,
    )


def _cpc41_accounts(
    period: StandardizedFinancials,
) -> tuple[Cpc41AccountEvidence, ...]:
    """Copy raw CVM 3.99 identities into the selected-window audit trail."""
    accounts: list[Cpc41AccountEvidence] = []
    for diluted in (False, True):
        entry = _cpc41_source_entry(period, diluted=diluted)
        if entry is None:
            continue
        if entry.status is SourceAccountStatus.MAPPED:
            if _cpc41_base_eps(period, diluted=diluted) is not None:
                selection = Cpc41SelectionStatus.SELECTED
            elif _cpc41_period_reason(period, diluted=diluted) in _CPC41_CLASS_REASONS:
                selection = Cpc41SelectionStatus.AMBIGUOUS
            else:
                selection = Cpc41SelectionStatus.NOT_SELECTED
        elif entry.status is SourceAccountStatus.PRESENT_UNREADABLE:
            selection = Cpc41SelectionStatus.UNREADABLE
        elif entry.status is SourceAccountStatus.UNMAPPED:
            selection = Cpc41SelectionStatus.NOT_SELECTED
        elif entry.status is SourceAccountStatus.ABSENT:
            selection = (
                Cpc41SelectionStatus.AMBIGUOUS
                if entry.found and entry.blocker in _CPC41_CLASS_REASONS
                else Cpc41SelectionStatus.ABSENT
            )
        else:
            selection = Cpc41SelectionStatus.NOT_SELECTED
        basis = "diluted" if diluted else "basic"
        if entry.found:
            accounts.extend(
                Cpc41AccountEvidence(
                    module=entry.statement,
                    code=ref.code,
                    name=ref.name,
                    selection_status=selection,
                    value=ref.value,
                    basis=basis,
                )
                for ref in entry.found
            )
            continue

        # Keep an absent disclosure auditable too.  There is no raw row to copy,
        # so materialize the expected CVM identity from the generic source
        # contract instead of returning an unqualified ``raw_ref none``.
        expected_codes = tuple(
            item.removeprefix("code=").strip()
            for item in entry.expected
            if item.startswith("code=")
        ) or (f"3.99.{'02' if diluted else '01'}.*",)
        expected_name = (
            "; ".join(item for item in entry.expected if not item.startswith("code="))
            or "class label required"
        )
        accounts.extend(
            Cpc41AccountEvidence(
                module=entry.statement,
                code=code,
                name=expected_name,
                selection_status=selection,
                value=None,
                basis=basis,
                expected=True,
            )
            for code in expected_codes
        )
    return tuple(accounts)


def _cpc41_base_eps(period: StandardizedFinancials, *, diluted: bool) -> Decimal | None:
    """Read one reconciled base EPS without touching market share counts."""
    disclosure = period.cpc41
    if disclosure is None:
        return None
    return disclosure.diluted_base_eps if diluted else disclosure.basic_base_eps


def _cpc41_multiplier_mismatch(
    periods: Sequence[StandardizedFinancials],
    refs: Sequence[date],
    *,
    diluted: bool,
) -> bool:
    """Detect an inconsistent unit basis across a selected strict window."""
    by_date = {period.reference_date: period for period in periods}
    multipliers: list[Decimal] = []
    for ref in refs:
        period = by_date.get(ref)
        if period is None:
            return False
        disclosure = period.cpc41
        if (
            disclosure is None
            or _cpc41_base_eps(period, diluted=diluted) is None
            or disclosure.security_multiplier is None
            or disclosure.security_multiplier <= 0
        ):
            return False
        multipliers.append(disclosure.security_multiplier)
    return len(set(multipliers)) > 1


def _cpc41_disclosure_status(
    period: StandardizedFinancials, *, diluted: bool
) -> Cpc41EvidenceStatus:
    """Classify whether one basic or diluted CPC 41 result is available."""
    if _cpc41_base_eps(period, diluted=diluted) is not None:
        return Cpc41EvidenceStatus.AVAILABLE
    entry = _cpc41_source_entry(period, diluted=diluted)
    if (
        entry is not None
        and entry.found
        or (period.eps_diluted_null_reason if diluted else period.eps_basic_null_reason)
        in _CPC41_CLASS_REASONS
    ):
        return Cpc41EvidenceStatus.AMBIGUOUS
    return Cpc41EvidenceStatus.ABSENT


def _cpc41_class_status(
    period: StandardizedFinancials, *, diluted: bool
) -> Cpc41EvidenceStatus:
    """Classify whether every required economic class was reconciled."""
    if _cpc41_base_eps(period, diluted=diluted) is not None:
        return Cpc41EvidenceStatus.AVAILABLE
    entry = _cpc41_source_entry(period, diluted=diluted)
    explicit = (
        period.eps_diluted_null_reason if diluted else period.eps_basic_null_reason
    )
    if (
        entry is not None
        and (
            entry.blocker in _CPC41_CLASS_REASONS
            or (entry.found and entry.status is SourceAccountStatus.MAPPED)
        )
        or explicit in _CPC41_CLASS_REASONS
    ):
        return Cpc41EvidenceStatus.AMBIGUOUS
    return Cpc41EvidenceStatus.ABSENT


def _cpc41_multiplier_status(
    period: StandardizedFinancials,
) -> Cpc41EvidenceStatus:
    """Classify the unit multiplier independently of the weighted denominator."""
    disclosure = period.cpc41
    if disclosure is None:
        entries = (
            _cpc41_source_entry(period, diluted=False),
            _cpc41_source_entry(period, diluted=True),
        )
        return (
            Cpc41EvidenceStatus.AMBIGUOUS
            if any(entry is not None and entry.found for entry in entries)
            else Cpc41EvidenceStatus.ABSENT
        )
    return (
        Cpc41EvidenceStatus.AVAILABLE
        if disclosure.security_multiplier is not None
        and disclosure.security_multiplier > 0
        else Cpc41EvidenceStatus.AMBIGUOUS
    )


def _cpc41_aggregate_status(
    basic: Cpc41EvidenceStatus,
    diluted: Cpc41EvidenceStatus,
) -> Cpc41EvidenceStatus:
    """Retain the legacy aggregate field without hiding basis differences."""
    return basic if basic is diluted else Cpc41EvidenceStatus.AMBIGUOUS


def _cpc41_weighted_status(
    period: StandardizedFinancials,
    candidate: _WeightedPeriod | None,
    *,
    diluted: bool,
) -> Cpc41EvidenceStatus:
    """Classify the denominator recovered from one selected period."""
    if candidate is not None:
        return Cpc41EvidenceStatus.AVAILABLE
    reason = _cpc41_period_reason(period, diluted=diluted)
    if reason in _CPC41_CLASS_REASONS:
        return Cpc41EvidenceStatus.AMBIGUOUS
    if (
        period.cpc41 is not None
        and _cpc41_base_eps(period, diluted=diluted) is not None
    ):
        # Filed compatible data that cannot produce a positive denominator is
        # present but not formula-compatible for this selected window.
        return Cpc41EvidenceStatus.AMBIGUOUS
    return Cpc41EvidenceStatus.ABSENT


def _weighted_average_shares(
    candidate: _WeightedPeriod | None,
) -> Decimal | None:
    """Recover the weighted shares represented by an isolated candidate."""
    if candidate is None or candidate.days <= 0:
        return None
    return candidate.share_days / Decimal(candidate.days)


def _cpc41_window_provenance(
    periods: Sequence[StandardizedFinancials],
    weighted_basic: dict[date, _WeightedPeriod | None],
    weighted_diluted: dict[date, _WeightedPeriod | None],
    refs: Sequence[date],
    *,
    basic_blocker: NullReason | None,
    diluted_blocker: NullReason | None,
) -> Cpc41WindowProvenance:
    """Build the complete selected-window audit, oldest period first."""
    by_date = {period.reference_date: period for period in periods}
    basic_multiplier_mismatch = _cpc41_multiplier_mismatch(periods, refs, diluted=False)
    diluted_multiplier_mismatch = _cpc41_multiplier_mismatch(
        periods, refs, diluted=True
    )
    selected: list[Cpc41PeriodProvenance] = []
    for ref in sorted(refs):
        period = by_date.get(ref)
        if period is None:
            selected.append(
                Cpc41PeriodProvenance(
                    reference_date=ref,
                    disclosure_status=Cpc41EvidenceStatus.ABSENT,
                    class_status=Cpc41EvidenceStatus.ABSENT,
                    multiplier_status=Cpc41EvidenceStatus.ABSENT,
                    basic_blocker=NullReason.MISSING_CPC41_DISCLOSURE,
                    diluted_blocker=NullReason.MISSING_CPC41_DISCLOSURE,
                    basic_disclosure_status=Cpc41EvidenceStatus.ABSENT,
                    diluted_disclosure_status=Cpc41EvidenceStatus.ABSENT,
                    basic_class_status=Cpc41EvidenceStatus.ABSENT,
                    diluted_class_status=Cpc41EvidenceStatus.ABSENT,
                    basic_multiplier_status=Cpc41EvidenceStatus.ABSENT,
                    diluted_multiplier_status=Cpc41EvidenceStatus.ABSENT,
                )
            )
            continue

        basic_candidate = weighted_basic.get(ref)
        diluted_candidate = weighted_diluted.get(ref)
        basic_disclosure_status = _cpc41_disclosure_status(period, diluted=False)
        diluted_disclosure_status = _cpc41_disclosure_status(period, diluted=True)
        basic_class_status = _cpc41_class_status(period, diluted=False)
        diluted_class_status = _cpc41_class_status(period, diluted=True)
        multiplier_status = _cpc41_multiplier_status(period)
        basic_multiplier_status = (
            Cpc41EvidenceStatus.AMBIGUOUS
            if basic_multiplier_mismatch
            else multiplier_status
        )
        diluted_multiplier_status = (
            Cpc41EvidenceStatus.AMBIGUOUS
            if diluted_multiplier_mismatch
            else multiplier_status
        )
        multiplier_status = _cpc41_aggregate_status(
            basic_multiplier_status, diluted_multiplier_status
        )
        basic_blocker_for_period = (
            NullReason.MISSING_UNIT_COMPOSITION
            if basic_candidate is None and basic_multiplier_mismatch
            else (
                _cpc41_period_reason(period, diluted=False)
                if basic_candidate is None
                else None
            )
        )
        diluted_blocker_for_period = (
            NullReason.MISSING_UNIT_COMPOSITION
            if diluted_candidate is None and diluted_multiplier_mismatch
            else (
                _cpc41_period_reason(period, diluted=True)
                if diluted_candidate is None
                else None
            )
        )
        selected.append(
            Cpc41PeriodProvenance(
                reference_date=ref,
                disclosure_status=_cpc41_aggregate_status(
                    basic_disclosure_status, diluted_disclosure_status
                ),
                class_status=_cpc41_aggregate_status(
                    basic_class_status, diluted_class_status
                ),
                multiplier_status=multiplier_status,
                multiplier=(
                    None if period.cpc41 is None else period.cpc41.security_multiplier
                ),
                basic_weighted_shares=_weighted_average_shares(basic_candidate),
                basic_weighted_shares_status=_cpc41_weighted_status(
                    period, basic_candidate, diluted=False
                ),
                diluted_weighted_shares=_weighted_average_shares(diluted_candidate),
                diluted_weighted_shares_status=_cpc41_weighted_status(
                    period, diluted_candidate, diluted=True
                ),
                basic_blocker=basic_blocker_for_period,
                diluted_blocker=diluted_blocker_for_period,
                source_accounts=_cpc41_accounts(period),
                basic_disclosure_status=basic_disclosure_status,
                diluted_disclosure_status=diluted_disclosure_status,
                basic_class_status=basic_class_status,
                diluted_class_status=diluted_class_status,
                basic_multiplier_status=basic_multiplier_status,
                diluted_multiplier_status=diluted_multiplier_status,
            )
        )
    return Cpc41WindowProvenance(
        selected_periods=tuple(selected),
        basic_blocker=basic_blocker,
        diluted_blocker=diluted_blocker,
    )


def _merge_source_account_evidence(
    base: tuple[SourceAccountEvidence, ...],
    overrides: tuple[SourceAccountEvidence, ...],
) -> tuple[SourceAccountEvidence, ...]:
    """Merge lineage by field, preserving base order and appending new fields."""
    merged = {entry.field: entry for entry in base}
    for entry in overrides:
        merged[entry.field] = entry
    return tuple(merged.values())


def _ttm_dep_amort_sources(
    periods: Sequence[StandardizedFinancials],
    annual: StandardizedFinancials | None,
    refs: Sequence[date],
    value: Decimal | None,
) -> tuple[SourceAccountEvidence, ...]:
    """Keep every cumulative source needed by a TTM that uses DVA D&A."""
    ends = {
        year: max(ref for ref in refs if ref.year == year)
        for year in {ref.year for ref in refs}
    }
    supporting = [
        period
        for period in periods
        if period.reference_date.year in ends
        and period.reference_date <= ends[period.reference_date.year]
    ]
    if annual is not None and annual.reference_date in refs:
        supporting.append(annual)
    entries: list[SourceAccountEvidence] = []
    for period in sorted(supporting, key=lambda item: item.reference_date):
        source = next(
            (
                item
                for item in period.source_account_evidence
                if item.field == "dep_amort"
            ),
            None,
        )
        if source is None:
            continue
        entries.append(
            replace(
                source,
                field=f"dep_amort[{period.reference_date.isoformat()}]",
                expected=(
                    f"period_end={period.reference_date}",
                    f"period_start={period.dfc_period_start}",
                    f"resolved_value={period.dep_amort}",
                    *source.expected,
                ),
            )
        )
    if not any(entry.statement == "DVA" for entry in entries):
        return ()
    root = SourceAccountEvidence(
        field="dep_amort",
        statement="derived",
        status=SourceAccountStatus.DERIVED,
        expected=tuple(f"selected_quarter={ref}" for ref in sorted(refs)),
        formula="sum(isolate_on_dfc_span(dep_amort)); Q4 = annual - Q1 - Q2 - Q3",
        dependencies=tuple(entry.field for entry in entries),
        blocker=NullReason.SOURCE_ACCOUNT_ABSENT if value is None else None,
        consumer_indicators=entries[0].consumer_indicators,
    )
    return (root, *entries)


def _distribution_source(
    period: StandardizedFinancials, field: str
) -> tuple[SourceAccountEvidence, dict[str, str]] | None:
    source = next((s for s in period.source_account_evidence if s.field == field), None)
    if source is None:
        return None
    facts = dict(entry.split("=", 1) for entry in source.expected if "=" in entry)
    start = period.dfc_period_start
    statement = "DFC"
    value = getattr(period, field)
    if (
        period.cd_cvm is None
        or period.filed_regime is None
        or start != date(period.reference_date.year, 1, 1)
        or source.statement != statement
        or facts.get("cvm_code") != period.cd_cvm
        or facts.get("period_start_date") != str(start)
        or facts.get("period_end_date") != str(period.reference_date)
        or facts.get("reference_date") != str(period.reference_date)
        or facts.get("balance_type") not in {"individual", "consolidated"}
        or not facts.get("currency")
        or facts.get("currency_size") not in {"1", "1000"}
        or value is None
        or not value.is_finite()
        or value < 0
    ):
        return None
    return source, facts


def _ttm_distribution_from_cumulative(
    quarters: Sequence[StandardizedFinancials],
    annual: StandardizedFinancials | None,
    end: date,
    field: str,
) -> tuple[Decimal, tuple[SourceAccountEvidence, ...]] | None:
    """Resolve the same TTM distribution from complete filed cumulative spans."""
    if (
        annual is None
        or annual.reference_date.month != 12
        or annual.reference_date.day != 31
    ):
        return None
    selected: tuple[StandardizedFinancials, ...]
    if annual.reference_date == end:
        selected = (annual,)
        formula = "annual distribution for the identical twelve-month window"
    else:
        current = [q for q in quarters if q.reference_date == end]
        previous = [q for q in quarters if q.reference_date == _year_before(end)]
        if (
            annual.reference_date != date(end.year - 1, 12, 31)
            or len(current) != 1
            or len(previous) != 1
        ):
            return None
        selected = (annual, previous[0], current[0])
        formula = "prior annual - prior same-period YTD + current YTD"
    proof = [_distribution_source(period, field) for period in selected]
    if any(item is None for item in proof):
        return None
    sources = [item for item in proof if item is not None]
    if (
        len({period.cd_cvm for period in selected}) != 1
        or len({period.filed_regime for period in selected}) != 1
        or any(
            len({facts[key] for _, facts in sources}) != 1
            for key in ("balance_type", "currency")
        )
    ):
        return None
    values = [cast(Decimal, getattr(period, field)) for period in selected]
    value = values[0] if len(values) == 1 else values[0] - values[1] + values[2]
    if value < 0 or (len(values) == 3 and values[1] > values[0]):
        return None
    lineage = tuple(
        replace(
            source,
            field=f"{field}[{period.reference_date}]",
            expected=(f"resolved_value={getattr(period, field)}", *source.expected),
        )
        for period, (source, _) in zip(selected, sources, strict=True)
    )
    root = SourceAccountEvidence(
        field=field,
        statement="derived",
        status=SourceAccountStatus.DERIVED,
        expected=(f"period_end={end}", f"resolved_value={value}"),
        formula=formula,
        dependencies=tuple(source.field for source in lineage),
        consumer_indicators=sources[0][0].consumer_indicators,
    )
    return value, (root, *lineage)


def _ttm_isolated_distribution_sources(
    quarters: Sequence[StandardizedFinancials],
    annual: StandardizedFinancials | None,
    refs: Sequence[date],
    field: str,
    value: Decimal | None,
) -> tuple[SourceAccountEvidence, ...]:
    ends = {
        year: max(ref for ref in refs if ref.year == year)
        for year in {r.year for r in refs}
    }
    supporting = [
        q
        for q in quarters
        if q.reference_date.year in ends
        and q.reference_date <= ends[q.reference_date.year]
    ]
    if annual is not None and annual.reference_date in refs:
        supporting.append(annual)
    lineage: list[SourceAccountEvidence] = []
    clock = "dfc_period_start"
    for period in sorted(supporting, key=lambda p: p.reference_date):
        source = next(
            (s for s in period.source_account_evidence if s.field == field), None
        )
        if source is not None:
            lineage.append(
                replace(
                    source,
                    field=f"{field}[{period.reference_date}]",
                    expected=(
                        f"period_end={period.reference_date}",
                        f"period_start={getattr(period, clock)}",
                        f"resolved_value={getattr(period, field)}",
                        *source.expected,
                    ),
                )
            )
    if not lineage:
        return ()
    root = SourceAccountEvidence(
        field=field,
        statement="derived",
        status=SourceAccountStatus.DERIVED,
        expected=tuple(f"selected_quarter={ref}" for ref in sorted(refs)),
        formula=f"sum(isolate_on_{clock}({field})); Q4 = annual - prior YTD",
        dependencies=tuple(s.field for s in lineage),
        blocker=NullReason.SOURCE_ACCOUNT_ABSENT if value is None else None,
        consumer_indicators=lineage[0].consumer_indicators,
    )
    return (root, *lineage)


def _isolate_year(
    periods: list[StandardizedFinancials],
) -> tuple[dict[date, Flows], Flows]:
    """Isolate each quarter of one fiscal year (oldest→newest).

    Returns the per-quarter isolated flows and the year's running 9-month
    cumulative (Σ of the isolated quarters so far), used to derive Q4 later.
    """
    isolated: dict[date, Flows] = {}
    running: Flows = dict.fromkeys(_FLOW_FIELDS, Decimal(0))
    for period in periods:
        dre_span = _months(period.period_start, period.reference_date)
        dfc_span = _months(period.dfc_period_start, period.reference_date)
        flows: Flows = {}
        for name in _FLOW_FIELDS:
            if name in _DFC_FLOW_FIELDS:
                span = dfc_span
            else:
                span = dre_span
            value = getattr(period, name)
            if span is not None and span > _ISOLATED_SPAN_MONTHS:
                # Year-to-date: isolate against the running cumulative, then
                # advance the cumulative to this quarter's YTD figure.
                flows[name] = _sub(value, running[name])
                running[name] = value
            else:
                # Already isolated: accumulate it into the running total.
                flows[name] = value
                running[name] = _add(running[name], value)
        isolated[period.reference_date] = flows
    return isolated, running


def build_ttm(
    quarters: list[StandardizedFinancials],
    annual: StandardizedFinancials | None,
) -> StandardizedFinancials | None:
    """Assemble one TTM ``StandardizedFinancials`` from ITR quarters + annual DFP.

    Returns ``None`` when fewer than four isolated quarters can be assembled (the
    window would not span 12 months), so the caller degrades instead of lying.
    """
    return _build_ttm(quarters, annual)


def build_ttm_as_of(
    quarters: list[StandardizedFinancials],
    annuals: list[StandardizedFinancials],
    end: date,
) -> StandardizedFinancials | None:
    """Assemble the exact trailing window ending on ``end``.

    This is the comparable-period primitive used by TTM growth. It deliberately
    refuses an older substitute when one of the four required quarters is
    absent: four observations spread over more than twelve months are not a TTM.
    The latest annual available by ``end`` remains eligible to derive its Q4.
    """
    eligible_quarters = [q for q in quarters if q.reference_date <= end]
    eligible_annuals = [a for a in annuals if a.reference_date <= end]
    annual = (
        max(eligible_annuals, key=lambda item: item.reference_date)
        if eligible_annuals
        else None
    )
    return _build_ttm(eligible_quarters, annual, required_end=end)


def _year_before(value: date) -> date:
    """The same fiscal date one year earlier, including a leap-day fallback."""
    try:
        return value.replace(year=value.year - 1)
    except ValueError:
        return value.replace(year=value.year - 1, day=28)


def _build_ttm(
    quarters: list[StandardizedFinancials],
    annual: StandardizedFinancials | None,
    *,
    required_end: date | None = None,
) -> StandardizedFinancials | None:
    if not quarters:
        return None

    by_year: dict[int, list[StandardizedFinancials]] = {}
    for quarter in quarters:
        by_year.setdefault(quarter.reference_date.year, []).append(quarter)

    isolated: dict[date, Flows] = {}
    year_cumulative: dict[int, tuple[Flows, int]] = {}
    for year, periods in by_year.items():
        ordered = sorted(periods, key=lambda p: p.reference_date)
        year_isolated, running = _isolate_year(ordered)
        isolated.update(year_isolated)
        year_cumulative[year] = (running, len(ordered))

    # Derive the isolated Q4 for the annual's year: annual − 9-month cumulative.
    if annual is not None:
        cumulative = year_cumulative.get(annual.reference_date.year)
        if cumulative is not None and cumulative[1] == 3:
            running = cumulative[0]
            isolated[annual.reference_date] = {
                name: _sub(getattr(annual, name), running[name])
                for name in _FLOW_FIELDS
            }

    refs = sorted(isolated, reverse=True)[:_TTM_QUARTERS]
    if len(refs) < _TTM_QUARTERS:
        return None
    if not _quarters_are_contiguous(refs):
        return None
    if required_end is not None and (
        refs[0] != required_end or refs[-1] <= _year_before(required_end)
    ):
        return None

    # Legacy basic-EPS reconstruction is retained as filing diagnostics and to
    # keep the diluted evidence path reviewable. It no longer selects the public
    # basic EPS: compute() divides net_income by the selected closing shares.
    weighted_basic = _weighted_isolated(quarters, annual, diluted=False)
    weighted_diluted = _weighted_isolated(quarters, annual, diluted=True)
    eps_basic = _ttm_weighted_eps(weighted_basic, refs)
    eps_diluted = _ttm_weighted_eps(weighted_diluted, refs)
    # Keep provenance in lockstep with the arithmetic source. The annual is a
    # selected period only when its derived Q4 is in this exact TTM window.
    cpc41_periods = list(quarters)
    if annual is not None and annual.reference_date in refs:
        cpc41_periods.append(annual)
    eps_basic_reason = (
        None
        if eps_basic is not None
        else _cpc41_ttm_null_reason(cpc41_periods, weighted_basic, refs, diluted=False)
    )
    eps_diluted_reason = (
        None
        if eps_diluted is not None
        else _cpc41_ttm_null_reason(cpc41_periods, weighted_diluted, refs, diluted=True)
    )
    cpc41_provenance: Cpc41WindowProvenance | None = _cpc41_window_provenance(
        cpc41_periods,
        weighted_basic,
        weighted_diluted,
        refs,
        basic_blocker=eps_basic_reason,
        diluted_blocker=eps_diluted_reason,
    )

    summed: Flows = {}
    for name in _FLOW_FIELDS:
        values = [isolated[ref][name] for ref in refs]
        present = [v for v in values if v is not None]
        # A TTM flow needs all four quarters; a gap makes it null, not understated.
        summed[name] = sum(present, Decimal(0)) if len(present) == len(values) else None

    distribution_sources: list[SourceAccountEvidence] = []
    for field in ("dividends_paid",):
        resolved = _ttm_distribution_from_cumulative(quarters, annual, refs[0], field)
        if resolved is not None:
            summed[field] = resolved[0]
            distribution_sources.extend(resolved[1])
        else:
            distribution_sources.extend(
                _ttm_isolated_distribution_sources(
                    quarters, annual, refs, field, summed[field]
                )
            )

    # The annual disclosure already resolves the weighted denominator for
    # this exact twelve-month span, including distinct class and dilution terms.
    # Use it directly when its attributable profit matches the assembled flow.
    annual_eps_fields: list[str] = []
    if _same_annual_eps_window(annual, refs, summed["net_income"], quarters):
        assert annual is not None
        if annual.eps_basic is not None and annual.eps_basic.is_finite():
            eps_basic = annual.eps_basic
            eps_basic_reason = None
            annual_eps_fields.append("eps_basic")
        if annual.eps_diluted is not None and annual.eps_diluted.is_finite():
            eps_diluted = annual.eps_diluted
            eps_diluted_reason = None
            annual_eps_fields.append("eps_diluted")
        if (
            annual_eps_fields
            and (eps_basic is None or "eps_basic" in annual_eps_fields)
            and (eps_diluted is None or "eps_diluted" in annual_eps_fields)
        ):
            # Quarter denominator diagnostics were not selected by this result.
            cpc41_provenance = None

    # Stocks come from the most recent balance sheet — the latest ITR quarter, or
    # the annual DFP when no newer quarter exists (window ends on the closed year).
    latest = max(quarters, key=lambda p: p.reference_date)
    stock_source = (
        annual
        if annual is not None and annual.reference_date > latest.reference_date
        else latest
    )
    source_account_evidence = _merge_source_account_evidence(
        latest.source_account_evidence, tuple(distribution_sources)
    )
    if annual_eps_fields:
        assert annual is not None
        source_account_evidence = _merge_source_account_evidence(
            source_account_evidence, _annual_eps_sources(annual, annual_eps_fields)
        )
    dep_amort_sources = _ttm_dep_amort_sources(
        quarters, annual, refs, summed["dep_amort"]
    )
    if dep_amort_sources:
        source_account_evidence = _merge_source_account_evidence(
            source_account_evidence, dep_amort_sources
        )
    for field in ("cfo", "capex"):
        source_account_evidence = _merge_source_account_evidence(
            source_account_evidence,
            _ttm_isolated_distribution_sources(
                quarters, annual, refs, field, summed[field]
            ),
        )
    source_account_evidence = _merge_source_account_evidence(
        source_account_evidence,
        tuple(
            s
            for s in stock_source.source_account_evidence
            if s.field in {"inventories", "free_float", "tag_along"}
        ),
    )

    end = stock_source.reference_date
    start_index = end.year * 12 + (end.month - 1) - 11
    period_start = date(start_index // 12, start_index % 12 + 1, 1)
    return StandardizedFinancials(
        reference_date=end,
        sector=latest.sector,
        period_start=period_start,
        dfc_period_start=period_start,  # the TTM flows are already isolated+summed
        total_assets=stock_source.total_assets,
        equity=stock_source.equity,
        equity_total=stock_source.equity_total,
        net_income=summed["net_income"],
        net_income_total=summed["net_income_total"],
        # Select the filed result for an identical annual span, otherwise use
        # the reconciled share-day denominator. Missing evidence stays null.
        eps_basic=eps_basic,
        eps_diluted=eps_diluted,
        eps_basic_null_reason=eps_basic_reason,
        eps_diluted_null_reason=eps_diluted_reason,
        revenue=summed["revenue"],
        gross_profit=summed["gross_profit"],
        ebit=summed["ebit"],
        ebitda=_add(summed["ebit"], summed["dep_amort"]),
        dep_amort=summed["dep_amort"],
        cash_equivalents=stock_source.cash_equivalents,
        current_financial_investments=(stock_source.current_financial_investments),
        current_assets=stock_source.current_assets,
        inventories=stock_source.inventories,
        free_float=stock_source.free_float,
        tag_along=stock_source.tag_along,
        tag_along_null_reason=stock_source.tag_along_null_reason,
        current_liabilities=stock_source.current_liabilities,
        total_debt=stock_source.total_debt,
        debt_coverage_null_reason=stock_source.debt_coverage_null_reason,
        debt_evidence=stock_source.debt_evidence,
        issuer_name=stock_source.issuer_name,
        cd_cvm=stock_source.cd_cvm,
        cnpj=stock_source.cnpj,
        dividends_paid=summed["dividends_paid"],
        cfo=summed["cfo"],
        capex=summed["capex"],
        loan_loss_provision=summed["loan_loss_provision"],
        fee_income=summed["fee_income"],
        personnel_expense=summed["personnel_expense"],
        admin_expense=summed["admin_expense"],
        loan_book=stock_source.loan_book,  # a balance, like the other stocks
        # Null-cause provenance (#30) travels with the window: same filer, same
        # regime and same deliberately-skipped fields as its quarters.
        filed_regime=stock_source.filed_regime,
        # Preserve explicitly evidenced inputs; the outer window resolver
        # separately assembles complete CVM roots and compatible stock pairs.
        unmapped_fields=latest.unmapped_fields,
        source_account_evidence=source_account_evidence,
        cpc41_window_provenance=cpc41_provenance,
    )
