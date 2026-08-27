"""Indicator calculator: annualization, sector awareness, growth (pure, no I/O)."""

from dataclasses import replace
from datetime import date
from decimal import Decimal

from smaug.analysis.domain.calculator import compute
from smaug.analysis.domain.financials import (
    AccountingRegime,
    BankRegulatoryProvenance,
    InsuranceUnderwritingEvidence,
    InsuranceUnderwritingStatus,
    MarketData,
    StandardizedFinancials,
)
from smaug.analysis.domain.indicators import NullReason
from smaug.portfolio.domain.sectors import Sector

_Q3 = date(2024, 9, 30)  # YTD 9 months -> annualization factor 12/9


def _nonfinancial() -> StandardizedFinancials:
    return StandardizedFinancials(
        reference_date=_Q3,
        sector=Sector.COMMODITY,
        total_assets=Decimal(12000),
        equity=Decimal(6000),  # controllers'; the group's is 6600 — 600 is minority
        equity_total=Decimal(6600),
        net_income=Decimal(900),  # annualized -> 1200
        eps_basic=Decimal("1.50"),
        eps_diluted=Decimal("1.40"),
        revenue=Decimal(3000),  # annualized -> 4000
        gross_profit=Decimal(1500),
        ebit=Decimal(900),  # annualized -> 1200
        ebitda=Decimal(1200),  # annualized -> 1600
        cash_equivalents=Decimal(500),
        current_financial_investments=Decimal(400),
        current_assets=Decimal(4000),
        current_liabilities=Decimal(2000),
        total_debt=Decimal(2000),
        dividends_paid=Decimal(600),  # trailing payout, used for DY
        cfo=Decimal(1000),
        capex=Decimal(100),  # FCF period = 900 -> annualized 1200
    )


def test_nonfinancial_computes_all_indicators() -> None:
    previous = StandardizedFinancials(
        reference_date=date(2023, 9, 30),
        sector=Sector.COMMODITY,
        revenue=Decimal(2400),
        net_income=Decimal(750),
    )
    market = MarketData(
        price=Decimal(12),
        market_cap=Decimal(12000),
        shares=Decimal(600),
        cash_distributions=Decimal("0.60"),
    )

    ind = compute(_nonfinancial(), previous, market)

    assert ind.roe == Decimal("0.2")  # annualized 1200 / 6000
    assert ind.roa == Decimal("0.1")  # 1200 / 12000
    assert ind.roic_statutory == Decimal(792) / Decimal(8100)
    assert ind.net_margin == Decimal("0.3")  # 900 / 3000 (period ratio)
    assert ind.gross_margin == Decimal("0.5")
    assert ind.ebit_margin == Decimal("0.3")  # 900 / 3000 (period ratio)
    assert ind.ebitda_margin == Decimal("0.4")
    assert ind.asset_turnover == Decimal(4000) / Decimal(12000)  # annual rev / assets
    assert ind.eps == Decimal("1.50")  # filed CPC 41 basic result, not annualized
    assert ind.eps_basic == Decimal("1.50")
    assert ind.eps_diluted == Decimal("1.40")
    assert ind.bvps == Decimal(10)  # 6000 / 600 shares
    assert ind.net_debt == Decimal(1500)  # 2000 - 500
    assert ind.cash_equivalents == Decimal(500)
    assert ind.current_financial_investments == Decimal(400)
    assert ind.net_debt_to_ebitda == Decimal("0.9375")  # 1500 / 1600
    assert ind.net_debt_to_ebit == Decimal("1.25")  # 1500 / 1200 annual EBIT
    assert ind.net_debt_to_equity == Decimal("0.25")  # 1500 / 6000
    assert ind.debt_to_equity == Decimal(2000) / Decimal(6000)  # gross debt / equity
    # ADR 0029: the two sit on different slices and are NOT complements. Third-party
    # capital comes off the CONSOLIDATED equity (minority interest is equity, not
    # debt); the equity share is the controllers'. What they leave between them —
    # here 5 pp — is the minority's 600.
    assert ind.liabilities_to_assets == Decimal("0.45")  # (12000 - 6600) / 12000
    assert ind.equity_to_assets == Decimal("0.5")  # 6000 / 12000, controllers'
    assert ind.current_ratio == Decimal(2)
    assert ind.revenue_growth == Decimal("0.25")
    assert ind.net_income_growth == Decimal("0.2")
    assert ind.pe_basic == Decimal(8)  # paper price 12 / filed basic EPS 1.50
    assert ind.pe_diluted == Decimal(12) / Decimal("1.40")
    assert ind.eps_basic_market == Decimal(2)  # 1200 / 600 closing shares
    assert ind.pe_basic_market == Decimal(6)  # 12 / estimated EPS 2
    assert ind.pb == Decimal("1.2")  # paper price 12 / closing BVPS 10
    assert ind.company_pe == Decimal(10)  # company cap 12000 / annual profit 1200
    assert ind.company_pb == Decimal(2)  # company cap 12000 / equity 6000
    assert ind.psr == Decimal(3)  # 12000 / 4000 annual revenue
    assert ind.price_to_assets == Decimal(1)  # 12000 / 12000
    assert ind.price_to_ebit == Decimal(10)  # 12000 / 1200 annual EBIT
    assert ind.price_to_working_capital == Decimal(6)  # 12000 / (4000 - 2000)
    assert ind.payout_cash_paid_in_period == Decimal(600) / Decimal(900)
    assert ind.dividend_yield == Decimal("0.05")  # R$ 0.60 / paper price R$ 12
    assert ind.company_cash_yield_paid_in_period == Decimal("0.05")
    # Consolidated EBIT/EBITDA include the minority-owned operations, so EV adds
    # the R$600 non-controlling interest to cap + net debt (ADR 0057).
    assert ind.non_controlling_interests == Decimal(600)
    assert ind.enterprise_value == Decimal(14100)
    assert ind.ev_ebitda == Decimal(14100) / Decimal(1600)
    assert ind.ev_ebit == Decimal("11.75")
    assert ind.fcf == Decimal(1200)  # annualized (1000 - 100)
    assert ind.price_to_fcf == Decimal(10)  # 12000 / 1200
    assert ind.fcf_yield == Decimal("0.1")  # 1200 / 12000
    # Headline financials passed through unchanged (the period's own figure).
    assert ind.revenue == Decimal(3000)
    assert ind.net_income == Decimal(900)
    assert ind.distributions_per_security == Decimal("0.60")
    assert ind.company_distributions_paid_in_period == Decimal(600)


