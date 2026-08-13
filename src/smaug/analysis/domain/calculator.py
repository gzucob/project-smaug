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

from collections.abc import Sequence
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
# (see docs/adr/0002-*).
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


# The compounded-growth window, in years of *variation* (#144). Six closed
# exercises are needed to span five years of change, and the count is in the
# indicator's own name (``revenue_cagr_5y``): the two endpoints are exactly five
# exercises apart.
_CAGR_YEARS = 5


def _cagr(series: Sequence[Decimal | None]) -> Decimal | None:
    """Compounded annual rate between the endpoints of a closed-year series.

    ``series`` is the value for each closed exercise, oldest → newest, ending at
    the period being computed. The rate is taken over the last ``_CAGR_YEARS``
    years of variation, so it needs ``_CAGR_YEARS + 1`` exercises: a shorter
    history yields ``None`` rather than a rate over a quietly narrower window,
    which would not be the number the label promises.

    Only the two endpoints matter — that is what "compounded" means, and it is
    also the reading's weakness: the path between them is invisible. Both
    endpoints must be positive, since ``(a / b) ** (1/n)`` has no real value when
    the ratio is negative and, for two negatives, would report a loss that
    deepened as growth.
    """
    if len(series) < _CAGR_YEARS + 1:
        return None
    start = series[-(_CAGR_YEARS + 1)]
    end = series[-1]
    if start is None or end is None or start <= 0 or end <= 0:
        return None
    # Decimal has no fractional power, and ``**`` on Decimal rejects a non-integer
    # exponent outright. float is acceptable precision here: this is a rate shown
    # to one decimal place, not money being added up.
    rate = (float(end) / float(start)) ** (1 / _CAGR_YEARS) - 1
    return Decimal(str(rate))


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
    """Complete evidenced debt net of CPC 03 cash equivalents (ADR 0059)."""
    if financials.total_debt is None or financials.cash_equivalents is None:
        return None
    return financials.total_debt - financials.cash_equivalents


# Indicators genuinely meaningless under a given accounting regime: the null is
# an inapplicable-regime null regardless of inputs (#30). A bank reports capital
# adequacy (Índice de Basileia), not net debt / EV-EBITDA, and has no EBITDA; an
# insurer's filed schema makes the generic operating-margin family degenerate.
#
# ADR 0015 (#48) closed the three verdicts ADR 0010 had left to the mapping: a
# bank's balance sheet has no current/non-current split whatsoever, so its
# current ratio and P/working-capital are not "not yet mapped" but unbuildable —
# and statutory ROIC's denominator (consolidated equity + net debt) inherits it
# above, since a deposit is funding, not borrowing. Every *other* indicator a
# financial filer nulls now falls through to the input check.
#
# The three bank ratios (ADR 0058) run the other way: they describe a balance sheet
# that *is* the business, and a company that sells goods has no spread, no loan book
# and no payroll-against-spread to report. They are inapplicable to everyone else.
_BANK_ONLY = frozenset({"net_interest_margin", "efficiency_ratio", "cost_of_risk"})

