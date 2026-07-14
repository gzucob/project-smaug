"""Pure indicator calculator (Phase 2 domain).

No I/O, no framework — just arithmetic over ``StandardizedFinancials`` and
``MarketData``. Two deliberate choices:

* **Annualization.** CVM ITR figures are year-to-date (Q1 = 3 months, Q2 = 6,
  Q3 = 9, annual = 12). Ratios that put a *flow* (result) over a *stock*
  (equity, assets) or over price — ROE, ROA, P/E, EV/EBITDA — annualize the flow
  first so the number is comparable to an annual figure. Pure period ratios
  (margins, growth vs. the same prior period) are left as-is.
* **Regime awareness.** Banks and insurers file under a different structure, and
  the mapper reads each regime's own chart of accounts (ADR 0015). What a regime
  genuinely cannot support is named once, in ``_INAPPLICABLE_BY_REGIME``, which
  drives both the suppressed value and its null reason (ADR 0010) — there is no
  second, hand-maintained guard for the calculator to drift away from.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Any

from smaug.analysis.domain.financials import (
    AccountingRegime,
    MarketData,
    StandardizedFinancials,
    expected_regime,
)
from smaug.analysis.domain.indicators import Indicators, NullReason

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


def _negated(value: Decimal | None) -> Decimal | None:
    """Flip a ratio built from a filed expense, which CVM records as negative.

    An efficiency ratio of 34% is a cost, and reporting it as −34% would be a
    faithful reading of the sign and a useless number to look at.
    """
    return None if value is None else -value


def _net_debt(financials: StandardizedFinancials) -> Decimal | None:
    if financials.total_debt is None:
        return None
    return financials.total_debt - (financials.cash or Decimal(0))


# Indicators genuinely meaningless under a given accounting regime: the null is
# an inapplicable-regime null regardless of inputs (#30). Audited per regime
# against AUVP Analítica + Investidor10 (ADR 0010): a bank reports capital
# adequacy (Índice de Basileia), not net debt / EV-EBITDA, and has no EBITDA; an
# insurer's margins are degenerate (both platforms show 0%).
#
# ADR 0015 (#48) closed the three verdicts ADR 0010 had left to the mapping: a
# bank's balance sheet has no current/non-current split whatsoever, so its
# current ratio and P/working-capital are not "not yet mapped" but unbuildable —
# and its ROIC denominator (equity + net debt) inherits the net-debt verdict
# above, since a deposit is funding, not borrowing. Every *other* indicator a
# financial filer nulls now falls through to the input check.
#
# The three bank ratios (ADR 0021) run the other way: they describe a balance sheet
# that *is* the business, and a company that sells goods has no spread, no loan book
# and no payroll-against-spread to report. They are inapplicable to everyone else.
_BANK_ONLY = frozenset({"net_interest_margin", "efficiency_ratio", "cost_of_risk"})

_INAPPLICABLE_BY_REGIME: dict[AccountingRegime, frozenset[str]] = {
    AccountingRegime.BANK: frozenset(
        {
            "ebitda_margin",
            "net_debt",
            "net_debt_to_ebitda",
            "debt_to_equity",
            "ev_ebitda",
            "roic",
            "current_ratio",
            "price_to_working_capital",
        }
    ),
    AccountingRegime.INSURANCE: frozenset(
        {
            "gross_margin",
            "ebit_margin",
            "ebitda_margin",
        }
    )
    | _BANK_ONLY,
    AccountingRegime.CORPORATE: _BANK_ONLY,
}


def _inapplicable(f: StandardizedFinancials) -> frozenset[str]:
    """Indicators the filer's own chart of accounts cannot support (ADR 0020).

    Keyed on the regime the company **files under**, read off the filing itself
    (ADR 0015) — never on the regime its sector predicts. The two differ: CXSE3 is
    an insurer by sector and files as a corporate holding (ADR 0006), and asking
    the sector suppressed three margins its chart of accounts supports perfectly
    well. The sector's expectation is the fallback for a filing whose regime could
    not be detected at all, where there is nothing better to ask.
    """
    regime = f.filed_regime or expected_regime(f.sector)
    return _INAPPLICABLE_BY_REGIME.get(regime, frozenset())


def _suppressed(indicators: Indicators, inapplicable: frozenset[str]) -> Indicators:
    """Null the regime's inapplicable indicators, whatever their inputs say.

    The per-regime map is the single source of truth for both the value and its
    reason. Suppression is what makes it so: an indicator the regime does not
    support must not surface merely because an input turned out to be mappable —
    an insurer's EBIT margin is degenerate (ADR 0010) even now that #48 reads its
    EBIT. Most entries here are already ``None`` from their missing inputs; this
    catches the ones that are not.
    """
    if not inapplicable:
        return indicators
    # ``Any`` because the field names are data, not literals — the whole point is
    # that the regime map, not this function, decides which fields these are.
    nulled: dict[str, Any] = dict.fromkeys(inapplicable)
    return replace(indicators, **nulled)


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
    "net_interest_margin": _Needs(
        accounts=("gross_profit", "loan_loss_provision", "total_assets")
    ),
    "efficiency_ratio": _Needs(
        accounts=(
            "gross_profit",
            "loan_loss_provision",
            "personnel_expense",
            "admin_expense",
            "fee_income",
        )
    ),
    "cost_of_risk": _Needs(accounts=("loan_loss_provision", "loan_book")),
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
) -> NullReason:
    """Attribute one null indicator to a cause, most-upstream cause first.

    Precedence: the filed regime's inapplicable set (the null exists regardless of
    inputs), then the accounting inputs (unmapped beats absent), then the
    market-side inputs, then the prior period. If none fired, every input the
    indicator needs is present, so a still-null ratio is a zero denominator (a zero
    *numerator* yields 0, a value — not a null): the ``ZERO_DENOMINATOR`` dead-end.
    This relies on every ``_Needs`` entry being input-complete for its denominator;
    ``None`` is never returned.
    """
    if name in inapplicable:
        return NullReason.INAPPLICABLE_REGIME
    for account in needs.accounts:
        if getattr(f, account) is None:
            if account in f.unmapped_fields:
                return NullReason.SOURCE_ACCOUNT_UNMAPPED
            return NullReason.SOURCE_ACCOUNT_ABSENT
    if needs.cap and market.market_cap is None:
        # The cap sums the company's share classes (ADR 0014), so which input went
        # missing is not readable from ``price``/``shares`` here — a sibling class
        # can be the one lacking a quote. The use case names it when it builds the
        # cap; the price is the fallback blame when it did not.
        if market.cap_null_reason is not None:
            return market.cap_null_reason
        return NullReason.MISSING_PRICE
    if needs.shares and market.shares is None:
        return NullReason.MISSING_SHARE_COUNT
    if needs.prior is not None and (
        previous is None or getattr(previous, needs.prior) is None
    ):
        return NullReason.MISSING_PRIOR_PERIOD
    return NullReason.ZERO_DENOMINATOR


def _null_reasons(
    computed: Indicators,
    f: StandardizedFinancials,
    previous: StandardizedFinancials | None,
    market: MarketData,
) -> dict[str, NullReason]:
    """Name the cause of every null in ``computed`` (#30).

    Every null now carries a reason — the zero-denominator dead-end is named
    too (ANL-23), so there is no unclassified status left for the nine tickers.
    """
    inapplicable = _inapplicable(f)
    reasons: dict[str, NullReason] = {}
    for name, needs in _NEEDS.items():
        if getattr(computed, name) is not None:
            continue
        reasons[name] = _classify(
            name,
            needs,
            f,
            previous,
            market,
            inapplicable=inapplicable,
        )
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
    # Everything is computed from its inputs and *then* suppressed per regime (#48).
    # The old blanket ``is_financial`` guard is gone: with the mapper reading each
    # regime's own chart of accounts (ADR 0015), the ratios a financial filer does
    # support (margins, P/EBIT, FCF) must compute rather than be blanked by their
    # filer's sector, and the ones it does not are named — once — in
    # ``_INAPPLICABLE_BY_REGIME``, which now drives the value as well as the reason.
    cap = market.market_cap
    annual_net_income = _annualized(f.net_income, f)
    annual_revenue = _annualized(f.revenue, f)
    annual_ebit = _annualized(f.ebit, f)
    annual_ebitda = _annualized(f.ebitda, f)

    net_debt = _net_debt(f)
    enterprise_value = None if net_debt is None or cap is None else cap + net_debt
    # Invested capital for ROIC: equity + net financial debt (net of cash).
    invested_capital = _add(f.equity, net_debt)
    nopat = None if annual_ebit is None else annual_ebit * (1 - _TAX_RATE)
    # Working capital drives the P/working-capital multiple (Graham's basis).
    working_capital = _sub(f.current_assets, f.current_liabilities)
    # Free cash flow: operating cash flow minus capex, annualized like the other
    # flows so a bare year-to-date period is comparable to a full year.
    annual_fcf = _annualized(_sub(f.cfo, f.capex), f)

    prev_revenue = previous.revenue if previous is not None else None
    prev_net_income = previous.net_income if previous is not None else None

    # The bank's spread, before the cost of default. A bank's 3.03 already deducts
    # the loan-loss provision (it sits inside the intermediation expenses in the
    # parent chart of accounts, ADR 0019), so adding the provision back — it is filed
    # negative — recovers the margin the bank earned before writing anything off.
    # That is the *margem financeira bruta* the banks themselves report.
    interest_margin = _sub(f.gross_profit, f.loan_loss_provision)
    annual_interest_margin = _annualized(interest_margin, f)
    # What the bank's own payroll and back office consume of what it earns: the
    # spread plus the fees it charges. Both sides annualized, so a quarter compares
    # to a year (ADR 0021).
    operating_expense = _add(f.personnel_expense, f.admin_expense)
    operating_revenue = _add(interest_margin, f.fee_income)
    annual_provision = _annualized(f.loan_loss_provision, f)

    indicators = Indicators(
        roe=_div(annual_net_income, f.equity),
        roa=_div(annual_net_income, f.total_assets),
        roic=_div(nopat, invested_capital),
        net_margin=_div(f.net_income, f.revenue),
        gross_margin=_div(f.gross_profit, f.revenue),
        ebit_margin=_div(f.ebit, f.revenue),
        ebitda_margin=_div(f.ebitda, f.revenue),
        asset_turnover=_div(annual_revenue, f.total_assets),
        eps=_div(annual_net_income, market.shares),
        bvps=_div(f.equity, market.shares),
        net_debt=net_debt,
        net_debt_to_ebitda=_div(net_debt, annual_ebitda),
        debt_to_equity=_div(f.total_debt, f.equity),
        liabilities_to_assets=_div(_sub(f.total_assets, f.equity), f.total_assets),
        current_ratio=_div(f.current_assets, f.current_liabilities),
        revenue_growth=_growth(f.revenue, prev_revenue),
        net_income_growth=_growth(f.net_income, prev_net_income),
        pe=_div(cap, annual_net_income),
        pb=_div(cap, f.equity),
        psr=_div(cap, annual_revenue),
        price_to_assets=_div(cap, f.total_assets),
        price_to_ebit=_div(cap, annual_ebit),
        price_to_working_capital=_div(cap, working_capital),
        net_interest_margin=_div(annual_interest_margin, f.total_assets),
        # Expenses are filed negative, so the ratio is negated to read as a cost.
        efficiency_ratio=_negated(_div(operating_expense, operating_revenue)),
        cost_of_risk=_negated(_div(annual_provision, f.loan_book)),
        payout=_div(f.dividends_paid, f.net_income),
        dividend_yield=_div(f.dividends_paid, cap),
        ev_ebitda=_div(enterprise_value, annual_ebitda),
        fcf=annual_fcf,
        price_to_fcf=_div(cap, annual_fcf),
        fcf_yield=_div(annual_fcf, cap),
        revenue=f.revenue,
        net_income=f.net_income,
        dividends=f.dividends_paid,
    )
    indicators = _suppressed(indicators, _inapplicable(f))
    return replace(
        indicators, null_reasons=_null_reasons(indicators, f, previous, market)
    )