def test_total_slice_variants_pair_slice_with_slice() -> None:
    # ADR 0026: the `_total` variants divide the consolidated result (minority
    # included) by the consolidated denominator; the bare names keep the
    # controllers' slice on both sides. Neither mixes.
    financials = replace(
        _nonfinancial(),
        net_income_total=Decimal(1080),  # annualized -> 1440
        equity_total=Decimal(7200),
    )
    market = MarketData(
        price=Decimal(12), market_cap=Decimal(12000), shares=Decimal(600)
    )

    ind = compute(financials, None, market)

    assert ind.roe == Decimal("0.2")  # controllers': 1200 / 6000
    assert ind.roe_total == Decimal("0.2")  # total/total: 1440 / 7200
    assert ind.roa_total == Decimal("0.12")  # 1440 / 12000
    assert ind.net_margin == Decimal("0.3")  # 900 / 3000, period ratio
    assert ind.net_margin_total == Decimal("0.36")  # 1080 / 3000
    assert ind.net_income_total == Decimal(1080)  # headline: as filed
    # CPC 41 is already filed on the controllers' class-specific slice.
    assert ind.eps == Decimal("1.50")


def test_total_slice_null_is_blamed_on_its_own_account() -> None:
    financials = replace(_nonfinancial(), net_income_total=None, equity_total=None)
    ind = compute(financials, None, MarketData())

    assert ind.roe_total is None
    assert ind.net_margin_total is None
    assert ind.null_reasons["roe_total"] is NullReason.SOURCE_ACCOUNT_ABSENT
    assert ind.null_reasons["net_margin_total"] is NullReason.SOURCE_ACCOUNT_ABSENT


def test_closed_year_leaves_annualization_a_no_op() -> None:
    # A December reference date is a full 12-month period, so annualizing (×12/12)
    # must leave the flows untouched — this is what makes the DFP closed-year view
    # correct without any calculator change.
    closed = StandardizedFinancials(
        reference_date=date(2024, 12, 31),
        sector=Sector.COMMODITY,
        equity=Decimal(6000),
        net_income=Decimal(1200),  # already annual: must NOT be scaled up
        revenue=Decimal(4000),
    )

    ind = compute(closed, None, MarketData(market_cap=Decimal(12000)))

    assert ind.roe == Decimal("0.2")  # 1200 / 6000, no 12/12 inflation
    assert ind.net_margin == Decimal("0.3")  # 1200 / 4000
    assert ind.company_pe == Decimal(10)  # 12000 / 1200


# The one line the CVM mapper still skips for a financial-regime filer — mirrors
# mongo_fundamentals._FINANCIAL_UNMAPPED_FIELDS, inlined here so the domain test
# stays free of infrastructure imports.
_FINANCIAL_UNMAPPED = frozenset({"dep_amort", "ebitda"})
_BANK_UNMAPPED = _FINANCIAL_UNMAPPED | frozenset({"current_financial_investments"})


def _bank_regulatory_provenance() -> BankRegulatoryProvenance:
    return BankRegulatoryProvenance(
        source="issuer_public_performance_analysis",
        period_start=date(2024, 1, 1),
        period_end=date(2024, 12, 31),
        perimeter="consolidated",
        averaging_method="arithmetic_mean_month_end",
        basis="issuer_defined_annualized_disclosure",
        available_inputs=frozenset(
            {
                "bank_interest_result_annualized",
                "average_earning_assets",
                "bank_efficiency_expenses",
                "bank_efficiency_income",
                "credit_loss_expense_annualized",
                "average_credit_portfolio",
            }
        ),
    )


def _mapped_bank() -> StandardizedFinancials:
    """A bank as the CVM mapper actually builds it (ADR 0058).

    It carries the faithful CVM lines, but never calls 3.05 EBIT and never turns
    closing/partial accounts into regulatory ratios. A bank files no debt line or
    current/non-current split either, so those stay ``None`` at the source.
    """
    return StandardizedFinancials(
        reference_date=_Q3,
        sector=Sector.BANK,
        total_assets=Decimal(90000),
        equity=Decimal(8000),
        equity_total=Decimal(8000),
        net_income=Decimal(600),  # annualized -> 800
        net_income_total=Decimal(600),
        eps_basic=Decimal("1.25"),
        eps_diluted=Decimal("1.20"),
        revenue=Decimal(3000),
        gross_profit=Decimal(1200),  # 3.03 — net interest income
        cash_equivalents=Decimal(5000),
        cfo=Decimal(450),  # annualized -> 600
        capex=Decimal(150),  # annualized -> 200
        filed_regime=AccountingRegime.BANK,
        bank_ratio_null_reason=NullReason.MISSING_REGULATORY_DISCLOSURE,
        unmapped_fields=_BANK_UNMAPPED,
    )


