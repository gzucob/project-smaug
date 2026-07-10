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
from enum import StrEnum

from smaug.portfolio.domain.sectors import Sector


class AccountingRegime(StrEnum):
    """The statement schema a company actually files under.

    Not the same thing as its ``Sector``: the regime is read off the filing
    itself (banks put equity at 2.07 and open the DRE with financial
    intermediation; the corporate schema opens with 3.01 "Receita de Venda"),
    and a filer can use a regime other than the one its sector predicts —
    CXSE3 is an insurer by sector but files as a holding (ADR 0006).
    """

    BANK = "bank"
    INSURANCE = "insurance"
    CORPORATE = "corporate"


# What each sector predicts the filer's regime to be. A mismatch with the
# detected ``filed_regime`` is the "unexpected regime" null cause (#30).
_REGIME_BY_SECTOR: dict[Sector, AccountingRegime] = {
    Sector.BANK: AccountingRegime.BANK,
    Sector.INSURER: AccountingRegime.INSURANCE,
}


def expected_regime(sector: Sector) -> AccountingRegime:
    """The accounting regime ``sector`` predicts (corporate unless financial)."""
    return _REGIME_BY_SECTOR.get(sector, AccountingRegime.CORPORATE)


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
    # Cash-flow flows (DFC, year-to-date basis — isolated on ``dfc_period_start``).
    cfo: Decimal | None = None  # net cash from operating activities (DFC 6.01)
    capex: Decimal | None = None  # purchases of PP&E + intangibles (positive outflow)
    # Null-cause provenance (#30). ``filed_regime`` is what the mapper detected
    # in the statements themselves (None = undetected); ``unmapped_fields`` names
    # the fields above that the mapper deliberately never read for this filer, so
    # the calculator can tell "we skipped it" apart from "the filing has no such
    # line".
    filed_regime: AccountingRegime | None = None
    unmapped_fields: frozenset[str] = frozenset()


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
