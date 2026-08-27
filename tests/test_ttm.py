"""TTM assembly: sum isolated quarter flows, latest stocks, derive the missing Q4."""

from dataclasses import fields, replace
from datetime import date
from decimal import Decimal

from smaug.analysis.domain.financials import (
    AccountingRegime,
    Cpc41Disclosure,
    DebtCoverageEvidence,
    DebtIdentityStatus,
    DebtLineEvidence,
    DebtLineRole,
    InsuranceUnderwritingEvidence,
    InsuranceUnderwritingStatus,
    RegimeSource,
    SourceAccountEvidence,
    SourceAccountRef,
    SourceAccountStatus,
    StandardizedFinancials,
)
from smaug.analysis.domain.indicators import NullReason
from smaug.analysis.domain.ttm import _FLOW_FIELDS, build_ttm, build_ttm_as_of
from smaug.portfolio.domain.sectors import Sector


def _q(
    end: date,
    *,
    revenue: Decimal | None = None,
    net_income: Decimal | None = None,
    equity: Decimal | None = None,
    period_start: date | None = None,
    dep_amort: Decimal | None = None,
    dividends_paid: Decimal | None = None,
    cfo: Decimal | None = None,
    capex: Decimal | None = None,
    dfc_period_start: date | None = None,
    cpc41: Cpc41Disclosure | None = None,
) -> StandardizedFinancials:
    return StandardizedFinancials(
        reference_date=end,
        sector=Sector.COMMODITY,
        period_start=period_start,
        dfc_period_start=dfc_period_start,
        revenue=revenue,
        net_income=net_income,
        equity=equity,
        dep_amort=dep_amort,
        dividends_paid=dividends_paid,
        cfo=cfo,
        capex=capex,
        cpc41=cpc41,
    )


_ENDS = (
    date(2025, 6, 30),
    date(2025, 9, 30),
    date(2025, 12, 31),
    date(2026, 3, 31),
)


def _cpc41(
    basic: str,
    *,
    multiplier: int = 1,
    diluted: str | None = None,
) -> Cpc41Disclosure:
    return Cpc41Disclosure(
        basic_base_eps=Decimal(basic),
        diluted_base_eps=None if diluted is None else Decimal(diluted),
        security_multiplier=Decimal(multiplier),
    )


def test_ttm_reconstructs_a_strict_share_day_weighted_basic_and_diluted_eps() -> None:
    quarters = [
        _q(
            date(2025, 6, 30),
            period_start=date(2025, 4, 1),
            net_income=Decimal(100),
            cpc41=_cpc41("2", diluted="2"),  # 50 weighted shares for 91 days
        ),
        _q(
            date(2025, 9, 30),
            period_start=date(2025, 7, 1),
            net_income=Decimal(100),
            cpc41=_cpc41("1.25", diluted="1.25"),  # 80 shares for 92 days
        ),
        _q(
            date(2025, 12, 31),
            period_start=date(2025, 10, 1),
            net_income=Decimal(100),
            cpc41=_cpc41("1", diluted="1"),  # 100 shares for 92 days
        ),
        _q(
            date(2026, 3, 31),
            period_start=date(2026, 1, 1),
            net_income=Decimal(100),
            cpc41=_cpc41("2.5", diluted="2.5"),  # 40 shares for 90 days
        ),
    ]

    ttm = build_ttm(quarters, None)

    assert ttm is not None
    expected = Decimal(400) / (
        (Decimal(50) * 91 + Decimal(80) * 92 + Decimal(100) * 92 + Decimal(40) * 90)
        / Decimal(365)
    )
    assert ttm.eps_basic == expected
    assert ttm.eps_diluted == expected
    assert ttm.eps_basic_null_reason is None
    assert ttm.eps_diluted_null_reason is None


