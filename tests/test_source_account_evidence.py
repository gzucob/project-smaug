"""Source-account provenance from CVM mapping through TTM, SQL, and API."""

from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from smaug.analysis.domain.calculator import compute
from smaug.analysis.domain.entities import VIEW_TTM, TickerAnalysis
from smaug.analysis.domain.financials import (
    AccountingRegime,
    BankRegulatoryProvenance,
    Cpc41AccountEvidence,
    Cpc41EvidenceStatus,
    Cpc41PeriodProvenance,
    Cpc41SelectionStatus,
    Cpc41WindowProvenance,
    MarketData,
    SourceAccountEvidence,
    SourceAccountRef,
    SourceAccountStatus,
    StandardizedFinancials,
)
from smaug.analysis.domain.indicators import Indicators, NullReason
from smaug.analysis.domain.ttm import build_ttm
from smaug.analysis.infrastructure.mongo_fundamentals import standardize
from smaug.analysis.infrastructure.sql_repository import _to_entity, _to_row
from smaug.entrypoints.api import _to_response
from smaug.portfolio.domain.sectors import Sector
from smaug.portfolio.domain.taxonomy import Classification


def _acc(code: str, name: str, quantity: str) -> dict[str, Any]:
    return {"code": code, "name": name, "quantity": quantity}


def _evidence_by_field(
    financials: StandardizedFinancials,
) -> dict[str, SourceAccountEvidence]:
    return {item.field: item for item in financials.source_account_evidence}


