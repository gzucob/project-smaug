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
from types import MappingProxyType
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from smaug.analysis.domain.financials import (
        BankRegulatoryProvenance,
        Cpc41WindowProvenance,
        SourceAccountEvidence,
    )


class NullReason(StrEnum):
    """Why an indicator is null — the enumerable cause vocabulary of #30.

    Root causes, keyed on the *accounting regime* (what the company
    actually files) rather than the ``Sector`` enum (ADR 0006):

    * ``INAPPLICABLE_REGIME`` — economically meaningless under the filer's
      regime (net debt for a bank: deposits are input, not borrowing).
    * ``SOURCE_ACCOUNT_UNMAPPED`` — our mapper deliberately never reads the
      account for this regime; computable in principle, not implemented.
    * ``SOURCE_ACCOUNT_ABSENT`` — we looked for the account and the filing has
      no such line (e.g. no dividend outflow in the DFC that year).
    * ``MISSING_REGULATORY_DISCLOSURE`` — the formula needs a public
      regulator/issuer disclosure outside the CVM structured statements, such as
      average earning assets and a bank's complete efficiency perimeter. No
      closing-balance or partial-account approximation substitutes for it.
    * ``PARTIAL_REGULATORY_DISCLOSURE`` / ``INCOMPATIBLE_REGULATORY_DISCLOSURE``
      — a bank disclosure provides only one side of a required pair, or its
      source metadata cannot prove the same period/perimeter/basis.
    * ``INCOMPLETE_DEBT_COVERAGE`` — the balance sheet does not establish a
      complete interest-bearing-liability perimeter. An absent borrowing line is
      not evidence of zero debt, and an undecomposed generic financial-liability
      bucket is not silently promoted to debt.
    * ``MISSING_PRICE`` / ``PRICE_SYMBOL_NOT_FOUND`` /
      ``PRICE_SOURCE_UNAVAILABLE`` / ``PRICE_SOURCE_MALFORMED`` /
      ``PRICE_SOURCE_TIMEOUT`` / ``MISSING_SHARE_COUNT`` /
      ``MISSING_UNIT_COMPOSITION`` / ``MISSING_TREASURY_COMPOSITION`` /
      ``UNRESOLVED_SHARE_CLASS`` / ``MISSING_ECONOMIC_RIGHTS`` /
      ``MISSING_PRIOR_PERIOD`` —
      an upstream input from another source is missing (the quote series, the
      FRE share count, the prior year's ingestion), split so a report can say
      *which*. ``MISSING_PRICE`` is the *transient* price miss (no session for
      that year); ``PRICE_SYMBOL_NOT_FOUND`` is its non-transient sibling — the
      series does not carry the code at all (a delisting, or a rename #193), so
      the null is structural, not a passing outage (#64). The three
      ``PRICE_SOURCE_*`` causes distinguish an unavailable archive, malformed
      content, and a timeout.
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
    PRICE_SOURCE_UNAVAILABLE = "price_source_unavailable"
    PRICE_SOURCE_MALFORMED = "price_source_malformed"
    PRICE_SOURCE_TIMEOUT = "price_source_timeout"
    NOT_YET_LISTED = "not_yet_listed"
    MISSING_SHARE_COUNT = "missing_share_count"
    MISSING_UNIT_COMPOSITION = "missing_unit_composition"
    MISSING_TREASURY_COMPOSITION = "missing_treasury_composition"
    UNRESOLVED_SHARE_CLASS = "unresolved_share_class"
    MISSING_REGULATORY_DISCLOSURE = "missing_regulatory_disclosure"
    PARTIAL_REGULATORY_DISCLOSURE = "partial_regulatory_disclosure"
    INCOMPATIBLE_REGULATORY_DISCLOSURE = "incompatible_regulatory_disclosure"
    INCOMPLETE_DEBT_COVERAGE = "incomplete_debt_coverage"
    MISSING_CPC41_DISCLOSURE = "missing_cpc41_disclosure"
    MISSING_WEIGHTED_AVERAGE_SHARES = "missing_weighted_average_shares"
    MISSING_ECONOMIC_RIGHTS = "missing_economic_rights"
    MISSING_CASH_DISTRIBUTIONS = "missing_cash_distributions"
    MISSING_CASH_DISTRIBUTION_VALUE = "missing_cash_distribution_value"
    MISSING_PRIOR_PERIOD = "missing_prior_period"
    ZERO_DENOMINATOR = "zero_denominator"
    NON_POSITIVE_ENDPOINT = "non_positive_endpoint"


class NullDisposition(StrEnum):
    """Stable product-level disposition for a named null.

    ``NullReason`` remains the diagnostic vocabulary and is intentionally more
    specific.  A disposition is the coarser answer a report or consumer needs:
    whether a null is inapplicable, an arithmetic dead-end, a disclosure that
    the primary source does not provide, a gap that can be investigated, or a
    historical period that did not exist.  The aliases keep the longer wording
    used in the issue contract available without creating extra categories.
    """

    INAPPLICABLE = "inapplicable"
    MATHEMATICALLY_UNDEFINED = "mathematically_undefined"
    PRIMARY_SOURCE_UNAVAILABLE = "primary_source_unavailable"
    RECOVERABLE_GAP = "recoverable_gap"
    HISTORICAL_PERIOD_DOES_NOT_EXIST = "historical_period_does_not_exist"

    # Descriptive aliases for callers that prefer the issue's wording.  They
    # are aliases, not additional dispositions, so exhaustive iteration remains
    # exactly five values.
    LEGITIMATE_INAPPLICABILITY = "inapplicable"
    PRIMARY_SOURCE_DISCLOSURE_UNAVAILABLE = "primary_source_unavailable"
    RECOVERABLE_SOURCE_GAP = "recoverable_gap"
    HISTORICAL_PERIOD_NOT_EXIST = "historical_period_does_not_exist"


# Keep this table adjacent to the enum: adding a NullReason without choosing a
# report disposition must fail loudly rather than silently becoming a sixth,
# unstable bucket.  ``MappingProxyType`` prevents callers from changing the
# product contract at runtime.
NULL_DISPOSITION_BY_REASON = MappingProxyType(
    {
        # A formula has no economic meaning under the filed regime.
        NullReason.INAPPLICABLE_REGIME: NullDisposition.INAPPLICABLE,
        # Inputs are present, but the requested arithmetic has no real result.
        NullReason.ZERO_DENOMINATOR: NullDisposition.MATHEMATICALLY_UNDEFINED,
        NullReason.NON_POSITIVE_ENDPOINT: NullDisposition.MATHEMATICALLY_UNDEFINED,
        # The applicable primary disclosure is absent or cannot prove the
        # required perimeter/basis.  There is no safe value to reconstruct.
        NullReason.SOURCE_ACCOUNT_ABSENT: NullDisposition.PRIMARY_SOURCE_UNAVAILABLE,
        NullReason.MISSING_REGULATORY_DISCLOSURE: (
            NullDisposition.PRIMARY_SOURCE_UNAVAILABLE
        ),
        NullReason.PARTIAL_REGULATORY_DISCLOSURE: (
            NullDisposition.PRIMARY_SOURCE_UNAVAILABLE
        ),
        NullReason.INCOMPATIBLE_REGULATORY_DISCLOSURE: (
            NullDisposition.PRIMARY_SOURCE_UNAVAILABLE
        ),
        NullReason.INCOMPLETE_DEBT_COVERAGE: (
            NullDisposition.PRIMARY_SOURCE_UNAVAILABLE
        ),
        NullReason.MISSING_CPC41_DISCLOSURE: NullDisposition.PRIMARY_SOURCE_UNAVAILABLE,
        NullReason.MISSING_WEIGHTED_AVERAGE_SHARES: (
            NullDisposition.PRIMARY_SOURCE_UNAVAILABLE
        ),
        # These are source, mapping, identity, continuity, or acquisition gaps
        # that can be revisited without changing the accounting formula.  The
        # generic missing_prior_period stays here until persisted evidence can
        # safely distinguish its accounting/window/tape causes.
        NullReason.SOURCE_ACCOUNT_UNMAPPED: NullDisposition.RECOVERABLE_GAP,
        NullReason.MISSING_PRICE: NullDisposition.RECOVERABLE_GAP,
        NullReason.PRICE_SYMBOL_NOT_FOUND: NullDisposition.RECOVERABLE_GAP,
        NullReason.PRICE_SOURCE_UNAVAILABLE: NullDisposition.RECOVERABLE_GAP,
        NullReason.PRICE_SOURCE_MALFORMED: NullDisposition.RECOVERABLE_GAP,
        NullReason.PRICE_SOURCE_TIMEOUT: NullDisposition.RECOVERABLE_GAP,
        NullReason.MISSING_SHARE_COUNT: NullDisposition.RECOVERABLE_GAP,
        NullReason.MISSING_UNIT_COMPOSITION: NullDisposition.RECOVERABLE_GAP,
        NullReason.MISSING_TREASURY_COMPOSITION: NullDisposition.RECOVERABLE_GAP,
        NullReason.UNRESOLVED_SHARE_CLASS: NullDisposition.RECOVERABLE_GAP,
        NullReason.MISSING_ECONOMIC_RIGHTS: NullDisposition.RECOVERABLE_GAP,
        NullReason.MISSING_CASH_DISTRIBUTIONS: NullDisposition.RECOVERABLE_GAP,
        NullReason.MISSING_CASH_DISTRIBUTION_VALUE: NullDisposition.RECOVERABLE_GAP,
        NullReason.MISSING_PRIOR_PERIOD: NullDisposition.RECOVERABLE_GAP,
        # The source cannot fill a period before the instrument first traded.
        NullReason.NOT_YET_LISTED: NullDisposition.HISTORICAL_PERIOD_DOES_NOT_EXIST,
    }
)

if set(NULL_DISPOSITION_BY_REASON) != set(NullReason):
    missing = set(NullReason) - set(NULL_DISPOSITION_BY_REASON)
    extra = set(NULL_DISPOSITION_BY_REASON) - set(NullReason)
    raise RuntimeError(
        "NullReason disposition table is not exhaustive: "
        f"missing={sorted(reason.value for reason in missing)} "
        f"extra={sorted(reason.value for reason in extra)}"
    )


def null_disposition(reason: NullReason) -> NullDisposition:
    """Return the one stable report disposition assigned to ``reason``."""
    return NULL_DISPOSITION_BY_REASON[reason]


class IndicatorTier(StrEnum):
    """How much interpretation is involved in an indicator's published value."""

    STRICT = "strict"
    MARKET_CONVENTION = "market_convention"