def test_ttm_derives_ytd_q4_only_from_a_complete_weighted_prefix() -> None:
    jan = date(2025, 1, 1)
    quarters = [
        _q(
            date(2025, 3, 31),
            period_start=jan,
            net_income=Decimal(100),
            cpc41=_cpc41("1", diluted="1"),  # 100 shares YTD
        ),
        _q(
            date(2025, 6, 30),
            period_start=jan,
            net_income=Decimal(240),
            cpc41=_cpc41("2", diluted="2"),  # 120 shares YTD
        ),
        _q(
            date(2025, 9, 30),
            period_start=jan,
            net_income=Decimal(330),
            cpc41=_cpc41("3", diluted="3"),  # 110 shares YTD
        ),
    ]
    annual = _q(
        date(2025, 12, 31),
        period_start=jan,
        net_income=Decimal(420),
        cpc41=_cpc41("4", diluted="4"),  # 105 shares for the full year
    )

    ttm = build_ttm(quarters, annual)

    assert ttm is not None
    assert ttm.net_income == Decimal(420)
    assert ttm.eps_basic == Decimal(4)
    assert ttm.eps_diluted == Decimal(4)


def test_ttm_keeps_basic_and_nulls_diluted_when_potential_shares_are_unavailable() -> (
    None
):
    quarters = [
        _q(
            end,
            period_start=start,
            net_income=Decimal(100),
            cpc41=_cpc41("2", multiplier=5),
        )
        for end, start in zip(
            _ENDS,
            (
                date(2025, 4, 1),
                date(2025, 7, 1),
                date(2025, 10, 1),
                date(2026, 1, 1),
            ),
            strict=True,
        )
    ]

    ttm = build_ttm(quarters, None)

    assert ttm is not None
    assert ttm.eps_basic == Decimal(40)
    assert ttm.eps_diluted is None
    assert ttm.eps_basic_null_reason is None
    assert ttm.eps_diluted_null_reason is NullReason.MISSING_CPC41_DISCLOSURE


def test_ttm_does_not_infer_a_weighted_denominator_from_a_missing_period() -> None:
    quarters = [
        _q(
            end,
            period_start=start,
            net_income=Decimal(100),
            cpc41=None if end == date(2025, 9, 30) else _cpc41("2"),
        )
        for end, start in zip(
            _ENDS,
            (
                date(2025, 4, 1),
                date(2025, 7, 1),
                date(2025, 10, 1),
                date(2026, 1, 1),
            ),
            strict=True,
        )
    ]

    ttm = build_ttm(quarters, None)

    assert ttm is not None
    assert ttm.eps_basic is None
    assert ttm.eps_basic_null_reason is NullReason.MISSING_CPC41_DISCLOSURE


def test_ttm_isolates_the_declared_dividends_on_the_dmpl_span() -> None:
    # #104: the DMPL is year-to-date like the DFC, on its own span. Four YTD
    # figures (100, 250, 400 within one year, then Q1 of the next at 80) must
    # isolate to 100+150+150... — here two full years make it simple: the last
    # four isolated quarters are 150 (Q2), 150 (Q3), 200 (Q4, from the annual),
    # and 80 (the new year's Q1) = 580.
    year1 = [
        replace(
            _q(
                date(2025, m, d),
                revenue=Decimal(1000),
                period_start=date(2025, m - 2, 1),
            ),
            dividends_declared=ytd,
            dmpl_period_start=date(2025, 1, 1),
        )
        for (m, d), ytd in zip(
            [(3, 31), (6, 30), (9, 30)],
            [Decimal(100), Decimal(250), Decimal(400)],
            strict=True,
        )
    ]
    annual = replace(
        _q(date(2025, 12, 31), revenue=Decimal(4000), period_start=date(2025, 1, 1)),
        dividends_declared=Decimal(600),  # the year's total → Q4 = 600 − 400
        dmpl_period_start=date(2025, 1, 1),
    )
    q1_next = replace(
        _q(date(2026, 3, 31), revenue=Decimal(1000), period_start=date(2026, 1, 1)),
        dividends_declared=Decimal(80),
        dmpl_period_start=date(2026, 1, 1),
    )

    ttm = build_ttm(year1 + [q1_next], annual)

    assert ttm is not None
    assert ttm.dividends_declared == Decimal(580)  # 150 + 150 + 200 + 80


