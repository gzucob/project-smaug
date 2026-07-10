"""Pure indicator calculator (Phase 2 domain).

No I/O, no framework — just arithmetic over ``StandardizedFinancials`` and
``MarketData``. Two deliberate choices:

* **Annualization.** CVM ITR figures are year-to-date (Q1 = 3 months, Q2 = 6,
  Q3 = 9, annual = 12). Ratios that put a *flow* (result) over a *stock*
  (equity, assets) or over price — ROE, ROA, P/E, EV/EBITDA — annualize the flow
  first so the number is comparable to an annual figure. Pure period ratios
  (margins, growth vs. the same prior period) are left as-is.
* **Sector awareness.** Banks and insurers file under a different structure, so
  the financial-regime guards below null the indicators that structure omits.
  *Which* of those nulls are genuinely inapplicable versus merely unmapped
  (pending #48's bank/insurer line-item mapping) is decided per regime in the
  null-reason attribution — see ``_INAPPLICABLE_BY_REGIME`` and ADR 0010.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal

from smaug.analysis.domain.financials import (
    AccountingRegime,
    MarketData,
    StandardizedFinancials,
    expected_regime,
)
from smaug.analysis.domain.indicators import Indicators, NullReason
from smaug.portfolio.domain.sectors import Sector

_MONTHS_IN_YEAR = Decimal(12)
# Statutory Brazilian corporate rate (IRPJ 25% + CSLL 9%). ROIC's NOPAT uses this
# flat rate rather than each company's effective rate — a deliberate approximation
# (see docs/adr/0002-*), matching how the reference platforms simplify.
_TAX_RATE = Decimal("0.34")


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


def _sub(a: Decimal | None, b: Decimal | None) -> Decimal | None:
    return None if a is None or b is None else a - b


def _add(a: Decimal | None, b: Decimal | None) -> Decimal | None:
    return None if a is None or b is None else a + b


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


# Indicators genuinely meaningless under a given accounting regime: the null is
# an inapplicable-regime null regardless of inputs (#30). Audited per regime
# against AUVP Analítica + Investidor10 (ADR 0010): a bank reports capital
# adequacy (Índice de Basileia), not net debt / EV-EBITDA, and has no EBITDA; an
# insurer's margins are degenerate (both platforms show 0%). Every *other*
# indicator a financial filer still nulls is merely unmapped — its inputs sit in
# ``unmapped_fields`` pending #48 — not inapplicable. Drawing that line per
# regime is the whole point of #30.
_INAPPLICABLE_BY_REGIME: dict[AccountingRegime, frozenset[str]] = {
    AccountingRegime.BANK: frozenset(
        {
            "ebitda_margin",
            "net_debt",
            "net_debt_to_ebitda",
            "debt_to_equity",
            "ev_ebitda",
        }
    ),
    AccountingRegime.INSURANCE: frozenset(
        {
            "gross_margin",
            "ebit_margin",
            "ebitda_margin",
        }
    ),
}


def _inapplicable(sector: Sector) -> frozenset[str]:
    """Indicators inapplicable under ``sector``'s expected accounting regime (#30)."""
    return _INAPPLICABLE_BY_REGIME.get(expected_regime(sector), frozenset())


@dataclass(frozen=True)
class _Needs:
    """The inputs one indicator needs, for attributing its null (#30).

    ``accounts`` are ``StandardizedFinancials`` field names; ``cap``/``shares``
    are the market-side inputs; ``prior`` is the prior-period field a growth
    ratio compares against.
    """

    accounts: tuple[str, ...] = ()
    cap: bool = False
    shares: bool = False
    prior: str | None = None