def test_bank_computes_the_ratios_its_schema_supports() -> None:
    ind = compute(
        _mapped_bank(),
        None,
        MarketData(price=Decimal(10), market_cap=Decimal(8000), shares=Decimal(800)),
    )

    assert ind.roe == Decimal("0.1")  # 800 / 8000
    assert ind.net_margin == Decimal("0.2")  # 600 / 3000
    assert ind.pe_basic == Decimal(8)  # paper price 10 / filed EPS 1.25
    assert ind.pb == Decimal(1)  # paper price 10 / BVPS 10
    assert ind.company_pe == Decimal(10)  # company cap 8000 / profit 800
    assert ind.company_pb == Decimal(1)
    # The filed intermediation result still supports the generic gross-margin
    # view. PBT is never mislabeled EBIT, and CFO-CAPEX is not bank free cash flow.
    assert ind.gross_margin == Decimal("0.4")  # 1200 / 3000 — the spread
    for name in (
        "ebit_margin",
        "ebit_cagr_5y",
        "price_to_ebit",
        "fcf",
        "price_to_fcf",
        "fcf_yield",
    ):
        assert getattr(ind, name) is None
        assert ind.null_reasons[name] is NullReason.INAPPLICABLE_REGIME
    # Unbuildable from a bank's schema — no debt line, no current/non-current split:
    assert ind.net_debt is None
    assert ind.net_debt_to_ebitda is None
    assert ind.debt_to_equity is None
    assert ind.ev_ebitda is None
    assert ind.ebitda_margin is None
    assert ind.roic_statutory is None
    assert ind.current_ratio is None
    assert ind.price_to_working_capital is None
    # No prior period -> no growth
    assert ind.revenue_growth is None


def test_bbas3_ratios_reconcile_to_the_2024_issuer_disclosure() -> None:
    # Banco do Brasil 4T24, tables 21/26/37/48. The source explicitly defines
    # spread as MFB / average earning assets (monthly closing-balance mean),
    # efficiency as full administrative expense / full operating income, and
    # credit risk expense against the average credit portfolio.
    # https://ri.bb.com.br/informacoes-financeiras/central-de-resultados/
    bank = replace(
        _mapped_bank(),
        reference_date=date(2024, 12, 31),
        bank_interest_result_annualized=Decimal(103_944),
        average_earning_assets=Decimal(2_137_682),
        bank_efficiency_expenses=Decimal(36_998),
        bank_efficiency_income=Decimal(144_688),
        credit_loss_expense_annualized=Decimal(41_422),
        average_credit_portfolio=Decimal(1_020_119),
        bank_ratio_null_reason=None,
        bank_regulatory_provenance=_bank_regulatory_provenance(),
    )

    ind = compute(bank, None, MarketData(market_cap=Decimal(8000)))

    assert ind.net_interest_margin == Decimal(103_944) / Decimal(2_137_682)
    assert ind.efficiency_ratio == Decimal(36_998) / Decimal(144_688)
    assert ind.cost_of_risk == Decimal(41_422) / Decimal(1_020_119)
    assert ind.net_interest_margin.quantize(Decimal("0.001")) == Decimal("0.049")
    assert ind.efficiency_ratio.quantize(Decimal("0.001")) == Decimal("0.256")
    assert ind.cost_of_risk.quantize(Decimal("0.001")) == Decimal("0.041")


def test_bbdc4_ratios_reconcile_to_the_4t24_issuer_disclosure() -> None:
    # Bradesco 4T24. The paired values are normalized to the annualized bases the
    # issuer publishes: 8.4% client margin, 53.2% quarterly IEO and 3.0% credit
    # cost. The efficiency denominator includes margin, services, insurance,
    # associates and taxes exactly as the report's footnote defines it.
    # https://pessoajuridica.bradesco/assets/classic/pdf/
    # bradesco-4T24-apresentacao-de-resultados-imprensa.pdf
    average_earning_assets = Decimal(790_286)
    average_credit_portfolio = Decimal("994666.6666666666666666666667")
    bank = replace(
        _mapped_bank(),
        reference_date=date(2024, 12, 31),
        period_start=date(2024, 10, 1),
        bank_interest_result_annualized=(average_earning_assets * Decimal("0.084")),
        average_earning_assets=average_earning_assets,
        bank_efficiency_expenses=Decimal(16_418),
        bank_efficiency_income=(
            Decimal(16_995)
            + Decimal(10_262)
            + Decimal(5_531)
            + Decimal(90)
            - Decimal(2_031)
        ),
        credit_loss_expense_annualized=Decimal(29_840),
        average_credit_portfolio=average_credit_portfolio,
        bank_ratio_null_reason=None,
        bank_regulatory_provenance=_bank_regulatory_provenance(),
    )

    ind = compute(bank, None, MarketData(market_cap=Decimal(8000)))

    assert ind.net_interest_margin == Decimal("0.084")
    assert ind.efficiency_ratio is not None
    assert ind.efficiency_ratio.quantize(Decimal("0.001")) == Decimal("0.532")
    assert ind.cost_of_risk is not None
    assert ind.cost_of_risk.quantize(Decimal("0.001")) == Decimal("0.030")


def test_the_bank_ratios_are_inapplicable_to_everyone_else() -> None:
    # A company that sells goods has no spread, no loan book and no payroll measured
    # against a spread. The null is a verdict of the regime, not a missing input.
    ind = compute(_nonfinancial(), None, MarketData(market_cap=Decimal(12000)))

    for name in ("net_interest_margin", "efficiency_ratio", "cost_of_risk"):
        assert getattr(ind, name) is None
        assert ind.null_reasons[name] is NullReason.INAPPLICABLE_REGIME


def _irbr3_2022() -> StandardizedFinancials:
    """IRB's official CVM DFP 2022 underwriting inputs (R$ thousand)."""
    return StandardizedFinancials(
        reference_date=date(2022, 12, 31),
        sector=Sector.INSURER,
        filed_regime=AccountingRegime.INSURANCE,
        earned_premium=Decimal(7_021_200),
        claims_incurred=Decimal(-6_911_514),
        acquisition_costs=Decimal(-255_606),
        insurance_admin_expenses=Decimal(-421_237),
    )