def test_ttm_sums_isolated_flows_and_takes_latest_stocks() -> None:
    quarters = [_q(e, revenue=Decimal(1000), net_income=Decimal(100)) for e in _ENDS]
    quarters[-1] = _q(
        _ENDS[-1], revenue=Decimal(1000), net_income=Decimal(100), equity=Decimal(6000)
    )

    ttm = build_ttm(quarters, None)

    assert ttm is not None
    assert ttm.revenue == Decimal(4000)  # 4 × 1000
    assert ttm.net_income == Decimal(400)
    assert ttm.equity == Decimal(6000)  # stock: latest quarter, not summed
    assert ttm.reference_date == date(2026, 3, 31)
    assert ttm.period_start == date(2025, 4, 1)  # 12 months back → annualization no-op


def test_ttm_sums_the_total_slice_flow_and_takes_its_stock_from_the_latest() -> None:
    # ADR 0026: the consolidated-total slice travels like its controllers'
    # sibling — the income total is a flow (summed), the equity total a stock.
    quarters = [
        replace(
            _q(e, net_income=Decimal(100)),
            net_income_total=Decimal(110),
        )
        for e in _ENDS
    ]
    quarters[-1] = replace(
        quarters[-1], equity=Decimal(6000), equity_total=Decimal(6600)
    )

    ttm = build_ttm(quarters, None)

    assert ttm is not None
    assert ttm.net_income_total == Decimal(440)  # 4 × 110, minority included
    assert ttm.equity_total == Decimal(6600)  # stock: latest quarter, not summed


def test_ttm_sums_the_bank_dre_lines_and_takes_the_loan_book_as_a_stock() -> None:
    # The raw CVM lines remain available as statement facts even though ADR 0058
    # forbids using them as substitutes for the regulatory ratio inputs. Expenses
    # stay signed as filed — summing must not flip them.
    quarters = [
        replace(
            _q(e),
            gross_profit=Decimal(1000),
            loan_loss_provision=Decimal(-200),
            fee_income=Decimal(300),
            personnel_expense=Decimal(-250),
            admin_expense=Decimal(-150),
            loan_book=Decimal(50_000),
        )
        for e in _ENDS
    ]
    quarters[-1] = replace(quarters[-1], loan_book=Decimal(56_000))

    ttm = build_ttm(quarters, None)

    assert ttm is not None
    assert ttm.gross_profit == Decimal(4000)
    assert ttm.loan_loss_provision == Decimal(-800)  # negative as filed
    assert ttm.fee_income == Decimal(1200)
    assert ttm.personnel_expense == Decimal(-1000)
    assert ttm.admin_expense == Decimal(-600)
    assert ttm.loan_book == Decimal(56_000)  # stock: latest quarter, not summed


def test_ttm_sums_the_insurer_dre_lines() -> None:
    # Same hole as the bank's (#140): #98 would have hit it the moment it landed.
    quarters = [
        replace(
            _q(e),
            earned_premium=Decimal(900),
            claims_incurred=Decimal(-400),
            acquisition_costs=Decimal(-80),
            insurance_admin_expenses=Decimal(-50),
        )
        for e in _ENDS
    ]

    ttm = build_ttm(quarters, None)

    assert ttm is not None
    assert ttm.earned_premium == Decimal(3600)
    assert ttm.claims_incurred == Decimal(-1600)  # negative as filed
    assert ttm.acquisition_costs == Decimal(-320)
    assert ttm.insurance_admin_expenses == Decimal(-200)


