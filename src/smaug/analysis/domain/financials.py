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
from smaug.portfolio.domain.share_classes import PerShareClass


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


@dataclass(frozen=True, slots=True)
class Cpc41Disclosure:
    """A class-reconciled CPC 41 result that can support TTM assembly.

    ``basic_base_eps`` and ``diluted_base_eps`` are the issuer's per-underlying
    class results after proving that every listed economic class carries the same
    value. ``security_multiplier`` composes that base result into the analyzed
    security (for example, the number of underlying classes in a unit). This is
    deliberately not a closing share count: the TTM builder can recover the
    issuer's weighted denominator algebraically from the filed attributable
    profit and this reconciled result without inventing an event date.

    Diluted EPS is only eligible when it has the same base as basic EPS. A
    different diluted result carries potential-share terms that are not present in
    the structured mirror and therefore cannot be reconstructed strictly.
    """

    basic_base_eps: Decimal | None = None
    diluted_base_eps: Decimal | None = None
    security_multiplier: Decimal | None = None


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
    # CPC 41 results as filed for this security's own class (or composed unit).
    # They are already reais per security and must never be multiplied by the
    # statement's currency scale. The paired causes distinguish an unavailable
    # filed result from an arithmetic zero.
    eps_basic: Decimal | None = None
    eps_diluted: Decimal | None = None
    eps_basic_null_reason: NullReason | None = None
    eps_diluted_null_reason: NullReason | None = None
    # Only present when the filed class leaves reconcile to one base result and
    # can therefore support strict TTM weighted-denominator assembly.
    cpc41: Cpc41Disclosure | None = None
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
    # Liquidity is split on the CVM taxonomy instead of treating every current
    # financial investment as cash. CPC 03 requires immediate convertibility and
    # insignificant value risk; the filed 1.01.01 line names that eligible set,
    # while 1.01.02 is retained separately rather than silently netted from debt.
    cash_equivalents: Decimal | None = None
    current_financial_investments: Decimal | None = None
    current_assets: Decimal | None = None
    current_liabilities: Decimal | None = None
    total_debt: Decimal | None = None
    # ``total_debt`` is published only when the CVM BPP establishes a complete
    # interest-bearing-liability perimeter. This paired cause distinguishes an
    # evidenced zero from an absent/ambiguous debt disclosure (ADR 0059).
    debt_coverage_null_reason: NullReason | None = None
    dividends_paid: Decimal | None = None  # dividends + JCP paid to controllers
    # Dividends + JCP the parent DECLARED against equity during the period (DMPL
    # 5.04 rows, positive). The paid figure above is the cash that left in the
    # period — often the prior year's profit; the declared figure is the charge
    # the company recorded during this period, without proving which exercise
    # generated it (ADR 0055). Year-to-date like the DFC, isolated on
    # ``dmpl_period_start``.
    dividends_declared: Decimal | None = None
    # Cash-flow flows (DFC, year-to-date basis — isolated on ``dfc_period_start``).
    cfo: Decimal | None = None  # net cash from operating activities (DFC 6.01)
    capex: Decimal | None = None  # purchases of PP&E + intangibles (positive outflow)
    # Bank-regime CVM lines retained as faithful statement facts. Signed as filed:
    # expenses are negative. They are not enough to reconstruct the bank ratios —
    # the structured filing has neither the issuer-defined managerial adjustments
    # nor the required average stocks (ADR 0058).
    loan_loss_provision: Decimal | None = None  # inside DRE 3.02 (negative)
    fee_income: Decimal | None = None  # DRE 3.04 services rendered
    personnel_expense: Decimal | None = None  # DRE 3.04 payroll (negative)
    admin_expense: Decimal | None = None  # DRE 3.04 other administrative (negative)
    loan_book: Decimal | None = None  # BPA 1.02.04, net of its own provision
    # Regulator/issuer-aligned bank-ratio inputs (ADR 0058). These are paired,
    # period-consistent values from one explicitly scoped public disclosure — not
    # aliases for the CVM lines above. Expense inputs are normalized as positive
    # magnitudes, and the two flow numerators are already annualized on the day-
    # count basis declared by their source. ``average_*`` means the average basis
    # declared by that source; a closing balance must never be substituted. The
    # current CVM-only provider leaves all six null and names why via
    # ``bank_ratio_null_reason``.
    bank_interest_result_annualized: Decimal | None = None
    average_earning_assets: Decimal | None = None
    bank_efficiency_expenses: Decimal | None = None
    bank_efficiency_income: Decimal | None = None
    credit_loss_expense_annualized: Decimal | None = None
    average_credit_portfolio: Decimal | None = None
    bank_ratio_null_reason: NullReason | None = None
    # Insurance-regime underwriting lines (ADR 0061), same sign convention:
    # expenses are negative. The pre-IFRS-17 CVM chart separates all four; the
    # current chart does not, so its aggregates are never substituted for these
    # components. Zero remains a value for a holding that files the line but does
    # not underwrite itself; an absent component remains ``None``.
    earned_premium: Decimal | None = None
    claims_incurred: Decimal | None = None
    acquisition_costs: Decimal | None = None
    insurance_admin_expenses: Decimal | None = None
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
    ``shares`` is the filed closing total, used by stock-at-an-instant measures
    such as BVPS; CPC 41 earnings per share carries its own weighted denominator
    inside the issuer's filed basic/diluted result. The two null-reason fields
    carry which upstream input was missing when
    a denominator could not be built: a null number alone cannot distinguish an
    absent filing from an unreadable unit composition or class price.
    """

    price: Decimal | None = None
    market_cap: Decimal | None = None
    shares: Decimal | None = None
    # B3 cash rights whose ex dates fall inside this view's explicit window,
    # summed per analyzed security on the same restated share base as ``price``.
    cash_distributions: Decimal | None = None
    cap_null_reason: NullReason | None = None
    shares_null_reason: NullReason | None = None
    cash_distributions_null_reason: NullReason | None = None


@dataclass(frozen=True)
class ShareCounts:
    """The shares a company filed for one fiscal year, split by class (CVM's FRE).

    ``total`` is the filer's own total, not a sum we compute — a company can file
    a total that its class lines do not add up to, and the mirror stays faithful.
    """

    common: Decimal | None = None
    preferred: Decimal | None = None
    total: Decimal | None = None
    # FRE's class ledger splits the aggregate preferred count into named
    # subclasses. ``preferred_other`` preserves every named class outside A/B so
    # generic PN is derived only from the unclassified remainder (#72).
    preferred_a: Decimal | None = None
    preferred_b: Decimal | None = None
    preferred_other: Decimal | None = None

    def of(self, per_share_class: PerShareClass) -> Decimal | None:
        """The filed count that belongs to one listed ON/PN/PNA/PNB class."""
        if per_share_class is PerShareClass.ORDINARY:
            return self.common
        if per_share_class is PerShareClass.PREFERRED_A:
            return self.preferred_a
        if per_share_class is PerShareClass.PREFERRED_B:
            return self.preferred_b
        if self.preferred is None:
            return None
        subclasses = tuple(
            count
            for count in (
                self.preferred_a,
                self.preferred_b,
                self.preferred_other,
            )
            if count is not None
        )
        if not subclasses:
            return self.preferred
        plain = self.preferred - sum(subclasses, Decimal(0))
        return plain if plain > 0 else None


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


@dataclass(frozen=True, slots=True)
class SessionClose:
    """What one trading code closed at on one session, as traded.

    The individual closes matter — and not only their yearly mean — because a
    corporate action lands on a *day*: the sessions before it are quoted on the
    old share base and the ones after it on the new, so restating the year's
    average is not the same operation as restating each session and averaging
    (ADR 0033). Built in bulk, hence ``slots``.
    """

    session: date
    close: Decimal


@dataclass(frozen=True)
class YearPrices:
    """Closing and average share prices over one calendar year.

    ``nominal_avg`` is the mean of daily closes; ``adjusted_avg`` is the mean of
    dividend-adjusted closes (the total-return series the platforms price
    historical multiples on). For heavy payers the two diverge a lot. ``closing``
    is the last available B3 close in the year, and ``closing_session`` is its
    exact session. Point-in-time valuation uses that pair; it never multiplies an
    annual average by a closing share count.

    ``null_reason`` explains an *empty* result (both averages ``None``):
    ``PRICE_SYMBOL_NOT_FOUND`` when the source rejected the symbol itself (a
    delisted/renamed ticker — #64), ``None`` for a plain gap (the symbol is known
    but had no trading in the window). It lets a fallback chain tell "nobody knows
    this symbol" apart from "this source just had no data for the year".
    """

    nominal_avg: Decimal | None = None
    adjusted_avg: Decimal | None = None
    closing: Decimal | None = None
    closing_session: date | None = None
    null_reason: NullReason | None = None