def test_irbr3_underwriting_ratios_reconcile_to_the_2022_cvm_filing() -> None:
    ind = compute(_irbr3_2022(), None, MarketData())

    assert ind.loss_ratio == Decimal(6_911_514) / Decimal(7_021_200)
    assert ind.combined_ratio == (
        Decimal(6_911_514) + Decimal(255_606) + Decimal(421_237)
    ) / Decimal(7_021_200)
    assert ind.loss_ratio.quantize(Decimal("0.001")) == Decimal("0.984")
    assert ind.combined_ratio.quantize(Decimal("0.001")) == Decimal("1.081")


def test_insurer_ratios_name_missing_components_and_zero_premium() -> None:
    missing = compute(
        replace(_irbr3_2022(), acquisition_costs=None), None, MarketData()
    )
    zero_premium = compute(
        replace(_irbr3_2022(), earned_premium=Decimal(0)), None, MarketData()
    )

    assert missing.loss_ratio is not None
    assert missing.combined_ratio is None
    assert missing.null_reasons["combined_ratio"] is NullReason.SOURCE_ACCOUNT_ABSENT
    assert zero_premium.loss_ratio is None
    assert zero_premium.combined_ratio is None
    assert zero_premium.null_reasons["loss_ratio"] is NullReason.ZERO_DENOMINATOR
    assert zero_premium.null_reasons["combined_ratio"] is NullReason.ZERO_DENOMINATOR


def test_zero_activity_only_suppresses_underwriting_ratios() -> None:
    financials = StandardizedFinancials(
        reference_date=date(2025, 12, 31),
        sector=Sector.INSURER,
        filed_regime=AccountingRegime.INSURANCE,
        revenue=Decimal(1000),
        net_income=Decimal(100),
        equity=Decimal(1000),
        insurance_underwriting_evidence=InsuranceUnderwritingEvidence(
            status=InsuranceUnderwritingStatus.ZERO_ACTIVITY
        ),
    )

    ind = compute(financials, None, MarketData())

    for name in ("loss_ratio", "combined_ratio"):
        assert getattr(ind, name) is None
        assert ind.null_reasons[name] is NullReason.INAPPLICABLE_REGIME
    # The aggregate proof is scoped to insurer-only ratios. It must not hide a
    # generic ratio that the insurance chart still supports.
    assert ind.net_margin == Decimal("0.1")
    assert "net_margin" not in ind.null_reasons


def test_active_ifrs17_insurer_without_legacy_components_stays_source_absent() -> None:
    financials = StandardizedFinancials(
        reference_date=date(2025, 12, 31),
        sector=Sector.INSURER,
        filed_regime=AccountingRegime.INSURANCE,
        revenue=Decimal(1000),
        net_income=Decimal(100),
        equity=Decimal(1000),
        insurance_underwriting_evidence=InsuranceUnderwritingEvidence(
            status=InsuranceUnderwritingStatus.ACTIVE
        ),
    )

    ind = compute(financials, None, MarketData())

    assert ind.loss_ratio is None
    assert ind.combined_ratio is None
    assert ind.null_reasons["loss_ratio"] is NullReason.SOURCE_ACCOUNT_ABSENT
    assert ind.null_reasons["combined_ratio"] is NullReason.SOURCE_ACCOUNT_ABSENT
    assert ind.net_margin == Decimal("0.1")


def test_insurer_expense_reversal_reduces_the_combined_ratio() -> None:
    reversal = compute(
        replace(_irbr3_2022(), insurance_admin_expenses=Decimal(421_237)),
        None,
        MarketData(),
    )

    assert reversal.combined_ratio == (
        Decimal(6_911_514) + Decimal(255_606) - Decimal(421_237)
    ) / Decimal(7_021_200)


def test_insurer_ratios_are_inapplicable_to_other_filing_regimes() -> None:
    for financials in (_nonfinancial(), _mapped_bank()):
        ind = compute(financials, None, MarketData())
        for name in ("loss_ratio", "combined_ratio"):
            assert getattr(ind, name) is None
            assert ind.null_reasons[name] is NullReason.INAPPLICABLE_REGIME


def test_bank_null_reasons_name_each_cause() -> None:
    ind = compute(
        replace(
            _mapped_bank(),
            eps_basic=None,
            eps_diluted=None,
            eps_basic_null_reason=NullReason.MISSING_CPC41_DISCLOSURE,
            eps_diluted_null_reason=NullReason.MISSING_CPC41_DISCLOSURE,
        ),
        None,
        MarketData(market_cap=Decimal(8000)),
    )  # no shares

    # Cause 1 — genuinely meaningless for a bank: it reports Basileia, not net debt
    # / EV-EBITDA, and has no EBITDA (ADR 0010). ADR 0015 adds the three the
    # mapping settled: a bank's balance sheet has no current/non-current split, so
    # its current ratio and P/working-capital are unbuildable, and its statutory
    # ROIC denominator (consolidated equity + net debt) inherits that verdict.
    assert ind.null_reasons["net_debt"] is NullReason.INAPPLICABLE_REGIME
    assert ind.null_reasons["net_debt_to_ebitda"] is NullReason.INAPPLICABLE_REGIME
    assert ind.null_reasons["net_debt_to_ebit"] is NullReason.INAPPLICABLE_REGIME
    assert ind.null_reasons["net_debt_to_equity"] is NullReason.INAPPLICABLE_REGIME
    assert ind.null_reasons["debt_to_equity"] is NullReason.INAPPLICABLE_REGIME
    assert ind.null_reasons["ev_ebitda"] is NullReason.INAPPLICABLE_REGIME
    assert ind.null_reasons["ev_ebit"] is NullReason.INAPPLICABLE_REGIME
    assert ind.null_reasons["ebitda_margin"] is NullReason.INAPPLICABLE_REGIME
    assert ind.null_reasons["roic_statutory"] is NullReason.INAPPLICABLE_REGIME
    assert ind.null_reasons["current_ratio"] is NullReason.INAPPLICABLE_REGIME
    assert ind.null_reasons["price_to_working_capital"] is (
        NullReason.INAPPLICABLE_REGIME
    )
    for name in (
        "ebit_margin",
        "ebit_cagr_5y",
        "price_to_ebit",
        "fcf",
        "price_to_fcf",
        "fcf_yield",
    ):
        assert ind.null_reasons[name] is NullReason.INAPPLICABLE_REGIME
    for name in ("net_interest_margin", "efficiency_ratio", "cost_of_risk"):
        assert ind.null_reasons[name] is (NullReason.MISSING_REGULATORY_DISCLOSURE)
    # Cause 2 — the bank chart cannot isolate a current-only investment bucket;
    # that headline is named unmapped rather than guessed from all financial assets.
    assert ind.null_reasons["current_financial_investments"] is (
        NullReason.SOURCE_ACCOUNT_UNMAPPED
    )
    # Cause 3 — upstream inputs, each named individually:
    assert ind.null_reasons["eps"] is NullReason.MISSING_CPC41_DISCLOSURE
    assert ind.null_reasons["eps_diluted"] is NullReason.MISSING_CPC41_DISCLOSURE
    assert ind.null_reasons["revenue_growth"] is NullReason.MISSING_PRIOR_PERIOD
    # The filing simply has no dividend line — absent, not unmapped:
    assert ind.null_reasons["payout_cash_paid_in_period"] is (
        NullReason.SOURCE_ACCOUNT_ABSENT
    )
    # Computed values never carry a reason:
    assert ind.roe is not None
    assert "roe" not in ind.null_reasons