def test_ttm_requires_zero_activity_proof_for_the_whole_insurance_window() -> None:
    evidence = InsuranceUnderwritingEvidence(
        status=InsuranceUnderwritingStatus.ZERO_ACTIVITY,
        revenue_aggregate=SourceAccountRef("3.01", "Insurance revenue", Decimal(0)),
        expense_aggregate=SourceAccountRef("3.02", "Insurance expenses", Decimal(0)),
    )
    source = SourceAccountEvidence(
        field="insurance_underwriting_activity",
        statement="DRE",
        status=SourceAccountStatus.DERIVED,
        expected=("code=3.01", "code=3.02"),
        found=(
            SourceAccountRef("3.01", "Insurance revenue", Decimal(0)),
            SourceAccountRef("3.02", "Insurance expenses", Decimal(0)),
        ),
        formula="3.01 == 0 and 3.02 == 0",
        blocker=NullReason.INAPPLICABLE_REGIME,
    )
    quarters = [
        replace(
            _q(e, revenue=Decimal(100)),
            sector=Sector.INSURER,
            filed_regime=AccountingRegime.INSURANCE,
            insurance_underwriting_evidence=evidence,
            source_account_evidence=(source,),
        )
        for e in _ENDS
    ]

    ttm = build_ttm(quarters, None)

    assert ttm is not None
    assert ttm.insurance_underwriting_evidence is not None
    assert (
        ttm.insurance_underwriting_evidence.status
        is InsuranceUnderwritingStatus.ZERO_ACTIVITY
    )
    assert ttm.source_account_evidence == (source,)


def test_ttm_keeps_active_underwriting_provenance_over_a_newer_zero_period() -> None:
    def underwriting(
        label: str, status: InsuranceUnderwritingStatus
    ) -> InsuranceUnderwritingEvidence:
        value = (
            Decimal(100) if status is InsuranceUnderwritingStatus.ACTIVE else Decimal(0)
        )
        return InsuranceUnderwritingEvidence(
            status=status,
            revenue_aggregate=SourceAccountRef("3.01", f"{label} revenue", value),
            expense_aggregate=SourceAccountRef("3.02", f"{label} expenses", -value),
        )

    def activity_source(
        label: str, status: InsuranceUnderwritingStatus
    ) -> SourceAccountEvidence:
        value = (
            Decimal(100) if status is InsuranceUnderwritingStatus.ACTIVE else Decimal(0)
        )
        return SourceAccountEvidence(
            field="insurance_underwriting_activity",
            statement="DRE",
            status=SourceAccountStatus.DERIVED,
            expected=("code=3.01", "code=3.02"),
            found=(
                SourceAccountRef("3.01", f"{label} revenue", value),
                SourceAccountRef("3.02", f"{label} expenses", -value),
            ),
            formula="3.01 == 0 and 3.02 == 0",
            blocker=(
                NullReason.INAPPLICABLE_REGIME
                if status is InsuranceUnderwritingStatus.ZERO_ACTIVITY
                else None
            ),
        )

    statuses = [
        InsuranceUnderwritingStatus.ACTIVE,
        InsuranceUnderwritingStatus.ZERO_ACTIVITY,
        InsuranceUnderwritingStatus.ZERO_ACTIVITY,
        InsuranceUnderwritingStatus.ZERO_ACTIVITY,
    ]
    quarters = [
        replace(
            _q(end, revenue=Decimal(100)),
            sector=Sector.INSURER,
            filed_regime=AccountingRegime.INSURANCE,
            insurance_underwriting_evidence=underwriting(f"Q{index}", status),
            source_account_evidence=(activity_source(f"Q{index}", status),),
        )
        for index, (end, status) in enumerate(
            zip(_ENDS, statuses, strict=True), start=1
        )
    ]

    ttm = build_ttm(quarters, None)

    assert ttm is not None
    assert ttm.insurance_underwriting_evidence is not None
    assert (
        ttm.insurance_underwriting_evidence.status is InsuranceUnderwritingStatus.ACTIVE
    )
    assert ttm.insurance_underwriting_evidence.revenue_aggregate == SourceAccountRef(
        "3.01", "Q1 revenue", Decimal(100)
    )
    activity = {item.field: item for item in ttm.source_account_evidence}[
        "insurance_underwriting_activity"
    ]
    assert activity.found == (
        SourceAccountRef("3.01", "Q1 revenue", Decimal(100)),
        SourceAccountRef("3.02", "Q1 expenses", Decimal(-100)),
    )
    assert activity.blocker is None