def _valid_bank_provenance() -> BankRegulatoryProvenance:
    return BankRegulatoryProvenance(
        source="issuer_public_performance_analysis",
        period_start=date(2025, 1, 1),
        period_end=date(2025, 12, 31),
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


def test_mapping_records_raw_accounts_and_dfc_parent_scope() -> None:
    financials = standardize(
        {
            "BPA": {
                "accounts": [
                    _acc("1", "Ativo Total", "1000"),
                    _acc("1.01", "Ativo Circulante", "400"),
                    _acc("1.01.01", "Caixa e Equivalentes", "100"),
                ]
            },
            "BPP": {
                "accounts": [
                    _acc("2.01", "Passivo Circulante", "200"),
                    _acc("2.01.04", "Empréstimos e Financiamentos", "50"),
                    _acc("2.02.01", "Empréstimos e Financiamentos", "150"),
                    _acc("2.03", "Patrimônio Líquido Consolidado", "600"),
                ]
            },
            "DRE": {
                "accounts": [
                    _acc("3.01", "Receita de Venda de Bens", "900"),
                    _acc("3.03", "Resultado Bruto", "300"),
                    _acc("3.05", "Resultado Antes do Resultado Financeiro", "200"),
                    _acc("3.11", "Lucro Consolidado do Período", "120"),
                ]
            },
            "DFC": {
                "accounts": [
                    _acc("6.01", "Caixa Líquido das Atividades Operacionais", "500"),
                    _acc("6.01.01.04", "Depreciação e amortização", "80"),
                    _acc("6.01.01.04.01", "Depreciação de direito de uso", "10"),
                    _acc("6.02.01", "Aquisição de Imobilizado", "-150"),
                ]
            },
        },
        Sector.COMMODITY,
        date(2024, 12, 31),
    )

    evidence = _evidence_by_field(financials)
    assert evidence["cfo"].status is SourceAccountStatus.MAPPED
    assert [ref.code for ref in evidence["cfo"].found] == ["6.01"]
    assert evidence["dep_amort"].status is SourceAccountStatus.MAPPED
    assert [ref.code for ref in evidence["dep_amort"].found] == ["6.01.01.04"]
    assert evidence["dep_amort"].parent_code == "6.01"
    assert evidence["ebitda"].status is SourceAccountStatus.DERIVED
    assert evidence["ebitda"].formula == "ebit + dep_amort"
    assert evidence["ebitda"].dependencies == ("ebit", "dep_amort")
    assert "fcf" in evidence["cfo"].consumer_indicators


def test_absent_and_unmapped_sources_remain_distinct() -> None:
    corporate = standardize(
        {"DRE": {"accounts": [_acc("3.01", "Receita de Venda", "100")]}},
        Sector.COMMODITY,
        date(2024, 12, 31),
    )
    bank = standardize(
        {
            "BPA": {"accounts": [_acc("1", "Ativo Total", "1000")]},
            "DRE": {
                "accounts": [
                    _acc("3.01", "Receitas de Intermediação Financeira", "100")
                ]
            },
        },
        Sector.BANK,
        date(2024, 12, 31),
    )

    corporate_evidence = _evidence_by_field(corporate)
    bank_evidence = _evidence_by_field(bank)
    assert corporate_evidence["dep_amort"].status is SourceAccountStatus.ABSENT
    assert corporate_evidence["dep_amort"].blocker is NullReason.SOURCE_ACCOUNT_ABSENT
    assert bank_evidence["dep_amort"].status is SourceAccountStatus.UNMAPPED
    assert bank_evidence["dep_amort"].blocker is NullReason.SOURCE_ACCOUNT_UNMAPPED
    assert (
        bank_evidence["average_earning_assets"].blocker
        is NullReason.MISSING_REGULATORY_DISCLOSURE
    )


def test_bank_ratios_require_a_complete_paired_source_contract() -> None:
    values = {
        "filed_regime": AccountingRegime.BANK,
        "bank_interest_result_annualized": Decimal("100"),
        "average_earning_assets": Decimal("1000"),
        "bank_efficiency_expenses": Decimal("20"),
        "bank_efficiency_income": Decimal("100"),
        "credit_loss_expense_annualized": Decimal("10"),
        "average_credit_portfolio": Decimal("1000"),
    }
    without_contract = StandardizedFinancials(
        reference_date=date(2025, 12, 31),
        sector=Sector.BANK,
        **values,
    )
    with_contract = replace(
        without_contract,
        bank_regulatory_provenance=_valid_bank_provenance(),
    )
    partial = replace(
        with_contract,
        bank_regulatory_provenance=replace(
            _valid_bank_provenance(),
            available_inputs=frozenset({"bank_interest_result_annualized"}),
        ),
    )

    missing = compute(without_contract, None, MarketData())
    valid = compute(with_contract, None, MarketData())
    partial_result = compute(partial, None, MarketData())

    assert missing.net_interest_margin is None
    assert missing.null_reasons["net_interest_margin"] is (
        NullReason.MISSING_REGULATORY_DISCLOSURE
    )
    assert valid.net_interest_margin == Decimal("0.1")
    assert valid.efficiency_ratio == Decimal("0.2")
    assert valid.cost_of_risk == Decimal("0.01")
    assert partial_result.net_interest_margin is None
    assert partial_result.null_reasons["net_interest_margin"] is (
        NullReason.PARTIAL_REGULATORY_DISCLOSURE
    )


def test_ttm_and_calculation_carry_source_lineage() -> None:
    evidence = SourceAccountEvidence(
        field="capex",
        statement="DFC",
        status=SourceAccountStatus.ABSENT,
        expected=("scope=6.02",),
        found=(SourceAccountRef("6.02.02", "Alienação de Imobilizado", Decimal("10")),),
        parent_code="6.02",
        blocker=NullReason.SOURCE_ACCOUNT_ABSENT,
        consumer_indicators=("fcf",),
    )
    quarters = [
        StandardizedFinancials(
            reference_date=end,
            sector=Sector.COMMODITY,
            revenue=Decimal("10"),
            source_account_evidence=(evidence,),
        )
        for end in (
            date(2025, 3, 31),
            date(2025, 6, 30),
            date(2025, 9, 30),
            date(2025, 12, 31),
        )
    ]
    ttm = build_ttm(quarters, None)
    assert ttm is not None
    assert ttm.source_account_evidence == (evidence,)
    indicators = compute(ttm, None, market=MarketData())
    # The source metadata is carried independently of whether the fixture has
    # enough market inputs to calculate the full indicator set.
    assert indicators.source_account_evidence == (evidence,)


def test_ttm_carries_valid_bank_inputs_and_provenance() -> None:
    quarters = [
        StandardizedFinancials(
            reference_date=end,
            sector=Sector.BANK,
            filed_regime=AccountingRegime.BANK,
            revenue=Decimal("10"),
            bank_interest_result_annualized=Decimal("100"),
            average_earning_assets=Decimal("1000"),
            bank_efficiency_expenses=Decimal("20"),
            bank_efficiency_income=Decimal("100"),
            credit_loss_expense_annualized=Decimal("10"),
            average_credit_portfolio=Decimal("1000"),
            bank_regulatory_provenance=_valid_bank_provenance(),
        )
        for end in (
            date(2025, 3, 31),
            date(2025, 6, 30),
            date(2025, 9, 30),
            date(2025, 12, 31),
        )
    ]
    ttm = build_ttm(quarters, None)

    assert ttm is not None
    assert ttm.bank_interest_result_annualized == Decimal("100")
    assert ttm.average_earning_assets == Decimal("1000")
    assert ttm.bank_efficiency_expenses == Decimal("20")
    assert ttm.bank_efficiency_income == Decimal("100")
    assert ttm.credit_loss_expense_annualized == Decimal("10")
    assert ttm.average_credit_portfolio == Decimal("1000")
    assert ttm.bank_regulatory_provenance == _valid_bank_provenance()


def test_source_lineage_round_trips_through_sql_and_api() -> None:
    evidence = SourceAccountEvidence(
        field="cfo",
        statement="DFC",
        status=SourceAccountStatus.MAPPED,
        expected=("code=6.01",),
        found=(SourceAccountRef("6.01", "CFO", Decimal("100"), "Operating"),),
        parent_code="6.01",
        consumer_indicators=("fcf",),
        duplicates_discarded=2,
    )
    indicators = Indicators(
        source_account_evidence=(evidence,),
        bank_regulatory_provenance=_valid_bank_provenance(),
    )
    analysis = TickerAnalysis(
        ticker="PETR4",
        classification=Classification("Petróleo", "Petróleo", None),
        reference_date=date(2024, 12, 31),
        computed_at=datetime(2026, 8, 17, tzinfo=UTC),
        view=VIEW_TTM,
        indicators=indicators,
    )
    row = _to_row(analysis)
    assert row.source_account_evidence is not None
    assert row.source_account_evidence[0]["found"][0]["code"] == "6.01"
    assert row.bank_regulatory_provenance is not None
    assert row.bank_regulatory_provenance["perimeter"] == "consolidated"
    restored = _to_entity(row)
    assert restored.indicators.source_account_evidence == (evidence,)
    assert restored.indicators.bank_regulatory_provenance == _valid_bank_provenance()
    response = _to_response(analysis)
    assert response.indicators.source_account_evidence[0].status is (
        SourceAccountStatus.MAPPED
    )
    assert response.indicators.source_account_evidence[0].found[0].value == Decimal(
        "100"
    )
    assert response.indicators.source_account_evidence[0].found[0].column == (
        "Operating"
    )
    assert response.indicators.source_account_evidence[0].duplicates_discarded == 2
    assert response.indicators.bank_regulatory_provenance is not None
    assert response.indicators.bank_regulatory_provenance.basis == (
        "issuer_defined_annualized_disclosure"
    )


def test_cpc41_window_provenance_round_trips_through_sql_and_api() -> None:
    provenance = Cpc41WindowProvenance(
        selected_periods=(
            Cpc41PeriodProvenance(
                reference_date=date(2025, 12, 31),
                disclosure_status=Cpc41EvidenceStatus.AVAILABLE,
                class_status=Cpc41EvidenceStatus.AVAILABLE,
                multiplier_status=Cpc41EvidenceStatus.AVAILABLE,
                multiplier=Decimal("2"),
                basic_weighted_shares=Decimal("100"),
                basic_weighted_shares_status=Cpc41EvidenceStatus.AVAILABLE,
                diluted_weighted_shares=None,
                diluted_weighted_shares_status=Cpc41EvidenceStatus.ABSENT,
                basic_blocker=None,
                diluted_blocker=NullReason.MISSING_CPC41_DISCLOSURE,
                source_accounts=(
                    Cpc41AccountEvidence(
                        module="DRE",
                        code="3.99.01.01",
                        name="Lucro por ação ON",
                        selection_status=Cpc41SelectionStatus.SELECTED,
                        value=Decimal("1.25"),
                        basis="basic",
                        expected=True,
                    ),
                ),
                basic_disclosure_status=Cpc41EvidenceStatus.AVAILABLE,
                diluted_disclosure_status=Cpc41EvidenceStatus.ABSENT,
                basic_class_status=Cpc41EvidenceStatus.AVAILABLE,
                diluted_class_status=Cpc41EvidenceStatus.ABSENT,
                basic_multiplier_status=Cpc41EvidenceStatus.AVAILABLE,
                diluted_multiplier_status=Cpc41EvidenceStatus.ABSENT,
            ),
        ),
        basic_blocker=None,
        diluted_blocker=NullReason.MISSING_CPC41_DISCLOSURE,
    )
    analysis = TickerAnalysis(
        ticker="PETR4",
        classification=Classification("Petróleo", "Petróleo", None),
        reference_date=date(2025, 12, 31),
        computed_at=datetime(2026, 8, 17, tzinfo=UTC),
        view=VIEW_TTM,
        indicators=Indicators(cpc41_window_provenance=provenance),
    )

    row = _to_row(analysis)
    assert row.cpc41_window_provenance is not None
    assert (
        row.cpc41_window_provenance["selected_periods"][0]["source_accounts"][0]["code"]
        == "3.99.01.01"
    )
    assert (
        row.cpc41_window_provenance["selected_periods"][0][
            "basic_weighted_shares_status"
        ]
        == "available"
    )
    restored = _to_entity(row)
    assert restored.indicators.cpc41_window_provenance == provenance

    response = _to_response(analysis)
    response_provenance = response.indicators.cpc41_window_provenance
    assert response_provenance is not None
    assert response_provenance.diluted_blocker is NullReason.MISSING_CPC41_DISCLOSURE
    assert response_provenance.selected_periods[0].source_accounts[0].module == "DRE"
    assert response_provenance.selected_periods[0].source_accounts[
        0
    ].selection_status is (Cpc41SelectionStatus.SELECTED)
    assert response_provenance.selected_periods[0].source_accounts[0].expected is True
    assert (
        response_provenance.selected_periods[0].basic_disclosure_status
        is Cpc41EvidenceStatus.AVAILABLE
    )
    assert (
        response_provenance.selected_periods[0].diluted_disclosure_status
        is Cpc41EvidenceStatus.ABSENT
    )
