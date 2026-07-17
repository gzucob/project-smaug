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

from smaug.analysis.domain.indicators import NullReason
from smaug.portfolio.domain.sectors import Sector
from smaug.portfolio.domain.share_classes import ShareKind


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
    # Start of the DMPL flow period — same reasoning as the DFC's: the equity
    # movements are filed year-to-date, on their own span.
    dmpl_period_start: date | None = None
    total_assets: Decimal | None = None
    equity: Decimal | None = None  # attributable to controlling shareholders
    net_income: Decimal | None = None  # attributable to controlling shareholders
    # The consolidated totals the controllers' figures above are sliced from —
    # minority interest included (DRE 3.11, BPP 2.03 as filed). Carried alongside
    # because both slices are published numbers answering different questions
    # (ADR 0026): the controllers' slice is what accrues to the listed shares,
    # the total is what the consolidated group earned/owns.
    net_income_total: Decimal | None = None
    equity_total: Decimal | None = None
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
    # Dividends + JCP the parent DECLARED against equity during the period (DMPL
    # 5.04 rows, positive). The paid figure above is the cash that left in the
    # period — often the prior year's profit; the declared figure is the charge
    # the company itself reports payout on (#104). Year-to-date like the DFC,
    # isolated on ``dmpl_period_start``.
    dividends_declared: Decimal | None = None
    # Cash-flow flows (DFC, year-to-date basis — isolated on ``dfc_period_start``).
    cfo: Decimal | None = None  # net cash from operating activities (DFC 6.01)
    capex: Decimal | None = None  # purchases of PP&E + intangibles (positive outflow)
    # Bank-regime lines (ADR 0015/0021). Signed as filed: the CVM records an expense
    # as negative and the mirror does not flip it, so the calculator adds a provision
    # back rather than subtracting it. Read from the parent filing's chart of accounts
    # (ADR 0019), where the loan-loss provision is deducted *inside* the intermediation
    # expenses — which is why ``gross_profit`` (3.03) is already net of it.
    loan_loss_provision: Decimal | None = None  # inside DRE 3.02 (negative)
    fee_income: Decimal | None = None  # DRE 3.04 services rendered
    personnel_expense: Decimal | None = None  # DRE 3.04 payroll (negative)
    admin_expense: Decimal | None = None  # DRE 3.04 other administrative (negative)
    loan_book: Decimal | None = None  # BPA 1.02.04, net of its own provision
    # Insurance-regime lines (ADR 0015), same sign convention. Zero for a filer
    # that holds insurers rather than underwriting itself (BBSE3).
    earned_premium: Decimal | None = None  # DRE 3.01.01 "Receitas com Seguros"
    claims_incurred: Decimal | None = None  # DRE 3.02.01 (negative)
    # Null-cause provenance (#30). ``filed_regime`` is what the mapper detected
    # in the statements themselves (None = undetected); ``unmapped_fields`` names
    # the fields above that the mapper deliberately never read for this filer, so
    # the calculator can tell "we skipped it" apart from "the filing has no such
    # line".
    filed_regime: AccountingRegime | None = None
    unmapped_fields: frozenset[str] = frozenset()


@dataclass(frozen=True)
class MarketData:
    """Market-side inputs for one view: the ticker's price and its company's cap.

    ``price`` is the analyzed ticker's own quote (a unit's price is the bundle's).
    ``market_cap`` is the whole company — the sum over its listed share classes
    (ADR 0014), so for a dual-class ticker it is *not* ``price × shares``.
    ``shares`` is the filed total, the denominator of the per-share indicators
    only. ``cap_null_reason`` carries which input the use case was missing when
    it could not build the cap, since a null cap alone cannot say whether a class
    price or a class share count was the one that went missing.
    """

    price: Decimal | None = None
    market_cap: Decimal | None = None
    shares: Decimal | None = None
    cap_null_reason: NullReason | None = None


@dataclass(frozen=True)
class ShareCounts:
    """The shares a company filed for one fiscal year, split by class (CVM's FRE).

    ``total`` is the filer's own total, not a sum we compute — a company can file
    a total that its class lines do not add up to, and the mirror stays faithful.
    """

    common: Decimal | None = None
    preferred: Decimal | None = None
    total: Decimal | None = None

    def of(self, kind: ShareKind) -> Decimal | None:
        """The count filed for one share class."""
        return self.common if kind is ShareKind.COMMON else self.preferred


@dataclass(frozen=True)
class CapitalComposition:
    """The statements' own capital composition — the only filing that names treasury.

    Mirrored from the DFP/ITR ``composicao_capital`` member (ADR 0016). Every count
    here is **at the filer's own scale**: some companies file units and some file
    thousands, and the member carries no column saying which (ADR 0017 resolves it
    against the FRE). ``issued_total`` is that scale's witness — it is the same
    quantity the FRE reports, so the two totals reconcile to the multiple.
    """

    issued_total: Decimal | None = None
    treasury_common: Decimal | None = None
    treasury_preferred: Decimal | None = None
    treasury_total: Decimal | None = None


@dataclass(frozen=True)
class YearPrices:
    """Average share price over one calendar year, both bases.

    ``nominal_avg`` is the mean of daily closes; ``adjusted_avg`` is the mean of
    dividend-adjusted closes (the total-return series the platforms price
    historical multiples on). For heavy payers the two diverge a lot.
    """

    nominal_avg: Decimal | None = None
    adjusted_avg: Decimal | None = None
