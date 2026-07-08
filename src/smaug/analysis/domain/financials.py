"""Standardized financial inputs for indicator calculation (Phase 2 domain).

These are the *normalized* line items the calculator needs, extracted from the
raw CVM mirror by the infrastructure mapper. Kept sector-tagged and period-tagged
so the calculator can annualize flows and skip inapplicable ratios. Every line is
optional: what a bank files differs from a utility, and a missing input yields a
``None`` indicator, never a wrong one.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from smaug.portfolio.domain.sectors import Sector


@dataclass(frozen=True)
class StandardizedFinancials:
    """One period's normalized accounts for a ticker."""

    reference_date: date  # end of the period (DRE/DFC span, or balance instant)
    sector: Sector
    period_start: date | None = None  # start of the DRE flow period, when known
    # Start of the DFC flow period. Tracked separately because the CVM cash-flow
    # statement is filed year-to-date even when the DRE comes as isolated
    # quarters — so DFC-sourced flows (D&A, dividends) must be isolated on their
    # own span, not the DRE's.
    dfc_period_start: date | None = None
    total_assets: Decimal | None = None
    equity: Decimal | None = None  # attributable to controlling shareholders
    net_income: Decimal | None = None  # attributable to controlling shareholders
    revenue: Decimal | None = None
    gross_profit: Decimal | None = None
    ebit: Decimal | None = None
    ebitda: Decimal | None = None
    dep_amort: Decimal | None = None
    cash: Decimal | None = None
    current_assets: Decimal | None = None
    current_liabilities: Decimal | None = None
    total_debt: Decimal | None = None
    dividends_paid: Decimal | None = None  # dividends + JCP paid to controllers


@dataclass(frozen=True)
class MarketData:
    """Market-side inputs (from brapi's quote): price and derived aggregates."""

    price: Decimal | None = None
    market_cap: Decimal | None = None
    shares: Decimal | None = None


@dataclass(frozen=True)
class YearPrices:
    """Average share price over one calendar year, both bases.

    ``nominal_avg`` is the mean of daily closes; ``adjusted_avg`` is the mean of
    dividend-adjusted closes (the total-return series the platforms price
    historical multiples on). For heavy payers the two diverge a lot.
    """

    nominal_avg: Decimal | None = None
    adjusted_avg: Decimal | None = None