_NEEDS: dict[str, _Needs] = {
    "roe": _Needs(accounts=("net_income", "equity")),
    "roa": _Needs(accounts=("net_income", "total_assets")),
    "roic": _Needs(accounts=("ebit", "equity", "total_debt")),
    "net_margin": _Needs(accounts=("net_income", "revenue")),
    "gross_margin": _Needs(accounts=("gross_profit", "revenue")),
    "ebit_margin": _Needs(accounts=("ebit", "revenue")),
    "ebitda_margin": _Needs(accounts=("ebitda", "revenue")),
    "asset_turnover": _Needs(accounts=("revenue", "total_assets")),
    "eps": _Needs(accounts=("net_income",), shares=True),
    "bvps": _Needs(accounts=("equity",), shares=True),
    "net_debt": _Needs(accounts=("total_debt",)),
    "net_debt_to_ebitda": _Needs(accounts=("total_debt", "ebitda")),
    "debt_to_equity": _Needs(accounts=("total_debt", "equity")),
    "liabilities_to_assets": _Needs(accounts=("total_assets", "equity")),
    "current_ratio": _Needs(accounts=("current_assets", "current_liabilities")),
    "revenue_growth": _Needs(accounts=("revenue",), prior="revenue"),
    "net_income_growth": _Needs(accounts=("net_income",), prior="net_income"),
    "pe": _Needs(accounts=("net_income",), cap=True),
    "pb": _Needs(accounts=("equity",), cap=True),
    "psr": _Needs(accounts=("revenue",), cap=True),
    "price_to_assets": _Needs(accounts=("total_assets",), cap=True),
    "price_to_ebit": _Needs(accounts=("ebit",), cap=True),
    "price_to_working_capital": _Needs(
        accounts=("current_assets", "current_liabilities"), cap=True
    ),
    "payout": _Needs(accounts=("dividends_paid", "net_income")),
    "dividend_yield": _Needs(accounts=("dividends_paid",), cap=True),
    "ev_ebitda": _Needs(accounts=("total_debt", "ebitda"), cap=True),
    "fcf": _Needs(accounts=("cfo", "capex")),
    "price_to_fcf": _Needs(accounts=("cfo", "capex"), cap=True),
    "fcf_yield": _Needs(accounts=("cfo", "capex"), cap=True),
    "revenue": _Needs(accounts=("revenue",)),
    "net_income": _Needs(accounts=("net_income",)),
    "dividends": _Needs(accounts=("dividends_paid",)),
}


def _classify(
    name: str,
    needs: _Needs,
    f: StandardizedFinancials,
    previous: StandardizedFinancials | None,
    market: MarketData,
    *,
    inapplicable: frozenset[str],
    mismatch: bool,
) -> NullReason | None:
    """Attribute one null indicator to a cause, most-upstream cause first.

    Precedence: the regime's inapplicable set (the null exists regardless of
    inputs), then the accounting inputs (unmapped beats absent, and a regime
    mismatch overrides both — the mismatch is why the input was never read),
    then the market-side inputs, then the prior period. ``None`` = unclassified
    (e.g. a zero denominator).
    """
    if name in inapplicable:
        return (
            NullReason.UNEXPECTED_REGIME if mismatch else NullReason.INAPPLICABLE_REGIME
        )
    for account in needs.accounts:
        if getattr(f, account) is None:
            if account in f.unmapped_fields:
                return (
                    NullReason.UNEXPECTED_REGIME
                    if mismatch
                    else NullReason.SOURCE_ACCOUNT_UNMAPPED
                )
            return NullReason.SOURCE_ACCOUNT_ABSENT
    if needs.cap and market.market_cap is None:
        # cap = price × shares (closed year, ADR 0012), so a null cap blames the
        # price unless the price is present and only the share count is missing.
        if market.price is not None and market.shares is None:
            return NullReason.MISSING_SHARE_COUNT
        return NullReason.MISSING_PRICE
    if needs.shares and market.shares is None:
        return NullReason.MISSING_SHARE_COUNT
    if needs.prior is not None and (
        previous is None or getattr(previous, needs.prior) is None
    ):
        return NullReason.MISSING_PRIOR_PERIOD
    return None


def _null_reasons(
    computed: Indicators,
    f: StandardizedFinancials,
    previous: StandardizedFinancials | None,
    market: MarketData,
) -> dict[str, NullReason]:
    """Name the cause of every classifiable null in ``computed`` (#30)."""
    inapplicable = _inapplicable(f.sector)
    mismatch = f.filed_regime is not None and f.filed_regime != expected_regime(
        f.sector
    )
    reasons: dict[str, NullReason] = {}
    for name, needs in _NEEDS.items():
        if getattr(computed, name) is not None:
            continue
        reason = _classify(
            name,
            needs,
            f,
            previous,
            market,
            inapplicable=inapplicable,
            mismatch=mismatch,
        )
        if reason is not None:
            reasons[name] = reason
    return reasons