@dataclass(frozen=True)
class IndicatorContract:
    """Machine-readable basis metadata for market-facing indicators.

    The contract is static because it describes the formula, while the analysis
    row carries the view-specific price and share bases referenced by it. Keeping
    the two separate lets a client explain a value without inferring its meaning
    from a display label.
    """

    tier: IndicatorTier
    basis: str
    numerator: str
    denominator: str
    reference_period: str
    price_basis: str
    share_basis: str
    provenance: tuple[str, ...]


# The market-facing family needs a basis beyond a bare number. In particular,
# ``company_pe``/``company_pb`` are useful market conventions, while the
# per-security P/E fields retain the strict CPC 41 contract. The codes are stable
# API vocabulary; the front-end localizes them for readers.
INDICATOR_CONTRACT: dict[str, IndicatorContract] = {
    "pe_basic": IndicatorContract(
        tier=IndicatorTier.STRICT,
        basis="security_cpc41",
        numerator="security_price",
        denominator="cpc41_basic_eps",
        reference_period="view_period",
        price_basis="analysis.price_basis",
        share_basis="cpc41_weighted_average_class_rights",
        provenance=("cvm", "b3"),
    ),
    "eps_basic_market": IndicatorContract(
        tier=IndicatorTier.MARKET_CONVENTION,
        basis="security_market_convention",
        numerator="annualized_attributable_net_income",
        denominator="closing_outstanding_shares",
        reference_period="view_period",
        price_basis="not_applicable",
        share_basis="analysis.share_count_basis",
        provenance=("cvm",),
    ),
    "pe_basic_market": IndicatorContract(
        tier=IndicatorTier.MARKET_CONVENTION,
        basis="security_market_convention",
        numerator="security_price",
        denominator="market_convention_basic_eps",
        reference_period="view_period",
        price_basis="analysis.price_basis",
        share_basis="analysis.share_count_basis",
        provenance=("cvm", "b3"),
    ),
    "pe_diluted": IndicatorContract(
        tier=IndicatorTier.STRICT,
        basis="security_cpc41",
        numerator="security_price",
        denominator="cpc41_diluted_eps",
        reference_period="view_period",
        price_basis="analysis.price_basis",
        share_basis="cpc41_weighted_average_class_rights",
        provenance=("cvm", "b3"),
    ),
    "pb": IndicatorContract(
        tier=IndicatorTier.STRICT,
        basis="security_closing",
        numerator="security_price",
        denominator="closing_attributable_bvps",
        reference_period="reference_date_closing",
        price_basis="analysis.price_basis",
        share_basis="analysis.share_count_basis",
        provenance=("cvm", "b3"),
    ),
    "company_pe": IndicatorContract(
        tier=IndicatorTier.MARKET_CONVENTION,
        basis="company_market_convention",
        numerator="market_capitalization",
        denominator="attributable_net_income",
        reference_period="view_period",
        price_basis="analysis.price_basis",
        share_basis="listed_classes_outstanding",
        provenance=("cvm", "b3"),
    ),
    "company_pb": IndicatorContract(
        tier=IndicatorTier.MARKET_CONVENTION,
        basis="company_market_convention",
        numerator="market_capitalization",
        denominator="current_attributable_equity",
        reference_period="reference_date_closing",
        price_basis="analysis.price_basis",
        share_basis="listed_classes_outstanding",
        provenance=("cvm", "b3"),
    ),
    "psr": IndicatorContract(
        tier=IndicatorTier.MARKET_CONVENTION,
        basis="company_market_convention",
        numerator="market_capitalization",
        denominator="attributable_revenue",
        reference_period="view_period",
        price_basis="analysis.price_basis",
        share_basis="listed_classes_outstanding",
        provenance=("cvm", "b3"),
    ),
    "price_to_assets": IndicatorContract(
        tier=IndicatorTier.MARKET_CONVENTION,
        basis="company_market_convention",
        numerator="market_capitalization",
        denominator="total_assets",
        reference_period="reference_date_closing",
        price_basis="analysis.price_basis",
        share_basis="listed_classes_outstanding",
        provenance=("cvm", "b3"),
    ),
    "price_to_ebit": IndicatorContract(
        tier=IndicatorTier.MARKET_CONVENTION,
        basis="company_market_convention",
        numerator="market_capitalization",
        denominator="ebit",
        reference_period="view_period",
        price_basis="analysis.price_basis",
        share_basis="listed_classes_outstanding",
        provenance=("cvm", "b3"),
    ),
    "price_to_working_capital": IndicatorContract(
        tier=IndicatorTier.MARKET_CONVENTION,
        basis="company_market_convention",
        numerator="market_capitalization",
        denominator="working_capital",
        reference_period="reference_date_closing",
        price_basis="analysis.price_basis",
        share_basis="listed_classes_outstanding",
        provenance=("cvm", "b3"),
    ),
    "dividend_yield": IndicatorContract(
        tier=IndicatorTier.STRICT,
        basis="security_b3_cash_rights",
        numerator="b3_cash_rights_per_security",
        denominator="security_price",
        reference_period="cash_rights_window",
        price_basis="analysis.price_basis",
        share_basis="b3_cash_rights_per_security",
        provenance=("b3",),
    ),
    "payout_cash_paid_in_period": IndicatorContract(
        tier=IndicatorTier.STRICT,
        basis="company_cvm_period_cash",
        numerator="cvm_dividends_paid",
        denominator="attributable_net_income",
        reference_period="view_period",
        price_basis="not_applicable",
        share_basis="not_applicable",
        provenance=("cvm",),
    ),
    "payout_declared_in_period": IndicatorContract(
        tier=IndicatorTier.STRICT,
        basis="company_cvm_period_declared",
        numerator="cvm_dividends_declared",
        denominator="attributable_net_income",
        reference_period="view_period",
        price_basis="not_applicable",
        share_basis="not_applicable",
        provenance=("cvm",),
    ),
    "company_cash_yield_paid_in_period": IndicatorContract(
        tier=IndicatorTier.MARKET_CONVENTION,
        basis="company_market_convention",
        numerator="cvm_dividends_paid",
        denominator="market_capitalization",
        reference_period="view_period",
        price_basis="analysis.price_basis",
        share_basis="listed_classes_outstanding",
        provenance=("cvm", "b3"),
    ),
    "company_yield_declared_in_period": IndicatorContract(
        tier=IndicatorTier.MARKET_CONVENTION,
        basis="company_market_convention",
        numerator="cvm_dividends_declared",
        denominator="market_capitalization",
        reference_period="view_period",
        price_basis="analysis.price_basis",
        share_basis="listed_classes_outstanding",
        provenance=("cvm", "b3"),
    ),
    "ev_ebitda": IndicatorContract(
        tier=IndicatorTier.MARKET_CONVENTION,
        basis="company_enterprise_value",
        numerator="market_capitalization_plus_net_debt_plus_nci",
        denominator="ebitda",
        reference_period="view_period",
        price_basis="analysis.price_basis",
        share_basis="listed_classes_outstanding",
        provenance=("cvm", "b3"),
    ),
    "ev_ebit": IndicatorContract(
        tier=IndicatorTier.MARKET_CONVENTION,
        basis="company_enterprise_value",
        numerator="market_capitalization_plus_net_debt_plus_nci",
        denominator="ebit",
        reference_period="view_period",
        price_basis="analysis.price_basis",
        share_basis="listed_classes_outstanding",
        provenance=("cvm", "b3"),
    ),
    "price_to_fcf": IndicatorContract(
        tier=IndicatorTier.MARKET_CONVENTION,
        basis="company_market_convention",
        numerator="market_capitalization",
        denominator="free_cash_flow",
        reference_period="view_period",
        price_basis="analysis.price_basis",
        share_basis="listed_classes_outstanding",
        provenance=("cvm", "b3"),
    ),
    "fcf_yield": IndicatorContract(
        tier=IndicatorTier.MARKET_CONVENTION,
        basis="company_market_convention",
        numerator="free_cash_flow",
        denominator="market_capitalization",
        reference_period="view_period",
        price_basis="analysis.price_basis",
        share_basis="listed_classes_outstanding",
        provenance=("cvm", "b3"),
    ),
}


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
    # The name states the tax model rather than presenting the 34% proxy as an
    # issuer-specific after-tax return (ADR 0057).
    roic_statutory: Decimal | None = None
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
    # Market convention fallback: attributable earnings divided by closing
    # outstanding shares. It remains separate from the CPC 41 fields; callers
    # choose it only when the strict result is unavailable.
    eps_basic_market: Decimal | None = None
    bvps: Decimal | None = None  # VPA — book value per share
    # Leverage / liquidity
    net_debt: Decimal | None = None
    cash_equivalents: Decimal | None = None
    current_financial_investments: Decimal | None = None
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
    # Per-security valuation multiples. P/E names its CPC 41 denominator; P/B
    # uses the security's own price and the documented closing BVPS allocation.
    pe_basic: Decimal | None = None
    pe_diluted: Decimal | None = None
    pb: Decimal | None = None
    # Whole-company counterparts retained under an explicit scope. Sibling
    # classes share these because both numerator and denominator cover the firm.
    company_pe: Decimal | None = None
    company_pb: Decimal | None = None
    # Per-security market-convention multiple, paired with ``eps_basic_market``.
    pe_basic_market: Decimal | None = None
    psr: Decimal | None = None  # P/Receita — price / sales
    price_to_assets: Decimal | None = None
    price_to_ebit: Decimal | None = None
    price_to_working_capital: Decimal | None = None
    # B3 cash rights per security over the view's stated ex-date window / the
    # analyzed security price on that view's stated price basis.
    dividend_yield: Decimal | None = None
    # Company-level timing ratios. Neither claims exercise attribution: a
    # post-closing AGM belongs to the period in which the DMPL records the
    # declaration, while DFC follows when cash actually left.
    payout_cash_paid_in_period: Decimal | None = None
    payout_declared_in_period: Decimal | None = None
    company_cash_yield_paid_in_period: Decimal | None = None
    company_yield_declared_in_period: Decimal | None = None
    ev_ebitda: Decimal | None = None
    ev_ebit: Decimal | None = None
    # Free cash flow (CFO − capex)
    fcf: Decimal | None = None  # annualized free cash flow, in absolute reais
    price_to_fcf: Decimal | None = None
    fcf_yield: Decimal | None = None
    # Bank-only ratios (ADR 0058). Each consumes an explicitly scoped pair from a
    # public regulator/issuer disclosure. The CVM structured statements alone do
    # not contain the required average stocks or complete managerial perimeter.
    net_interest_margin: Decimal | None = None  # interest result / avg earning assets
    efficiency_ratio: Decimal | None = None  # full expenses / full operating income
    cost_of_risk: Decimal | None = None  # credit loss / avg credit portfolio
    # Insurance-only underwriting ratios (ADR 0061). Expense inputs are filed as
    # negative values and sign-reversed once by the calculator.
    loss_ratio: Decimal | None = None  # claims / earned premium
    combined_ratio: Decimal | None = None  # claims + acquisition + admin / premium
    # Headline financials (absolute reais, the period's own figure — not
    # annualized). Persisted alongside the ratios so the front-end can chart the
    # per-year evolution of revenue / earnings / dividends, which the ratios alone
    # cannot reconstruct.
    revenue: Decimal | None = None
    net_income: Decimal | None = None  # controllers' slice — pairs with eps
    net_income_total: Decimal | None = None  # consolidated, minority included
    distributions_per_security: Decimal | None = None
    company_distributions_paid_in_period: Decimal | None = None
    company_distributions_declared_in_period: Decimal | None = None
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
    # listed classes (ADR 0014); ``enterprise_value`` is ``cap + net_debt +
    # non_controlling_interests`` so it matches consolidated EBIT/EBITDA;
    # ``shares`` is the filed closing count used by BVPS and market cap; it is not
    # CPC 41's weighted EPS denominator.
    market_cap: Decimal | None = None
    enterprise_value: Decimal | None = None
    non_controlling_interests: Decimal | None = None
    shares: Decimal | None = None
    # Raw-account lineage is output metadata, not an indicator cell. It is
    # intentionally excluded from ``indicator_names`` below.
    source_account_evidence: tuple[SourceAccountEvidence, ...] = ()
    # Strict CPC 41 TTM evidence is a window-level contract rather than one
    # latest-period account snapshot. It is metadata, not an indicator cell.
    cpc41_window_provenance: Cpc41WindowProvenance | None = None
    bank_regulatory_provenance: BankRegulatoryProvenance | None = None
    # Why each null field is null, keyed by the field's name. Only null fields
    # appear; a null field with no entry is unclassified (see ``NullReason``).
    null_reasons: Mapping[str, NullReason] = field(default_factory=dict)


def indicator_names() -> tuple[str, ...]:
    """The names of every indicator field, in declaration order.

    Derived from the dataclass so a new indicator is covered automatically —
    the coverage report (#47) enumerates exactly these, and ``null_reasons`` (the
    attribution map, not an indicator) is excluded.
    """
    return tuple(
        f.name
        for f in fields(Indicators)
        if f.name
        not in {
            "null_reasons",
            "source_account_evidence",
            "cpc41_window_provenance",
            "bank_regulatory_provenance",
        }
    )