_INAPPLICABLE_BY_REGIME: dict[AccountingRegime, frozenset[str]] = {
    AccountingRegime.BANK: frozenset(
        {
            "ebit_margin",
            "ebitda_margin",
            "ebit_cagr_5y",
            "price_to_ebit",
            "fcf",
            "price_to_fcf",
            "fcf_yield",
            "net_debt",
            "net_debt_to_ebitda",
            "net_debt_to_ebit",
            "net_debt_to_equity",
            "debt_to_equity",
            "ev_ebitda",
            "ev_ebit",
            "enterprise_value",
            "roic_statutory",
            "current_ratio",
            "price_to_working_capital",
        }
    ),
    AccountingRegime.INSURANCE: frozenset(
        {
            "gross_margin",
            "ebit_margin",
            "ebitda_margin",
            # A generic statutory ROIC remains a category error for an insurer:
            # underwriting and investment liabilities are the operation, not a
            # corporate invested-capital bridge (ADR 0010/0059).
            "roic_statutory",
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
    price: bool = False
    shares: bool = False
    cash_distributions: bool = False
    prior: str | None = None
    # The ``StandardizedFinancials`` field a compounded rate reads across the
    # closed-year series. Set only for the CAGRs, whose null is attributed
    # against the *window* — too few exercises, or an endpoint that is not
    # positive — rather than against the current period's own accounts.
    series: str | None = None


_NEEDS: dict[str, _Needs] = {
    "roe": _Needs(accounts=("net_income", "equity")),
    "roe_total": _Needs(accounts=("net_income_total", "equity_total")),
    "roa": _Needs(accounts=("net_income", "total_assets")),
    "roa_total": _Needs(accounts=("net_income_total", "total_assets")),
    "roic_statutory": _Needs(
        accounts=("ebit", "equity_total", "total_debt", "cash_equivalents")
    ),
    "net_margin": _Needs(accounts=("net_income", "revenue")),
    "net_margin_total": _Needs(accounts=("net_income_total", "revenue")),
    "gross_margin": _Needs(accounts=("gross_profit", "revenue")),
    "ebit_margin": _Needs(accounts=("ebit", "revenue")),
    "ebitda_margin": _Needs(accounts=("ebitda", "revenue")),
    "asset_turnover": _Needs(accounts=("revenue", "total_assets")),
    "eps": _Needs(accounts=("eps_basic",)),
    "eps_basic": _Needs(accounts=("eps_basic",)),
    "eps_diluted": _Needs(accounts=("eps_diluted",)),
    "bvps": _Needs(accounts=("equity",), shares=True),
    "net_debt": _Needs(accounts=("total_debt", "cash_equivalents")),
    "cash_equivalents": _Needs(accounts=("cash_equivalents",)),
    "current_financial_investments": _Needs(
        accounts=("current_financial_investments",)
    ),
    "net_debt_to_ebitda": _Needs(accounts=("total_debt", "cash_equivalents", "ebitda")),
    "net_debt_to_ebit": _Needs(accounts=("total_debt", "cash_equivalents", "ebit")),
    "net_debt_to_equity": _Needs(accounts=("total_debt", "cash_equivalents", "equity")),
    "debt_to_equity": _Needs(accounts=("total_debt", "equity")),
    "liabilities_to_assets": _Needs(accounts=("total_assets", "equity_total")),
    "equity_to_assets": _Needs(accounts=("equity", "total_assets")),
    "current_ratio": _Needs(accounts=("current_assets", "current_liabilities")),
    "revenue_growth": _Needs(accounts=("revenue",), prior="revenue"),
    "net_income_growth": _Needs(accounts=("net_income",), prior="net_income"),
    "revenue_cagr_5y": _Needs(series="revenue"),
    "ebitda_cagr_5y": _Needs(series="ebitda"),
    "ebit_cagr_5y": _Needs(series="ebit"),
    "net_income_cagr_5y": _Needs(series="net_income"),
    "pe_basic": _Needs(accounts=("eps_basic",), price=True),
    "pe_diluted": _Needs(accounts=("eps_diluted",), price=True),
    "pb": _Needs(accounts=("equity",), price=True, shares=True),
    "company_pe": _Needs(accounts=("net_income",), cap=True),
    "company_pb": _Needs(accounts=("equity",), cap=True),
    "psr": _Needs(accounts=("revenue",), cap=True),
    "price_to_assets": _Needs(accounts=("total_assets",), cap=True),
    "price_to_ebit": _Needs(accounts=("ebit",), cap=True),
    "price_to_working_capital": _Needs(
        accounts=("current_assets", "current_liabilities"), cap=True
    ),
    "net_interest_margin": _Needs(
        accounts=("bank_interest_result_annualized", "average_earning_assets")
    ),
    "efficiency_ratio": _Needs(
        accounts=("bank_efficiency_expenses", "bank_efficiency_income")
    ),
    "cost_of_risk": _Needs(
        accounts=("credit_loss_expense_annualized", "average_credit_portfolio")
    ),
    "dividend_yield": _Needs(price=True, cash_distributions=True),
    "payout_cash_paid_in_period": _Needs(accounts=("dividends_paid", "net_income")),
    "payout_declared_in_period": _Needs(accounts=("dividends_declared", "net_income")),
    "company_cash_yield_paid_in_period": _Needs(accounts=("dividends_paid",), cap=True),
    "company_yield_declared_in_period": _Needs(
        accounts=("dividends_declared",), cap=True
    ),
    "ev_ebitda": _Needs(
        accounts=(
            "total_debt",
            "cash_equivalents",
            "equity_total",
            "equity",
            "ebitda",
        ),
        cap=True,
    ),
    "ev_ebit": _Needs(
        accounts=(
            "total_debt",
            "cash_equivalents",
            "equity_total",
            "equity",
            "ebit",
        ),
        cap=True,
    ),
    "fcf": _Needs(accounts=("cfo", "capex")),
    "price_to_fcf": _Needs(accounts=("cfo", "capex"), cap=True),
    "fcf_yield": _Needs(accounts=("cfo", "capex"), cap=True),
    "revenue": _Needs(accounts=("revenue",)),
    "net_income": _Needs(accounts=("net_income",)),
    "net_income_total": _Needs(accounts=("net_income_total",)),
    "distributions_per_security": _Needs(cash_distributions=True),
    "company_distributions_paid_in_period": _Needs(accounts=("dividends_paid",)),
    "company_distributions_declared_in_period": _Needs(
        accounts=("dividends_declared",)
    ),
    # Balance-sheet scale. ``total_liabilities`` is assets less the consolidated
    # equity, so it is missing whenever either side is.
    "total_assets": _Needs(accounts=("total_assets",)),
    "total_liabilities": _Needs(accounts=("total_assets", "equity_total")),
    "equity": _Needs(accounts=("equity",)),
    "equity_total": _Needs(accounts=("equity_total",)),
    "non_controlling_interests": _Needs(accounts=("equity_total", "equity")),
    # Scale figures. ``enterprise_value`` = cap + net debt + non-controlling
    # interests, so it needs both equity slices as well as the liquidity bridge.
    # It is also in the bank inapplicable set, so a bank's null is named
    # INAPPLICABLE_REGIME before its inputs are checked.
    "market_cap": _Needs(cap=True),
    "enterprise_value": _Needs(
        accounts=(
            "total_debt",
            "cash_equivalents",
            "equity_total",
            "equity",
        ),
        cap=True,
    ),
    "shares": _Needs(shares=True),
}


def _classify(
    name: str,
    needs: _Needs,
    f: StandardizedFinancials,
    previous: StandardizedFinancials | None,
    market: MarketData,
    history: Sequence[StandardizedFinancials],
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
    if (
        name in _BANK_ONLY
        and f.bank_ratio_null_reason is not None
        and any(getattr(f, account) is None for account in needs.accounts)
    ):
        return f.bank_ratio_null_reason
    if name in {"eps", "eps_basic"} and f.eps_basic_null_reason is not None:
        return f.eps_basic_null_reason
    if name == "eps_diluted" and f.eps_diluted_null_reason is not None:
        return f.eps_diluted_null_reason
    if name == "pe_basic" and f.eps_basic_null_reason is not None:
        return f.eps_basic_null_reason
    if name == "pe_diluted" and f.eps_diluted_null_reason is not None:
        return f.eps_diluted_null_reason
    if needs.series is not None:
        return _classify_cagr(needs.series, f, history)
    for account in needs.accounts:
        if getattr(f, account) is None:
            if account == "total_debt" and f.debt_coverage_null_reason is not None:
                return f.debt_coverage_null_reason
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
    if needs.price and market.price is None:
        return NullReason.MISSING_PRICE
    if needs.shares and market.shares is None:
        return market.shares_null_reason or NullReason.MISSING_SHARE_COUNT
    if needs.cash_distributions and market.cash_distributions is None:
        return (
            market.cash_distributions_null_reason
            or NullReason.MISSING_CASH_DISTRIBUTIONS
        )
    if needs.prior is not None and (
        previous is None or getattr(previous, needs.prior) is None
    ):
        return NullReason.MISSING_PRIOR_PERIOD
    return NullReason.ZERO_DENOMINATOR


def _classify_cagr(
    account: str,
    f: StandardizedFinancials,
    history: Sequence[StandardizedFinancials],
) -> NullReason:
    """Attribute a null compounded rate, against the window rather than the period.

    Precedence mirrors ``_classify``'s: too short a history first (the rate does
    not exist yet for this company, whatever its accounts say), then a missing
    endpoint, then the arithmetic dead-end — an endpoint that is not positive,
    which is the one case where every input is present and the rate still cannot
    be formed.
    """
    if len(history) < _CAGR_YEARS + 1:
        return NullReason.MISSING_PRIOR_PERIOD
    endpoints = (
        getattr(history[-(_CAGR_YEARS + 1)], account),
        getattr(history[-1], account),
    )
    for value in endpoints:
        if value is None:
            if account in f.unmapped_fields:
                return NullReason.SOURCE_ACCOUNT_UNMAPPED
            return NullReason.SOURCE_ACCOUNT_ABSENT
    return NullReason.NON_POSITIVE_ENDPOINT


def _null_reasons(
    computed: Indicators,
    f: StandardizedFinancials,
    previous: StandardizedFinancials | None,
    market: MarketData,
    history: Sequence[StandardizedFinancials],
) -> dict[str, NullReason]:
    """Name the cause of every null in ``computed`` (#30).

    Every null carries a reason, including the zero-denominator dead-end
    (ANL-23); ``smaug doctor --all`` verifies this at exchange scale.
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
            history,
            inapplicable=inapplicable,
        )
    return reasons


def compute(
    current: StandardizedFinancials,
    previous: StandardizedFinancials | None,
    market: MarketData,
    history: Sequence[StandardizedFinancials] = (),
) -> Indicators:
    """Compute all applicable indicators for one ticker/period.

    Every null field in the result carries its cause in ``null_reasons`` when
    one is classifiable — the reason is attributed here, where the null is
    produced, because only the calculator sees which input broke which ratio.

    ``history`` is the closed-exercise series this period sits at the end of,
    oldest → newest, and only the compounded rates (#144) read it. A closed-year
    view passes the exercises up to and including its own, so a 2020 row is
    computed from what was knowable in 2020 rather than from the whole series;
    the TTM view passes every closed exercise, and its CAGR therefore ends at the
    last closed year — a compounded rate is a property of the closed history, and
    a moving 12-month window is not one more exercise to compound. Left empty by
    default so the rates simply do not compute, which is what a caller without a
    history should get.
    """
    f = current
    # Everything is computed from its inputs and *then* suppressed per regime (#48).
    # The old blanket ``is_financial`` guard is gone: with the mapper reading each
    # regime's own chart of accounts (ADR 0015), supported ratios compute rather
    # than being blanked by sector. Bank EBIT/FCF are explicit exceptions because
    # PBT is not EBIT and deposit-funded CFO-CAPEX is not comparable FCF (ADR 0058).
    # Unsupported measures are named — once — in
    # ``_INAPPLICABLE_BY_REGIME``, which now drives the value as well as the reason.
    cap = market.market_cap
    annual_net_income = _annualized(f.net_income, f)
    annual_net_income_total = _annualized(f.net_income_total, f)
    annual_revenue = _annualized(f.revenue, f)
    annual_ebit = _annualized(f.ebit, f)
    annual_ebitda = _annualized(f.ebitda, f)

    net_debt = _net_debt(f)
    non_controlling_interests = _sub(f.equity_total, f.equity)
    # A consolidated EBIT/EBITDA belongs to the whole group. Add the part of that
    # group funded by non-controlling owners to the market-side numerator, and use
    # consolidated equity in statutory ROIC's invested capital (ADR 0057).
    enterprise_value = _add(_add(cap, net_debt), non_controlling_interests)
    invested_capital = _add(f.equity_total, net_debt)
    nopat = None if annual_ebit is None else annual_ebit * (1 - _TAX_RATE)
    # Working capital drives the P/working-capital multiple (Graham's basis).
    working_capital = _sub(f.current_assets, f.current_liabilities)
    # Free cash flow: operating cash flow minus capex, annualized like the other
    # flows so a bare year-to-date period is comparable to a full year.
    annual_fcf = _annualized(_sub(f.cfo, f.capex), f)
    bvps = _div(f.equity, market.shares)

    prev_revenue = previous.revenue if previous is not None else None
    prev_net_income = previous.net_income if previous is not None else None

    def series(account: str) -> list[Decimal | None]:
        """One account across the closed exercises, oldest → newest."""
        return [getattr(annual, account) for annual in history]

    # Bank ratios only consume explicitly paired, already annualized
    # regulatory/issuer inputs
    # (ADR 0058). The CVM-only mapper leaves them null: closing total assets, a
    # partial operating-revenue subtotal, and a closing net loan book are not
    # substitutes for the published average/perimeter definitions.
    indicators = Indicators(
        roe=_div(annual_net_income, f.equity),
        roe_total=_div(annual_net_income_total, f.equity_total),
        roa=_div(annual_net_income, f.total_assets),
        roa_total=_div(annual_net_income_total, f.total_assets),
        roic_statutory=_div(nopat, invested_capital),
        net_margin=_div(f.net_income, f.revenue),
        net_margin_total=_div(f.net_income_total, f.revenue),
        gross_margin=_div(f.gross_profit, f.revenue),
        ebit_margin=_div(f.ebit, f.revenue),
        ebitda_margin=_div(f.ebitda, f.revenue),
        asset_turnover=_div(annual_revenue, f.total_assets),
        eps=f.eps_basic,
        eps_basic=f.eps_basic,
        eps_diluted=f.eps_diluted,
        bvps=bvps,
        net_debt=net_debt,
        cash_equivalents=f.cash_equivalents,
        current_financial_investments=f.current_financial_investments,
        net_debt_to_ebitda=_div(net_debt, annual_ebitda),
        net_debt_to_ebit=_div(net_debt, annual_ebit),
        net_debt_to_equity=_div(net_debt, f.equity),
        debt_to_equity=_div(f.total_debt, f.equity),
        # Third-party capital only: the minority interest is equity (CPC 26), so
        # the consolidated slice is what comes off the assets (ADR 0029). This is
        # ``total_liabilities`` over the assets, by construction.
        liabilities_to_assets=_div(
            _sub(f.total_assets, f.equity_total), f.total_assets
        ),
        equity_to_assets=_div(f.equity, f.total_assets),
        current_ratio=_div(f.current_assets, f.current_liabilities),
        revenue_growth=_growth(f.revenue, prev_revenue),
        net_income_growth=_growth(f.net_income, prev_net_income),
        revenue_cagr_5y=_cagr(series("revenue")),
        ebitda_cagr_5y=_cagr(series("ebitda")),
        ebit_cagr_5y=_cagr(series("ebit")),
        net_income_cagr_5y=_cagr(series("net_income")),
        pe_basic=_div(market.price, f.eps_basic),
        pe_diluted=_div(market.price, f.eps_diluted),
        pb=_div(market.price, bvps),
        company_pe=_div(cap, annual_net_income),
        company_pb=_div(cap, f.equity),
        psr=_div(cap, annual_revenue),
        price_to_assets=_div(cap, f.total_assets),
        price_to_ebit=_div(cap, annual_ebit),
        price_to_working_capital=_div(cap, working_capital),
        net_interest_margin=_div(
            f.bank_interest_result_annualized, f.average_earning_assets
        ),
        efficiency_ratio=_div(f.bank_efficiency_expenses, f.bank_efficiency_income),
        cost_of_risk=_div(f.credit_loss_expense_annualized, f.average_credit_portfolio),
        dividend_yield=_div(market.cash_distributions, market.price),
        payout_cash_paid_in_period=_div(f.dividends_paid, f.net_income),
        payout_declared_in_period=_div(f.dividends_declared, f.net_income),
        company_cash_yield_paid_in_period=_div(f.dividends_paid, cap),
        company_yield_declared_in_period=_div(f.dividends_declared, cap),
        ev_ebitda=_div(enterprise_value, annual_ebitda),
        ev_ebit=_div(enterprise_value, annual_ebit),
        fcf=annual_fcf,
        price_to_fcf=_div(cap, annual_fcf),
        fcf_yield=_div(annual_fcf, cap),
        revenue=f.revenue,
        net_income=f.net_income,
        net_income_total=f.net_income_total,
        distributions_per_security=market.cash_distributions,
        company_distributions_paid_in_period=f.dividends_paid,
        company_distributions_declared_in_period=f.dividends_declared,
        total_assets=f.total_assets,
        total_liabilities=_sub(f.total_assets, f.equity_total),
        equity=f.equity,
        equity_total=f.equity_total,
        market_cap=cap,
        enterprise_value=enterprise_value,
        non_controlling_interests=non_controlling_interests,
        shares=market.shares,
    )
    indicators = _suppressed(indicators, _inapplicable(f))
    return replace(
        indicators,
        null_reasons=_null_reasons(indicators, f, previous, market, history),
    )
