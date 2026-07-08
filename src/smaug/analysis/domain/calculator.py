"""Pure indicator calculator (Phase 2 domain).

No I/O, no framework — just arithmetic over ``StandardizedFinancials`` and
``MarketData``. Two deliberate choices:

* **Annualization.** CVM ITR figures are year-to-date (Q1 = 3 months, Q2 = 6,
  Q3 = 9, annual = 12). Ratios that put a *flow* (result) over a *stock*
  (equity, assets) or over price — ROE, ROA, P/E, EV/EBITDA — annualize the flow
  first so the number is comparable to an annual figure. Pure period ratios
  (margins, growth vs. the same prior period) are left as-is.
* **Sector awareness.** Banks and insurers file under a different structure;
  net debt, current ratio and EV/EBITDA do not apply and return ``None``.
"""

from __future__ import annotations

from decimal import Decimal

from smaug.analysis.domain.financials import MarketData, StandardizedFinancials
from smaug.analysis.domain.indicators import Indicators

_MONTHS_IN_YEAR = Decimal(12)


def _period_months(financials: StandardizedFinancials) -> int:
    """Length of the flow period in months.

    Prefer the explicit ``period_start``..``reference_date`` span (a TTM window and
    a closed year are both 12 → annualization is a no-op). Fall back to the
    reference month for a bare year-to-date ITR (Q3 = 9 months), which is what the
    figure defaults to when no start date was captured.
    """
    start = financials.period_start
    if start is None:
        return financials.reference_date.month
    end = financials.reference_date
    return (end.year - start.year) * 12 + (end.month - start.month) + 1


def _div(numerator: Decimal | None, denominator: Decimal | None) -> Decimal | None:
    if numerator is None or denominator is None or denominator == 0:
        return None
    return numerator / denominator


def _growth(current: Decimal | None, previous: Decimal | None) -> Decimal | None:
    if current is None or previous is None or previous == 0:
        return None
    return (current - previous) / abs(previous)


def _annualized(
    value: Decimal | None, financials: StandardizedFinancials
) -> Decimal | None:
    months = _period_months(financials)
    if value is None or months == 0:
        return None
    return value * _MONTHS_IN_YEAR / Decimal(months)


def _net_debt(financials: StandardizedFinancials) -> Decimal | None:
    if financials.total_debt is None:
        return None
    return financials.total_debt - (financials.cash or Decimal(0))


def compute(
    current: StandardizedFinancials,
    previous: StandardizedFinancials | None,
    market: MarketData,
) -> Indicators:
    """Compute all applicable indicators for one ticker/period."""
    f = current
    is_financial = f.sector.is_financial
    annual_net_income = _annualized(f.net_income, f)
    annual_ebitda = _annualized(f.ebitda, f)

    net_debt = None if is_financial else _net_debt(f)
    enterprise_value = (
        None
        if net_debt is None or market.market_cap is None
        else market.market_cap + net_debt
    )
    prev_revenue = previous.revenue if previous is not None else None
    prev_net_income = previous.net_income if previous is not None else None

    return Indicators(
        roe=_div(annual_net_income, f.equity),
        roa=_div(annual_net_income, f.total_assets),
        net_margin=_div(f.net_income, f.revenue),
        gross_margin=None if is_financial else _div(f.gross_profit, f.revenue),
        ebitda_margin=None if is_financial else _div(f.ebitda, f.revenue),
        net_debt=net_debt,
        net_debt_to_ebitda=None if is_financial else _div(net_debt, annual_ebitda),
        current_ratio=(
            None if is_financial else _div(f.current_assets, f.current_liabilities)
        ),
        revenue_growth=_growth(f.revenue, prev_revenue),
        net_income_growth=_growth(f.net_income, prev_net_income),
        pe=_div(market.market_cap, annual_net_income),
        pb=_div(market.market_cap, f.equity),
        dividend_yield=_div(f.dividends_paid, market.market_cap),
        ev_ebitda=None if is_financial else _div(enterprise_value, annual_ebitda),
    )