def test_ttm_uses_annual_underwriting_proof_without_losing_latest_lineage() -> None:
    def activity_source(label: str) -> SourceAccountEvidence:
        return SourceAccountEvidence(
            field="insurance_underwriting_activity",
            statement="DRE",
            status=SourceAccountStatus.DERIVED,
            expected=("code=3.01", "code=3.02"),
            found=(
                SourceAccountRef("3.01", f"{label} revenue", Decimal(0)),
                SourceAccountRef("3.02", f"{label} expenses", Decimal(0)),
            ),
            formula="3.01 == 0 and 3.02 == 0",
            blocker=NullReason.INAPPLICABLE_REGIME,
        )

    def underwriting_zero(label: str) -> InsuranceUnderwritingEvidence:
        return InsuranceUnderwritingEvidence(
            status=InsuranceUnderwritingStatus.ZERO_ACTIVITY,
            revenue_aggregate=SourceAccountRef("3.01", f"{label} revenue", Decimal(0)),
            expense_aggregate=SourceAccountRef("3.02", f"{label} expenses", Decimal(0)),
        )

    jan = date(2025, 1, 1)
    quarters = [
        replace(
            _q(end, revenue=Decimal(value), period_start=jan),
            sector=Sector.INSURER,
            filed_regime=AccountingRegime.INSURANCE,
            insurance_underwriting_evidence=underwriting_zero(f"Q{index}"),
            source_account_evidence=(
                SourceAccountEvidence(
                    field="revenue",
                    statement="DRE",
                    status=SourceAccountStatus.MAPPED,
                    expected=("code=3.01",),
                    found=(
                        SourceAccountRef(
                            "3.01", "Latest quarter revenue", Decimal(value)
                        ),
                    ),
                ),
                activity_source(f"Q{index}"),
            ),
        )
        for index, (end, value) in enumerate(
            zip(
                (
                    date(2025, 3, 31),
                    date(2025, 6, 30),
                    date(2025, 9, 30),
                ),
                (Decimal(100), Decimal(200), Decimal(300)),
                strict=True,
            ),
            start=1,
        )
    ]
    annual = replace(
        _q(date(2025, 12, 31), revenue=Decimal(400), period_start=jan),
        sector=Sector.INSURER,
        filed_regime=AccountingRegime.INSURANCE,
        insurance_underwriting_evidence=underwriting_zero("Annual"),
        source_account_evidence=(
            SourceAccountEvidence(
                field="revenue",
                statement="DRE",
                status=SourceAccountStatus.MAPPED,
                expected=("code=3.01",),
                found=(SourceAccountRef("3.01", "Annual revenue", Decimal(400)),),
            ),
            activity_source("Annual"),
        ),
    )

    ttm = build_ttm(quarters, annual)

    assert ttm is not None
    assert ttm.insurance_underwriting_evidence is not None
    assert ttm.insurance_underwriting_evidence.revenue_aggregate == SourceAccountRef(
        "3.01", "Annual revenue", Decimal(0)
    )
    evidence = {item.field: item for item in ttm.source_account_evidence}
    assert evidence["revenue"].found[0] == SourceAccountRef(
        "3.01", "Latest quarter revenue", Decimal(300)
    )
    assert evidence["insurance_underwriting_activity"].found == (
        SourceAccountRef("3.01", "Annual revenue", Decimal(0)),
        SourceAccountRef("3.02", "Annual expenses", Decimal(0)),
    )