def test_insurer_null_reasons_split_by_regime() -> None:
    # ADR 0010: an insurer is the near-mirror of a bank — generic operating
    # margins are degenerate under its filed schema, so they are inapplicable.
    # ADR 0015: unlike a bank, it files a corporate-shaped balance sheet, so its
    # current ratio computes; but this filing does not establish a complete debt
    # perimeter. Absence is not evidence of zero (ADR 0059).
    insurer = StandardizedFinancials(
        reference_date=_Q3,
        sector=Sector.INSURER,
        total_assets=Decimal(50000),
        equity=Decimal(9000),
        net_income=Decimal(2500),
        revenue=Decimal(4000),
        ebit=Decimal(3000),  # 3.07 for an insurer, not 3.05
        current_assets=Decimal(6000),
        current_liabilities=Decimal(3000),
        debt_coverage_null_reason=NullReason.INCOMPLETE_DEBT_COVERAGE,
        filed_regime=AccountingRegime.INSURANCE,  # files as its sector predicts
        unmapped_fields=_FINANCIAL_UNMAPPED,
    )
    ind = compute(insurer, None, MarketData(market_cap=Decimal(30000)))

    # Inapplicable for an insurer — the margins:
    assert ind.null_reasons["gross_margin"] is NullReason.INAPPLICABLE_REGIME
    assert ind.null_reasons["ebit_margin"] is NullReason.INAPPLICABLE_REGIME
    assert ind.null_reasons["ebitda_margin"] is NullReason.INAPPLICABLE_REGIME
    # The same debt-evidence failure propagates through every dependent value;
    # neither missing cash nor missing EBITDA may disguise the upstream cause.
    for name in (
        "net_debt",
        "net_debt_to_ebitda",
        "debt_to_equity",
        "enterprise_value",
        "ev_ebitda",
    ):
        assert ind.null_reasons[name] is NullReason.INCOMPLETE_DEBT_COVERAGE
    # Statutory corporate ROIC is inapplicable outright to the insurance regime,
    # so applicability still precedes the incomplete input.
    assert ind.null_reasons["roic_statutory"] is NullReason.INAPPLICABLE_REGIME
    # Its balance sheet *does* carry the current/non-current split a bank lacks:
    assert ind.current_ratio == Decimal(2)  # 6000 / 3000
    assert "current_ratio" not in ind.null_reasons


def test_insurer_incomplete_debt_does_not_publish_cash_as_net_debt() -> None:
    # BBSE3's insurance chart carries cash but no complete borrowing perimeter.
    # The old formula silently replaced the missing debt with zero and published
    # ``-cash``; every value built on that invented zero must now remain null.
    insurer = StandardizedFinancials(
        reference_date=date(2024, 12, 31),
        sector=Sector.INSURER,
        total_assets=Decimal(50000),
        equity=Decimal(9000),
        net_income=Decimal(2500),
        revenue=Decimal(4000),
        ebit=Decimal(3000),
        cash_equivalents=Decimal(8000),
        equity_total=Decimal(9000),
        debt_coverage_null_reason=NullReason.INCOMPLETE_DEBT_COVERAGE,
        filed_regime=AccountingRegime.INSURANCE,
        unmapped_fields=_FINANCIAL_UNMAPPED,
    )
    ind = compute(insurer, None, MarketData(market_cap=Decimal(30000)))

    for name in (
        "net_debt",
        "enterprise_value",
        "net_debt_to_equity",
        "net_debt_to_ebit",
        "ev_ebit",
        "debt_to_equity",
    ):
        assert getattr(ind, name) is None
        assert ind.null_reasons[name] is NullReason.INCOMPLETE_DEBT_COVERAGE
    assert ind.equity_to_assets == Decimal("0.18")  # 9000 / 50000
    assert ind.roic_statutory is None
    assert ind.null_reasons["roic_statutory"] is NullReason.INAPPLICABLE_REGIME


def test_insurer_evidenced_zero_debt_can_publish_net_cash() -> None:
    insurer = StandardizedFinancials(
        reference_date=date(2024, 12, 31),
        sector=Sector.INSURER,
        total_assets=Decimal(50000),
        equity=Decimal(9000),
        equity_total=Decimal(9000),
        ebit=Decimal(3000),
        cash_equivalents=Decimal(8000),
        total_debt=Decimal(0),  # both BPP maturity aggregates explicitly filed zero
        filed_regime=AccountingRegime.INSURANCE,
        unmapped_fields=_FINANCIAL_UNMAPPED,
    )

    ind = compute(insurer, None, MarketData(market_cap=Decimal(30000)))

    assert ind.net_debt == Decimal(-8000)
    assert ind.enterprise_value == Decimal(22000)
    assert ind.net_debt_to_equity == Decimal(-8000) / Decimal(9000)
    assert ind.net_debt_to_ebit == Decimal(-8000) / Decimal(3000)
    assert ind.debt_to_equity == 0
    assert ind.ev_ebit == Decimal(22000) / Decimal(3000)
    # EBITDA itself remains deliberately unmapped for this filing regime.
    assert ind.null_reasons["net_debt_to_ebitda"] is (
        NullReason.SOURCE_ACCOUNT_UNMAPPED
    )


