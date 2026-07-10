"""Trailing-twelve-months (TTM) assembly (Phase 2 domain, pure).

Rebuilds a 12-month period from CVM quarters. Two rules the CVM data forces:

* **Flow vs. stock.** Income-statement lines (revenue, net income, EBIT, D&A) are
  *flows*: the TTM value is the **sum** of the four trailing isolated quarters.
  Balance-sheet lines (equity, assets, cash, debt) are *stocks*: the TTM value is
  the **latest** quarter, never a sum.
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

from datetime import date
from decimal import Decimal

from smaug.analysis.domain.financials import StandardizedFinancials

# Flows summed over the window; EBITDA is recomposed from EBIT+D&A. DRE flows are
# isolated on the DRE span, DFC flows (D&A, dividends) on the DFC span — the two
# statements use different period bases in the CVM files.
_DRE_FLOW_FIELDS = ("revenue", "net_income", "ebit", "gross_profit")
_DFC_FLOW_FIELDS = ("dep_amort", "dividends_paid", "cfo", "capex")
_FLOW_FIELDS = _DRE_FLOW_FIELDS + _DFC_FLOW_FIELDS
_TTM_QUARTERS = 4
_ISOLATED_SPAN_MONTHS = 3

Flows = dict[str, Decimal | None]


def _months(start: date | None, end: date) -> int | None:
    if start is None:
        return None
    return (end.year - start.year) * 12 + (end.month - start.month) + 1


def _sub(a: Decimal | None, b: Decimal | None) -> Decimal | None:
    return None if a is None or b is None else a - b


def _add(a: Decimal | None, b: Decimal | None) -> Decimal | None:
    return None if a is None or b is None else a + b


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
            span = dfc_span if name in _DFC_FLOW_FIELDS else dre_span
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

    summed: Flows = {}
    for name in _FLOW_FIELDS:
        values = [isolated[ref][name] for ref in refs]
        present = [v for v in values if v is not None]
        # A TTM flow needs all four quarters; a gap makes it null, not understated.
        summed[name] = sum(present, Decimal(0)) if len(present) == len(values) else None

    # Stocks come from the most recent balance sheet — the latest ITR quarter, or
    # the annual DFP when no newer quarter exists (window ends on the closed year).
    latest = max(quarters, key=lambda p: p.reference_date)
    stock_source = (
        annual
        if annual is not None and annual.reference_date > latest.reference_date
        else latest
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
        net_income=summed["net_income"],
        revenue=summed["revenue"],
        gross_profit=summed["gross_profit"],
        ebit=summed["ebit"],
        ebitda=_add(summed["ebit"], summed["dep_amort"]),
        dep_amort=summed["dep_amort"],
        cash=stock_source.cash,
        current_assets=stock_source.current_assets,
        current_liabilities=stock_source.current_liabilities,
        total_debt=stock_source.total_debt,
        dividends_paid=summed["dividends_paid"],
        cfo=summed["cfo"],
        capex=summed["capex"],
        # Null-cause provenance (#30) travels with the window: same filer, same
        # regime and same deliberately-skipped fields as its quarters.
        filed_regime=latest.filed_regime,
        unmapped_fields=latest.unmapped_fields,
    )