def test_ttm_carries_every_numeric_account_on_its_declared_basis() -> None:
    # The assembly names each account explicitly, which is how the bank lines went
    # missing for a year (#140). Fill every numeric field with the same value in
    # all four quarters and read the basis off the result: a flow sums to 4×, a
    # stock stays at the latest quarter's 1×. So this fails both ways a new
    # account can go wrong — dropped entirely, or wired to the wrong basis, which
    # is the worse one (a quarter published as if it were a year).
    numeric = [
        f.name for f in fields(StandardizedFinancials) if f.type == "Decimal | None"
    ]
    assert "loan_book" in numeric  # the annotation is read as text — fail loudly
    quarters = [replace(_q(e), **dict.fromkeys(numeric, Decimal(100))) for e in _ENDS]

    ttm = build_ttm(quarters, None)

    assert ttm is not None
    for name in numeric:
        if name in {
            "eps_basic",
            "eps_diluted",
            "bank_interest_result_annualized",
            "average_earning_assets",
            "bank_efficiency_expenses",
            "bank_efficiency_income",
            "credit_loss_expense_annualized",
            "average_credit_portfolio",
        }:
            # Per-share results carry weighted denominators and cannot be added
            # across quarters or treated as closing stocks. Bank regulatory
            # pairs likewise require an explicit TTM disclosure; CVM quarters
            # cannot manufacture their averages/perimeter.
            expected = None
        elif name == "ebitda":
            expected = Decimal(800)  # recomposed from the summed EBIT + D&A
        elif name in _FLOW_FIELDS:
            expected = Decimal(400)  # flow: summed over the four quarters
        else:
            expected = Decimal(100)  # stock: the latest quarter, never summed
        assert getattr(ttm, name) == expected, name
    assert ttm.eps_basic_null_reason is NullReason.MISSING_CPC41_DISCLOSURE


def test_ttm_preserves_specific_cpc41_blocker_from_a_missing_period() -> None:
    quarters = [
        _q(
            end,
            period_start=start,
            net_income=Decimal(100),
            cpc41=None if end == date(2025, 9, 30) else _cpc41("2"),
        )
        for end, start in zip(
            _ENDS,
            (
                date(2025, 4, 1),
                date(2025, 7, 1),
                date(2025, 10, 1),
                date(2026, 1, 1),
            ),
            strict=True,
        )
    ]
    missing = next(
        index for index, period in enumerate(quarters) if period.cpc41 is None
    )
    quarters[missing] = replace(
        quarters[missing],
        eps_basic_null_reason=NullReason.MISSING_ECONOMIC_RIGHTS,
        eps_diluted_null_reason=NullReason.MISSING_UNIT_COMPOSITION,
    )

    ttm = build_ttm(quarters, None)

    assert ttm is not None
    assert ttm.eps_basic_null_reason is NullReason.MISSING_ECONOMIC_RIGHTS
    assert ttm.eps_diluted_null_reason is NullReason.MISSING_UNIT_COMPOSITION


def test_ttm_carries_the_null_cause_provenance() -> None:
    # filed_regime / unmapped_fields (#30) must survive the TTM assembly, or the
    # live view would lose the ability to attribute its nulls.
    quarters = [
        replace(
            _q(e, revenue=Decimal(1000)),
            filed_regime=AccountingRegime.BANK,
            bank_ratio_null_reason=NullReason.MISSING_REGULATORY_DISCLOSURE,
            debt_coverage_null_reason=NullReason.INCOMPLETE_DEBT_COVERAGE,
            unmapped_fields=frozenset({"cfo", "capex"}),
        )
        for e in _ENDS
    ]

    ttm = build_ttm(quarters, None)

    assert ttm is not None
    assert ttm.filed_regime is AccountingRegime.BANK
    assert ttm.bank_ratio_null_reason is NullReason.MISSING_REGULATORY_DISCLOSURE
    assert ttm.debt_coverage_null_reason is NullReason.INCOMPLETE_DEBT_COVERAGE
    assert ttm.unmapped_fields == frozenset({"cfo", "capex"})