def test_insurer_explicit_debt_uses_the_same_formula_as_a_corporate_filer() -> None:
    insurer = StandardizedFinancials(
        reference_date=date(2024, 12, 31),
        sector=Sector.INSURER,
        equity=Decimal(9000),
        equity_total=Decimal(9000),
        ebit=Decimal(3000),
        cash_equivalents=Decimal(8000),
        total_debt=Decimal(10000),
        filed_regime=AccountingRegime.INSURANCE,
    )

    ind = compute(insurer, None, MarketData(market_cap=Decimal(30000)))

    assert ind.net_debt == Decimal(2000)
    assert ind.enterprise_value == Decimal(32000)
    assert ind.debt_to_equity == Decimal(10000) / Decimal(9000)
    assert ind.ev_ebit == Decimal(32000) / Decimal(3000)


def test_declared_dividend_basis_computes_alongside_the_paid_one() -> None:
    # #104: the declared basis (DMPL charge) and the paid basis (DFC outflow)
    # answer different questions and are published side by side — the same dual
    # pattern ADR 0026 set for the statement slices.
    financials = replace(
        _nonfinancial(),
        dividends_declared=Decimal(450),
        dmpl_period_start=date(2024, 1, 1),
    )
    market = MarketData(
        price=Decimal(12),
        market_cap=Decimal(12000),
        cash_distributions=Decimal("0.60"),
    )

    ind = compute(financials, None, market)

    assert ind.payout_cash_paid_in_period == Decimal(600) / Decimal(900)
    assert ind.payout_declared_in_period == Decimal("0.5")  # 450 / 900
    assert ind.dividend_yield == Decimal("0.05")  # B3 rights / paper price
    assert ind.company_cash_yield_paid_in_period == Decimal("0.05")
    assert ind.company_yield_declared_in_period == Decimal("0.0375")
    assert ind.company_distributions_declared_in_period == Decimal(450)


def test_a_missing_dmpl_row_blames_the_declared_account() -> None:
    ind = compute(_nonfinancial(), None, MarketData(market_cap=Decimal(12000)))

    assert ind.payout_declared_in_period is None
    assert ind.null_reasons["payout_declared_in_period"] is (
        NullReason.SOURCE_ACCOUNT_ABSENT
    )
    assert ind.null_reasons["company_distributions_declared_in_period"] is (
        NullReason.SOURCE_ACCOUNT_ABSENT
    )
    # The paid basis is untouched by the declared one going missing:
    assert ind.payout_cash_paid_in_period is not None


def test_post_closing_agm_is_not_mislabelled_as_exercise_payout() -> None:
    # A 2025 AGM may declare the distribution of 2024 profit. The structured
    # inputs identify when the declaration entered DMPL, not which exercise
    # generated it, so the ratio states its timing instead of guessing.
    financials = replace(
        _nonfinancial(),
        reference_date=date(2025, 12, 31),
        period_start=date(2025, 1, 1),
        dividends_declared=Decimal(450),
        dmpl_period_start=date(2025, 1, 1),
    )

    ind = compute(financials, None, MarketData(market_cap=Decimal(12000)))

    assert ind.payout_declared_in_period == Decimal("0.5")
    assert ind.company_distributions_declared_in_period == Decimal(450)
    assert not hasattr(ind, "payout_declared")


def test_incomplete_debt_coverage_precedes_a_missing_market_input() -> None:
    # EV is suppressed before market arithmetic when its debt perimeter is not
    # established. A missing cap must not hide the more fundamental basis gap.
    insurer = StandardizedFinancials(
        reference_date=date(2024, 12, 31),
        sector=Sector.INSURER,
        equity=Decimal(9000),
        equity_total=Decimal(9000),
        ebit=Decimal(3000),
        cash_equivalents=Decimal(8000),
        debt_coverage_null_reason=NullReason.INCOMPLETE_DEBT_COVERAGE,
        filed_regime=AccountingRegime.INSURANCE,
        unmapped_fields=_FINANCIAL_UNMAPPED,
    )
    ind = compute(insurer, None, MarketData())  # no cap

    assert ind.ev_ebit is None
    assert ind.null_reasons["ev_ebit"] is NullReason.INCOMPLETE_DEBT_COVERAGE
    assert ind.null_reasons["enterprise_value"] is (NullReason.INCOMPLETE_DEBT_COVERAGE)


def test_applicability_follows_the_filed_regime_not_the_sector() -> None:
    # The CXSE3 case (ADR 0006/0020): an insurer by sector that files as a holding.
    # Applicability is a property of the chart of accounts the company *uses*, so
    # this filer is judged as the corporate it files as — the insurer's suppressed
    # margins are not applied to it, and a null it does have gets a filed cause.
    holding = StandardizedFinancials(
        reference_date=date(2024, 12, 31),
        sector=Sector.INSURER,
        total_assets=Decimal(20000),
        equity=Decimal(9000),
        net_income=Decimal(2500),
        revenue=Decimal(4000),
        filed_regime=AccountingRegime.CORPORATE,
        # Filing corporately, it is now *mapped* corporately (ADR 0015), so nothing
        # is deliberately skipped for it — its unmapped set is empty.
        unmapped_fields=frozenset(),
    )
    ind = compute(
        holding, None, MarketData(market_cap=Decimal(30000), shares=Decimal(3000))
    )

    # Not suppressed: the corporate schema it files under supports a gross margin,
    # and this filing simply carries no gross-profit line. That is an absence in the
    # filing, not a verdict of ours — which is the whole difference (#95).
    assert ind.null_reasons["gross_margin"] is NullReason.SOURCE_ACCOUNT_ABSENT
    assert ind.null_reasons["fcf"] is NullReason.SOURCE_ACCOUNT_ABSENT
    for name in ("loss_ratio", "combined_ratio"):
        assert getattr(ind, name) is None
        assert ind.null_reasons[name] is NullReason.INAPPLICABLE_REGIME
    assert ind.net_margin == Decimal("0.625")
    assert ind.roe is not None  # the mapped core still computes