def compute(
    current: StandardizedFinancials,
    previous: StandardizedFinancials | None,
    market: MarketData,
) -> Indicators:
    """Compute all applicable indicators for one ticker/period.

    Every null field in the result carries its cause in ``null_reasons`` when
    one is classifiable — the reason is attributed here, where the null is
    produced, because only the calculator sees which input broke which ratio.
    """
    f = current
    # Financial filers null the structure-specific indicators below because their
    # inputs are not mapped yet (#48). Which of those nulls are *inapplicable* vs
    # merely *unmapped* is decided per regime in ``_null_reasons`` (ADR 0010) — so
    # the merely-unmapped ones will light up once #48 maps the accounts, without a
    # change here.
    is_financial = f.sector.is_financial
    cap = market.market_cap
    annual_net_income = _annualized(f.net_income, f)
    annual_revenue = _annualized(f.revenue, f)
    annual_ebit = _annualized(f.ebit, f)
    annual_ebitda = _annualized(f.ebitda, f)

    net_debt = None if is_financial else _net_debt(f)
    enterprise_value = None if net_debt is None or cap is None else cap + net_debt
    # Invested capital for ROIC: equity + net financial debt (net of cash).
    invested_capital = None if is_financial else _add(f.equity, net_debt)
    nopat = None if annual_ebit is None else annual_ebit * (1 - _TAX_RATE)
    # Working capital drives the P/working-capital multiple (Graham's basis).
    working_capital = (
        None if is_financial else _sub(f.current_assets, f.current_liabilities)
    )
    # Free cash flow: operating cash flow minus capex, annualized like the other
    # flows so a bare year-to-date period is comparable to a full year.
    annual_fcf = _annualized(_sub(f.cfo, f.capex), f)

    prev_revenue = previous.revenue if previous is not None else None
    prev_net_income = previous.net_income if previous is not None else None

    indicators = Indicators(
        roe=_div(annual_net_income, f.equity),
        roa=_div(annual_net_income, f.total_assets),
        roic=None if is_financial else _div(nopat, invested_capital),
        net_margin=_div(f.net_income, f.revenue),
        gross_margin=None if is_financial else _div(f.gross_profit, f.revenue),
        ebit_margin=None if is_financial else _div(f.ebit, f.revenue),
        ebitda_margin=None if is_financial else _div(f.ebitda, f.revenue),
        asset_turnover=_div(annual_revenue, f.total_assets),
        eps=_div(annual_net_income, market.shares),
        bvps=_div(f.equity, market.shares),
        net_debt=net_debt,
        net_debt_to_ebitda=None if is_financial else _div(net_debt, annual_ebitda),
        debt_to_equity=None if is_financial else _div(f.total_debt, f.equity),
        liabilities_to_assets=_div(_sub(f.total_assets, f.equity), f.total_assets),
        current_ratio=(
            None if is_financial else _div(f.current_assets, f.current_liabilities)
        ),
        revenue_growth=_growth(f.revenue, prev_revenue),
        net_income_growth=_growth(f.net_income, prev_net_income),
        pe=_div(cap, annual_net_income),
        pb=_div(cap, f.equity),
        psr=_div(cap, annual_revenue),
        price_to_assets=_div(cap, f.total_assets),
        price_to_ebit=None if is_financial else _div(cap, annual_ebit),
        price_to_working_capital=None if is_financial else _div(cap, working_capital),
        payout=_div(f.dividends_paid, f.net_income),
        dividend_yield=_div(f.dividends_paid, cap),
        ev_ebitda=None if is_financial else _div(enterprise_value, annual_ebitda),
        fcf=annual_fcf,
        price_to_fcf=_div(cap, annual_fcf),
        fcf_yield=_div(annual_fcf, cap),
        revenue=f.revenue,
        net_income=f.net_income,
        dividends=f.dividends_paid,
    )
    return replace(
        indicators, null_reasons=_null_reasons(indicators, f, previous, market)
    )
