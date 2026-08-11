"""The computed indicators (Phase 2 output).

Pure value object. Every field is ``Decimal | None`` — ``None`` is meaningful,
never zero — and ``null_reasons`` names *why* each null is null (#30), as a
parallel map rather than a sentinel inside the ``Decimal | None`` fields: a
sentinel would poison every consumer's arithmetic, while an absent key degrades
to the old behaviour. Ratios are fractions (0.18 = 18%), not percentages, so
the presentation layer decides formatting.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, fields
from decimal import Decimal
from enum import StrEnum


class NullReason(StrEnum):
    """Why an indicator is null — the enumerable cause vocabulary of #30.

    Four root causes, keyed on the *accounting regime* (what the company
    actually files) rather than the ``Sector`` enum (ADR 0006):

    * ``INAPPLICABLE_REGIME`` — economically meaningless under the filer's
      regime (net debt for a bank: deposits are input, not borrowing).
    * ``SOURCE_ACCOUNT_UNMAPPED`` — our mapper deliberately never reads the
      account for this regime; computable in principle, not implemented.
    * ``SOURCE_ACCOUNT_ABSENT`` — we looked for the account and the filing has
      no such line (e.g. no dividend outflow in the DFC that year).
    * ``MISSING_PRICE`` / ``MISSING_SHARE_COUNT`` /
      ``MISSING_UNIT_COMPOSITION`` / ``MISSING_PRIOR_PERIOD`` —
      an upstream input from another source is missing (the quote series, the
      FRE share count, the prior year's ingestion), split so a report can say
      *which*. ``MISSING_PRICE`` is the *transient* price miss (no session for
      that year); ``PRICE_SYMBOL_NOT_FOUND`` is its non-transient sibling — the
      series does not carry the code at all (a delisting, or a rename #193), so
      the null is structural, not a passing outage (#64).
      ``NOT_YET_LISTED`` is the third, at the other end of the timeline: the
      period *precedes the instrument's first trade*, so no source will ever fill
      it — CXSE3 listed in 2021 and the SAPR11 unit did not exist before 2017,
      yet extending the history to 2015 (#63) produced rows for both (#153). It
      is a fact about the world rather than a gap of ours, and it is the only
      price cause that is *deliberate*: the others are worth chasing, this one
      is not.
    * ``ZERO_DENOMINATOR`` — every input is present, but the ratio is undefined
      because its denominator is zero (a holding filing revenue = 0 nulls
      P/Receita and the margins; a year with ~zero earnings nulls P/E, payout).
      The arithmetic dead-end, named rather than left unclassified.
    * ``NON_POSITIVE_ENDPOINT`` — ``ZERO_DENOMINATOR``'s sibling for a
      compounded rate (#144). A CAGR raises the ratio of its two endpoints to a
      fractional power, which has no real value unless *both* endpoints are
      positive: a company that lost money in the first year of the window has no
      compounded growth rate, and two negative endpoints would report a
      deepening loss as growth. Distinct from a zero denominator because the
      inputs are present and well-formed — it is the arithmetic, not the data,
      that runs out.

    A null with no recorded reason is *unclassified* — a reportable status of
    its own (#47). ``smaug doctor --all`` gates this across the exchange; formula
    and reconciliation tests independently protect non-null values (ADR 0050).
    """

    INAPPLICABLE_REGIME = "inapplicable_regime"
    SOURCE_ACCOUNT_UNMAPPED = "source_account_unmapped"
    SOURCE_ACCOUNT_ABSENT = "source_account_absent"
    MISSING_PRICE = "missing_price"
    PRICE_SYMBOL_NOT_FOUND = "price_symbol_not_found"
    NOT_YET_LISTED = "not_yet_listed"
    MISSING_SHARE_COUNT = "missing_share_count"
    MISSING_UNIT_COMPOSITION = "missing_unit_composition"
    MISSING_CPC41_DISCLOSURE = "missing_cpc41_disclosure"
    MISSING_WEIGHTED_AVERAGE_SHARES = "missing_weighted_average_shares"
    MISSING_ECONOMIC_RIGHTS = "missing_economic_rights"
    MISSING_PRIOR_PERIOD = "missing_prior_period"
    ZERO_DENOMINATOR = "zero_denominator"
    NON_POSITIVE_ENDPOINT = "non_positive_endpoint"


@dataclass(frozen=True)
class Indicators:
    """Fundamental + market indicators for one ticker at one point in time."""

    # Profitability. The ratios that mix a result with a whole-firm denominator
    # are published on both statement slices (ADR 0026): the bare name pairs the
    # controllers' result with the controllers' equity (the listed shares' own
    # return), and the ``_total`` variant pairs the consolidated total — minority
    # included — with the consolidated denominator. Per-share and cap-based
    # indicators have no ``_total`` variant: the share count and the cap are the
    # controllers' instruments, so a total numerator would mix slices.
    roe: Decimal | None = None
    roe_total: Decimal | None = None  # total net income / total equity
    roa: Decimal | None = None
    roa_total: Decimal | None = None  # total net income / total assets
    roic: Decimal | None = None  # NOPAT (EBIT·(1−tax)) / invested capital
    net_margin: Decimal | None = None
    net_margin_total: Decimal | None = None  # total net income / revenue
    gross_margin: Decimal | None = None
    ebit_margin: Decimal | None = None
    ebitda_margin: Decimal | None = None
    asset_turnover: Decimal | None = None  # revenue / total assets
    # Per share
    # ``eps`` remains the compatibility alias for the filed basic value. New
    # consumers use the explicit fields so a P/E can state which CPC 41 basis it
    # selected rather than silently mixing basic and diluted denominators.
    eps: Decimal | None = None
    eps_basic: Decimal | None = None
    eps_diluted: Decimal | None = None
    bvps: Decimal | None = None  # VPA — book value per share
    # Leverage / liquidity
    net_debt: Decimal | None = None
    net_debt_to_ebitda: Decimal | None = None
    net_debt_to_ebit: Decimal | None = None
    net_debt_to_equity: Decimal | None = None
    debt_to_equity: Decimal | None = None  # gross debt / equity
    # The two sit on deliberately different slices and are NOT complements: what
    # they leave between them is the minority interest, which is neither a
    # creditor's claim nor the listed shareholders' (ADR 0029).
    liabilities_to_assets: Decimal | None = None  # (assets − equity_total) / assets
    equity_to_assets: Decimal | None = None  # controllers' equity / assets
    current_ratio: Decimal | None = None
    # Growth (needs a prior comparable period)
    revenue_growth: Decimal | None = None
    net_income_growth: Decimal | None = None
    # Compounded annual growth over a *stated* window (#144). The year-on-year
    # figures above let one atypical exercise dominate the reading — a profit
    # that fell 40% and then grew 60% reads as a 60% grower. These take the ratio
    # of two endpoints five exercises apart: ``(this year / five years back) **
    # (1/5) - 1``. The window is in the name on purpose, because the reference
    # platforms disagree on what "CAGR 5A" spans and a compounded rate over an
    # unstated window is not a number this project publishes. Null — never
    # silently shortened — when the closed-year series is shorter than six
    # exercises, and null when the base endpoint is not positive
    # (``NON_POSITIVE_BASE``). Closed exercises only: the TTM window is a moving
    # 12 months, not one more of them.
    revenue_cagr_5y: Decimal | None = None
    ebitda_cagr_5y: Decimal | None = None
    ebit_cagr_5y: Decimal | None = None
    net_income_cagr_5y: Decimal | None = None
    # Market multiples
    pe: Decimal | None = None
    pb: Decimal | None = None
    psr: Decimal | None = None  # P/Receita — price / sales
    price_to_assets: Decimal | None = None
    price_to_ebit: Decimal | None = None
    price_to_working_capital: Decimal | None = None
    payout: Decimal | None = None  # dividends paid / net income
    dividend_yield: Decimal | None = None
    # The declared basis (#104): dividends + JCP the parent charged against
    # equity in the period (DMPL), not the cash that left (DFC). The two answer
    # different questions — the cash paid in a year was often declared on the
    # prior year's profit — and the declared one reconciles to the distribution
    # the company records against equity.
    payout_declared: Decimal | None = None  # dividends declared / net income
    dividend_yield_declared: Decimal | None = None
    ev_ebitda: Decimal | None = None
    ev_ebit: Decimal | None = None
    # Free cash flow (CFO − capex)
    fcf: Decimal | None = None  # annualized free cash flow, in absolute reais
    price_to_fcf: Decimal | None = None
    fcf_yield: Decimal | None = None
    # Bank-only ratios (ADR 0021). A bank's balance sheet is its business, so the
    # ratios that describe it are its own: how wide the spread it earns is, how much
    # of that spread its own payroll consumes, and what its lending is costing it in
    # defaults. Null under every other regime — inapplicable, not missing.
    net_interest_margin: Decimal | None = None  # spread / total assets
    efficiency_ratio: Decimal | None = None  # operating expense / operating revenue
    cost_of_risk: Decimal | None = None  # loan-loss provision / loan book
    # Headline financials (absolute reais, the period's own figure — not
    # annualized). Persisted alongside the ratios so the front-end can chart the
    # per-year evolution of revenue / earnings / dividends, which the ratios alone
    # cannot reconstruct.
    revenue: Decimal | None = None
    net_income: Decimal | None = None  # controllers' slice — pairs with eps
    net_income_total: Decimal | None = None  # consolidated, minority included
    dividends: Decimal | None = None
    dividends_declared: Decimal | None = None  # DMPL charge, not the DFC cash
    # Balance-sheet scale (absolute reais, at the period's closing instant).
    # Persisted for the same reason as the flows above: the ratios divide the two
    # sides away, so what the company owns against what it owes cannot be
    # reconstructed from ``liabilities_to_assets`` alone. ``total_liabilities``
    # subtracts the *consolidated* equity — minority interest is equity, not
    # third-party capital. Note this differs from ``liabilities_to_assets``,
    # which subtracts the controllers' slice and so counts the minority as
    # liability; the two disagree by exactly that amount (#149).
    total_assets: Decimal | None = None
    total_liabilities: Decimal | None = None
    equity: Decimal | None = None  # controllers' slice — the bvps numerator
    equity_total: Decimal | None = None  # consolidated, minority included
    # Scale figures (absolute reais / a share count) — the market-side inputs the
    # calculator already builds its multiples from, persisted so the front-end can
    # show them at the top of a ticker page. ``market_cap`` is the sum over the
    # listed classes (ADR 0014); ``enterprise_value`` is ``cap + net_debt`` and is
    # null wherever ``net_debt`` is (banks: inapplicable); ``shares`` is the filed
    # closing count used by BVPS and market cap; it is not CPC 41's weighted EPS
    # denominator.
    market_cap: Decimal | None = None
    enterprise_value: Decimal | None = None
    shares: Decimal | None = None
    # Why each null field is null, keyed by the field's name. Only null fields
    # appear; a null field with no entry is unclassified (see ``NullReason``).
    null_reasons: Mapping[str, NullReason] = field(default_factory=dict)


def indicator_names() -> tuple[str, ...]:
    """The names of every indicator field, in declaration order.

    Derived from the dataclass so a new indicator is covered automatically —
    the coverage report (#47) enumerates exactly these, and ``null_reasons`` (the
    attribution map, not an indicator) is excluded.
    """
    return tuple(f.name for f in fields(Indicators) if f.name != "null_reasons")