def test_a_filer_is_judged_by_the_regime_it_files_even_when_its_sector_agrees() -> None:
    # The other side of #95: a bank that files as one keeps the bank's inapplicable
    # set, and its null says so — the change is *which* regime is asked, not whether
    # a regime decides.
    bank = StandardizedFinancials(
        reference_date=date(2024, 12, 31),
        sector=Sector.BANK,
        total_assets=Decimal(50000),
        equity=Decimal(8000),
        net_income=Decimal(900),
        revenue=Decimal(4000),
        filed_regime=AccountingRegime.BANK,
    )

    ind = compute(bank, None, MarketData(market_cap=Decimal(30000)))

    assert ind.net_debt is None
    assert ind.null_reasons["net_debt"] is NullReason.INAPPLICABLE_REGIME


def test_scale_figures_are_carried_through_to_the_output() -> None:
    # The calculator persists the market inputs and the consolidated EV bridge so
    # consumers do not have to reconstruct a basis-sensitive value.
    market = MarketData(
        price=Decimal(12), market_cap=Decimal(12000), shares=Decimal(600)
    )

    ind = compute(_nonfinancial(), None, market)

    assert ind.market_cap == Decimal(12000)
    assert ind.shares == Decimal(600)
    # EV = cap + net debt + NCI = 12000 + 1500 + (6600 - 6000).
    assert ind.enterprise_value == Decimal(14100)


def test_enterprise_value_is_inapplicable_for_a_bank() -> None:
    # EV inherits net debt's applicability: a bank has no borrowings line, so both are
    # inapplicable under the regime — named, not silently missing (like net_debt).
    ind = compute(_mapped_bank(), None, MarketData(market_cap=Decimal(8000)))

    assert ind.market_cap == Decimal(8000)  # the cap itself is still meaningful
    assert ind.enterprise_value is None
    assert ind.null_reasons["enterprise_value"] is NullReason.INAPPLICABLE_REGIME


def test_missing_price_nulls_the_market_multiples_with_a_named_cause() -> None:
    # The #42 shape: fundamentals fine, no price -> every cap-based multiple
    # must say "missing price", not go silently null.
    ind = compute(_nonfinancial(), None, MarketData(shares=Decimal(600)))

    assert ind.pe_basic is None
    assert ind.pe_diluted is None
    assert ind.pb is None
    assert ind.null_reasons["pe_basic"] is NullReason.MISSING_PRICE
    assert ind.null_reasons["pe_diluted"] is NullReason.MISSING_PRICE
    assert ind.null_reasons["pb"] is NullReason.MISSING_PRICE
    assert ind.null_reasons["dividend_yield"] is NullReason.MISSING_PRICE
    assert ind.eps is not None  # filed per-share result is independent of price


def test_market_convention_multiples_survive_a_missing_cpc41_share_input() -> None:
    # The strict P/E needs the issuer's CPC 41 weighted-average/class-rights
    # result. Company P/E and both closing-equity P/B variants have independent
    # denominators and must remain available when those filed results are absent.
    financials = replace(
        _nonfinancial(),
        eps_basic=None,
        eps_basic_null_reason=NullReason.MISSING_WEIGHTED_AVERAGE_SHARES,
    )

    ind = compute(
        financials,
        None,
        MarketData(
            price=Decimal(12),
            market_cap=Decimal(12000),
            shares=Decimal(600),
        ),
    )

    assert ind.pe_basic is None
    assert ind.null_reasons["pe_basic"] is NullReason.MISSING_WEIGHTED_AVERAGE_SHARES
    assert ind.eps_basic_market == Decimal(2)
    assert ind.pe_basic_market == Decimal(6)
    assert ind.company_pe == Decimal(10)
    assert ind.pb == Decimal("1.2")
    assert ind.company_pb == Decimal(2)


def test_missing_shares_blames_the_share_count_not_the_price() -> None:
    # The cap sums the company's share classes (ADR 0014), so the use case is the
    # one that knows which input it was missing and hands the reason over. Here
    # the year's price is present and the filed count is not.
    ind = compute(
        _nonfinancial(),
        None,
        MarketData(price=Decimal(6), cap_null_reason=NullReason.MISSING_SHARE_COUNT),
    )

    assert ind.pe_basic == Decimal(4)  # EPS is already filed per security
    assert ind.pe_diluted == Decimal(6) / Decimal("1.40")
    assert ind.company_pe is None
    assert ind.null_reasons["company_pe"] is NullReason.MISSING_SHARE_COUNT
    assert ind.null_reasons["pb"] is NullReason.MISSING_SHARE_COUNT


def test_missing_unit_composition_names_each_per_security_input() -> None:
    ind = compute(
        replace(
            _nonfinancial(),
            eps_basic=None,
            eps_diluted=None,
            eps_basic_null_reason=NullReason.MISSING_ECONOMIC_RIGHTS,
            eps_diluted_null_reason=NullReason.MISSING_ECONOMIC_RIGHTS,
        ),
        None,
        MarketData(shares_null_reason=NullReason.MISSING_UNIT_COMPOSITION),
    )

    assert ind.eps is None
    assert ind.bvps is None
    assert ind.null_reasons["eps"] is NullReason.MISSING_ECONOMIC_RIGHTS
    assert ind.null_reasons["bvps"] is NullReason.MISSING_UNIT_COMPOSITION


