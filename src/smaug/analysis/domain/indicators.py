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
    * ``MISSING_PRICE`` / ``MISSING_SHARE_COUNT`` / ``MISSING_PRIOR_PERIOD`` —
      an upstream input from another source is missing (brapi price, FRE share
      count, the prior year's ingestion), split so a report can say *which*.
    * ``UNEXPECTED_REGIME`` — the company files under a regime other than the
      one its sector predicts (CXSE3 declares as a holding, not an insurer),
      so every regime-driven null is neither inapplicable nor unmapped.

    A null with no recorded reason is *unclassified* — a reportable status of
    its own (#47), e.g. a zero denominator.
    """

    INAPPLICABLE_REGIME = "inapplicable_regime"
    SOURCE_ACCOUNT_UNMAPPED = "source_account_unmapped"
    SOURCE_ACCOUNT_ABSENT = "source_account_absent"
    MISSING_PRICE = "missing_price"
    MISSING_SHARE_COUNT = "missing_share_count"
    MISSING_PRIOR_PERIOD = "missing_prior_period"
    UNEXPECTED_REGIME = "unexpected_regime"


@dataclass(frozen=True)
class Indicators:
    """Fundamental + market indicators for one ticker at one point in time."""

    # Profitability
    roe: Decimal | None = None
    roa: Decimal | None = None
    roic: Decimal | None = None  # NOPAT (EBIT·(1−tax)) / invested capital
    net_margin: Decimal | None = None
    gross_margin: Decimal | None = None
    ebit_margin: Decimal | None = None
    ebitda_margin: Decimal | None = None
    asset_turnover: Decimal | None = None  # revenue / total assets
    # Per share
    eps: Decimal | None = None  # LPA — earnings per share
    bvps: Decimal | None = None  # VPA — book value per share
    # Leverage / liquidity
    net_debt: Decimal | None = None
    net_debt_to_ebitda: Decimal | None = None
    debt_to_equity: Decimal | None = None  # gross debt / equity
    liabilities_to_assets: Decimal | None = None  # (assets − equity) / assets
    current_ratio: Decimal | None = None
    # Growth (needs a prior comparable period)
    revenue_growth: Decimal | None = None
    net_income_growth: Decimal | None = None
    # Market multiples
    pe: Decimal | None = None
    pb: Decimal | None = None
    psr: Decimal | None = None  # P/Receita — price / sales
    price_to_assets: Decimal | None = None
    price_to_ebit: Decimal | None = None
    price_to_working_capital: Decimal | None = None
    payout: Decimal | None = None  # dividends paid / net income
    dividend_yield: Decimal | None = None
    ev_ebitda: Decimal | None = None
    # Free cash flow (CFO − capex)
    fcf: Decimal | None = None  # annualized free cash flow, in absolute reais
    price_to_fcf: Decimal | None = None
    fcf_yield: Decimal | None = None
    # Headline financials (absolute reais, the period's own figure — not
    # annualized). Persisted alongside the ratios so the front-end can chart the
    # per-year evolution of revenue / earnings / dividends, which the ratios alone
    # cannot reconstruct.
    revenue: Decimal | None = None
    net_income: Decimal | None = None
    dividends: Decimal | None = None
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