def test_ttm_carries_raw_bpp_debt_evidence_and_issuer_identity() -> None:
    evidence = DebtCoverageEvidence(
        regime=AccountingRegime.CORPORATE,
        regime_source=RegimeSource.FILED,
        identity_status=DebtIdentityStatus.RESOLVED,
        used_lines=(
            DebtLineEvidence(
                "2.01.04",
                "Empréstimos e Financiamentos",
                Decimal("100"),
                DebtLineRole.CURRENT_AGGREGATE,
            ),
        ),
        included_instruments=("2.01.04",),
    )
    quarters = [
        replace(
            _q(e, revenue=Decimal(1000)),
            issuer_name="ACME S.A.",
            cd_cvm="1234",
            cnpj="12.345.678/0001-90",
            debt_evidence=evidence,
        )
        for e in _ENDS
    ]

    ttm = build_ttm(quarters, None)

    assert ttm is not None
    assert ttm.issuer_name == "ACME S.A."
    assert ttm.cd_cvm == "1234"
    assert ttm.cnpj == "12.345.678/0001-90"
    assert ttm.debt_evidence == evidence


def test_ttm_normalizes_ytd_quarters_and_derives_q4_from_annual() -> None:
    # ITR income statements filed year-to-date; Q4 has no ITR, only the annual DFP.
    jan = date(2025, 1, 1)
    quarters = [
        _q(date(2025, 3, 31), revenue=Decimal(100), period_start=jan),  # Q1 YTD
        _q(date(2025, 6, 30), revenue=Decimal(250), period_start=jan),  # 6-month YTD
        _q(date(2025, 9, 30), revenue=Decimal(390), period_start=jan),  # 9-month YTD
    ]
    annual = _q(date(2025, 12, 31), revenue=Decimal(500), equity=Decimal(8000))

    ttm = build_ttm(quarters, annual)

    assert ttm is not None
    # Isolated: 100, 150, 140, and Q4 = 500 − 390 = 110 → full year = 500.
    assert ttm.revenue == Decimal(500)
    assert ttm.reference_date == date(2025, 12, 31)  # window ends on the closed year
    assert ttm.equity == Decimal(8000)  # stock from the annual (latest balance)


def test_ttm_as_of_selects_the_exact_comparable_interim_window() -> None:
    quarters = [
        _q(date(year, month, day), revenue=Decimal(value))
        for year, month, day, value in (
            (2024, 3, 31, 10),
            (2024, 6, 30, 20),
            (2024, 9, 30, 30),
            (2024, 12, 31, 40),
            (2025, 3, 31, 50),
            (2025, 6, 30, 60),
            (2025, 9, 30, 70),
        )
    ]

    q1 = build_ttm_as_of(quarters, [], date(2025, 3, 31))
    q2 = build_ttm_as_of(quarters, [], date(2025, 6, 30))
    q3 = build_ttm_as_of(quarters, [], date(2025, 9, 30))

    assert q1 is not None
    assert q2 is not None
    assert q3 is not None
    assert q1.revenue == Decimal(140)  # Q2/24 through Q1/25
    assert q2.revenue == Decimal(180)  # Q3/24 through Q2/25
    assert q3.revenue == Decimal(220)  # Q4/24 through Q3/25


def test_ttm_as_of_derives_the_closed_fourth_quarter() -> None:
    quarters = [
        _q(date(2024, month, day), revenue=Decimal(value))
        for month, day, value in ((3, 31, 100), (6, 30, 110), (9, 30, 120))
    ]
    annual = _q(date(2024, 12, 31), revenue=Decimal(500))

    ttm = build_ttm_as_of(quarters, [annual], date(2024, 12, 31))

    assert ttm is not None
    assert ttm.reference_date == annual.reference_date
    assert ttm.revenue == annual.revenue  # Q4 = 500 - (100 + 110 + 120)