def test_a_sibling_class_without_a_quote_blames_the_price() -> None:
    # A dual-class company whose ON class has no quote cannot be capitalized even
    # though the analyzed ticker's own price and share count are both in hand —
    # the null cap blames the missing price, not the shares it does have.
    ind = compute(
        _nonfinancial(),
        None,
        MarketData(
            price=Decimal(6),
            shares=Decimal(600),
            cap_null_reason=NullReason.MISSING_PRICE,
        ),
    )

    assert ind.pe_basic == Decimal(4)
    assert ind.company_pe is None
    assert ind.null_reasons["company_pe"] is NullReason.MISSING_PRICE
    assert ind.eps is not None  # the filed per-share side needs no quote


def test_zero_denominator_null_is_named() -> None:
    # A zero denominator is a known status, not an unclassified null: with every
    # input present, payout = dividends / 0 is the ZERO_DENOMINATOR dead-end (ANL-23).
    zero_income = replace(_nonfinancial(), net_income=Decimal(0))
    ind = compute(
        zero_income,
        None,
        MarketData(price=Decimal(12), market_cap=Decimal(12000)),
    )

    assert ind.payout_cash_paid_in_period is None  # dividends / 0
    assert ind.null_reasons["payout_cash_paid_in_period"] is (
        NullReason.ZERO_DENOMINATOR
    )
    assert ind.company_pe is None
    assert ind.null_reasons["company_pe"] is NullReason.ZERO_DENOMINATOR
    assert ind.pe_basic == Decimal(8)  # CPC 41 EPS remains its own denominator


def _closed_year(year: int, **accounts: Decimal | None) -> StandardizedFinancials:
    """One closed exercise — a full 12 months, so nothing is annualized."""
    return replace(
        _nonfinancial(),
        reference_date=date(year, 12, 31),
        period_start=date(year, 1, 1),
        **accounts,
    )


def _rising_history(values: list[Decimal | None]) -> list[StandardizedFinancials]:
    """Closed exercises ending in 2024, one per value, oldest -> newest."""
    first = 2024 - len(values) + 1
    return [
        _closed_year(first + offset, revenue=value, net_income=value)
        for offset, value in enumerate(values)
    ]


def test_cagr_compounds_between_the_endpoints_five_exercises_apart() -> None:
    # Revenue doubling over the window: 2^(1/5) - 1 = 14.87% a year. Only the two
    # endpoints count, so the noise in between must not move the rate.
    history = _rising_history(
        [
            Decimal(1000),
            Decimal(5000),
            Decimal(200),
            Decimal(3000),
            Decimal(1),
            Decimal(2000),
        ]
    )
    ind = compute(history[-1], history[-2], MarketData(), history)

    assert ind.revenue_cagr_5y is not None
    assert round(float(ind.revenue_cagr_5y), 4) == 0.1487


def test_cagr_is_null_until_the_window_closes() -> None:
    # Five exercises span four years of variation, not five. Shortening the window
    # in silence would make the number mean something other than its name (#144).
    history = _rising_history([Decimal(1000)] * 5)
    ind = compute(history[-1], history[-2], MarketData(), history)

    assert ind.revenue_cagr_5y is None
    assert ind.null_reasons["revenue_cagr_5y"] is NullReason.MISSING_PRIOR_PERIOD


def test_cagr_rejects_a_discontinuous_closed_year_window() -> None:
    history = [
        _closed_year(year, revenue=Decimal(1000), net_income=Decimal(1000))
        for year in (2019, 2020, 2022, 2023, 2024, 2025)
    ]
    ind = compute(history[-1], history[-2], MarketData(), history)

    assert ind.revenue_cagr_5y is None
    assert ind.null_reasons["revenue_cagr_5y"] is NullReason.MISSING_PRIOR_PERIOD


def test_cagr_refuses_a_non_positive_endpoint() -> None:
    # A loss in the base year has no compounded rate: two negative endpoints would
    # report a deepening loss as growth.
    history = _rising_history(
        [
            Decimal(-500),
            Decimal(100),
            Decimal(200),
            Decimal(300),
            Decimal(400),
            Decimal(-900),
        ]
    )
    ind = compute(history[-1], history[-2], MarketData(), history)

    assert ind.net_income_cagr_5y is None
    assert ind.null_reasons["net_income_cagr_5y"] is NullReason.NON_POSITIVE_ENDPOINT


def test_cagr_needs_no_history_to_compute_the_rest() -> None:
    # The default empty history must not break a caller that has no series: every
    # other indicator still computes, and the rates alone go null.
    ind = compute(_nonfinancial(), None, MarketData(market_cap=Decimal(12000)))

    assert ind.revenue_cagr_5y is None
    assert ind.roe is not None


def test_balance_sheet_liabilities_exclude_the_minority_interest() -> None:
    # Minority interest is equity, not third-party capital: the liabilities side
    # subtracts the consolidated equity, not the controllers' slice (#149).
    f = replace(_nonfinancial(), equity=Decimal(6000), equity_total=Decimal(6500))
    ind = compute(f, None, MarketData())

    assert ind.total_assets == Decimal(12000)
    assert ind.total_liabilities == Decimal(5500)
    assert ind.equity == Decimal(6000)
    assert ind.equity_total == Decimal(6500)
    # The ratio is the published figure over the published assets, so a reader can
    # check it on the screen (ADR 0029) — which is what it was not before.
    assert ind.liabilities_to_assets == ind.total_liabilities / ind.total_assets
    # And the pair does not close to 1: the 500 of minority interest is neither a
    # creditor's claim nor the listed shareholders'. Stated as the identity in
    # reais, which is exact — the same claim over the assets only holds to the
    # Decimal context's precision.
    assert ind.total_liabilities + ind.equity + Decimal(500) == ind.total_assets
    assert ind.liabilities_to_assets + ind.equity_to_assets < Decimal(1)