def test_ttm_as_of_does_not_reach_past_a_missing_quarter() -> None:
    quarters = [
        _q(end, revenue=Decimal(100))
        for end in (
            date(2024, 3, 31),
            # Q2/2024 is absent: Q1 must not silently replace it.
            date(2024, 9, 30),
            date(2024, 12, 31),
            date(2025, 3, 31),
        )
    ]

    assert build_ttm_as_of(quarters, [], date(2025, 3, 31)) is None


def test_ttm_rejects_a_discontinuous_four_quarter_window() -> None:
    quarters = [
        _q(end, revenue=Decimal(100))
        for end in (
            date(2024, 6, 30),
            date(2024, 9, 30),
            # Q4/2024 is absent; four rows must not be treated as a TTM.
            date(2025, 3, 31),
            date(2025, 6, 30),
        )
    ]

    assert build_ttm(quarters, None) is None


def test_ttm_isolates_dfc_flows_on_their_own_year_to_date_span() -> None:
    # The DRE is isolated quarters but the DFC is year-to-date, so D&A and
    # dividends must isolate on the DFC span, independent of the DRE span.
    jan = date(2025, 1, 1)
    quarters = [
        _q(  # DRE Q1 isolated; DFC YTD 3m
            date(2025, 3, 31),
            revenue=Decimal(100),
            period_start=jan,
            dep_amort=Decimal(10),
            dividends_paid=Decimal(0),
            cfo=Decimal(30),
            capex=Decimal(12),
            dfc_period_start=jan,
        ),
        _q(  # DRE Q2 isolated (Apr-Jun); DFC YTD 6m
            date(2025, 6, 30),
            revenue=Decimal(110),
            period_start=date(2025, 4, 1),
            dep_amort=Decimal(25),
            dividends_paid=Decimal(40),
            cfo=Decimal(70),
            capex=Decimal(30),
            dfc_period_start=jan,
        ),
        _q(  # DRE Q3 isolated (Jul-Sep); DFC YTD 9m
            date(2025, 9, 30),
            revenue=Decimal(120),
            period_start=date(2025, 7, 1),
            dep_amort=Decimal(45),
            dividends_paid=Decimal(40),
            cfo=Decimal(120),
            capex=Decimal(50),
            dfc_period_start=jan,
        ),
    ]
    annual = _q(
        date(2025, 12, 31),
        revenue=Decimal(500),
        dep_amort=Decimal(70),
        dividends_paid=Decimal(60),
        cfo=Decimal(200),
        capex=Decimal(80),
        equity=Decimal(8000),
    )

    ttm = build_ttm(quarters, annual)

    assert ttm is not None
    assert ttm.revenue == Decimal(500)  # DRE: 100+110+120 + Q4(170)
    # DFC isolated: 10, 15, 20, and Q4 D&A = 70 - 45 = 25 -> full year 70.
    assert ttm.dep_amort == Decimal(70)
    # DFC isolated: 0, 40, 0, and Q4 dividends = 60 - 40 = 20 -> full year 60.
    assert ttm.dividends_paid == Decimal(60)
    # CFO isolated on the DFC span: 30, 40, 50, Q4 = 200 - 120 = 80 -> full year 200.
    assert ttm.cfo == Decimal(200)
    # Capex isolated: 12, 18, 20, Q4 = 80 - 50 = 30 -> full year 80.
    assert ttm.capex == Decimal(80)


def test_ttm_returns_none_with_fewer_than_four_quarters() -> None:
    quarters = [_q(e, revenue=Decimal(1000)) for e in _ENDS[:3]]
    assert build_ttm(quarters, None) is None


def test_ttm_flow_is_null_when_a_quarter_lacks_the_line() -> None:
    quarters = [_q(e, revenue=Decimal(1000), net_income=Decimal(100)) for e in _ENDS]
    quarters[1] = _q(_ENDS[1], revenue=Decimal(1000))  # this quarter has no net income

    ttm = build_ttm(quarters, None)

    assert ttm is not None
    assert ttm.revenue == Decimal(4000)  # revenue present in all four
    assert ttm.net_income is None  # a gap makes the TTM flow null, not understated
