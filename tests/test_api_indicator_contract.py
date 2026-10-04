"""The read API preserves current and historical indicator calculation contracts."""

from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal

from smaug.analysis.domain.entities import (
    VIEW_CLOSED_YEAR,
    VIEW_TTM,
    AnalysisView,
    TickerAnalysis,
)
from smaug.analysis.domain.financials import (
    AccountingRegime,
    DebtBlocker,
    DebtCoverageEvidence,
    DebtEvidenceSnapshot,
    DebtIdentityStatus,
    DebtInstrument,
    DebtLineClassification,
    DebtLineEvidence,
    DebtLineRole,
    RegimeSource,
)
from smaug.analysis.domain.indicators import (
    CALCULATION_CONTRACT_VERSION,
    INDICATOR_CONTRACT,
    LEGACY_CALCULATION_CONTRACT,
    Indicators,
    NullReason,
    indicator_names,
    public_indicator_names,
)
from smaug.entrypoints.api import IndicatorsResponse, _to_response
from smaug.portfolio.domain.share_classes import (
    PerShareClass,
    ShareClassMapping,
    ShareClassMappingReason,
    ShareKind,
    TickerCodeEvidence,
)
from smaug.portfolio.domain.taxonomy import Classification


def _analysis(view: AnalysisView) -> TickerAnalysis:
    return TickerAnalysis(
        ticker="PETR4",
        classification=Classification(
            "Petróleo", "Petróleo, Gás e Biocombustíveis", None
        ),
        reference_date=date(2025, 12, 31),
        computed_at=datetime(2026, 8, 15, tzinfo=UTC),
        view=view,
        calculation_contract_version=CALCULATION_CONTRACT_VERSION,
        price=Decimal("38"),
        price_source_code="PETR4",
        price_source_session=date(2026, 8, 14),
        price_basis="b3_latest_close",
        share_count_basis="cvm_latest_filed_outstanding_current_base",
        filed_regime=AccountingRegime.CORPORATE,
        regime_source=RegimeSource.FILED,
        issuer_name="Petroleo Teste S.A.",
        cd_cvm="9512",
        cnpj="95.123.456/0001-78",
        debt_evidence_snapshot=DebtEvidenceSnapshot.CURRENT,
        debt_evidence=DebtCoverageEvidence(
            regime=AccountingRegime.CORPORATE,
            regime_source=RegimeSource.FILED,
            identity_status=DebtIdentityStatus.RESOLVED,
            used_lines=(
                DebtLineEvidence(
                    "2.01.04",
                    "Empréstimos e Financiamentos",
                    Decimal("100"),
                    DebtLineRole.CURRENT_AGGREGATE,
                    instrument=DebtInstrument.LOANS_FINANCING,
                ),
            ),
            excluded_lines=(
                DebtLineEvidence(
                    "2.02.02.02.07",
                    "Passivo de Arrendamento",
                    Decimal("25"),
                    DebtLineRole.EXCLUDED_LIABILITY,
                    DebtBlocker.INCOMPLETE_DEBT_COVERAGE,
                    DebtInstrument.LEASES,
                ),
            ),
            included_instruments=("2.01.04",),
            primary_blocker=DebtBlocker.INCOMPLETE_DEBT_COVERAGE,
            secondary_blockers=(DebtBlocker.MISSING_NON_CURRENT_AGGREGATE,),
        ),
        indicators=Indicators(
            pe_basic=Decimal("6"),
            pb=Decimal("1.4"),
        ),
    )


def test_api_contract_exposes_one_selected_result_per_concept() -> None:
    response = _to_response(_analysis(VIEW_TTM))

    selected_pe = response.indicator_contract["pe_basic"]
    assert selected_pe.basis == "security_closing_capital"
    assert selected_pe.numerator == "security_price"
    assert selected_pe.denominator == "net_income_per_selected_closing_share"
    assert selected_pe.reference_period == "last_twelve_months"
    assert selected_pe.share_basis == "selected_closing_total_unit_equivalent"
    assert selected_pe.provenance == ["cvm", "b3"]

    assert "pe_basic_market" not in response.indicator_contract
    assert "eps_basic_market" not in response.indicator_contract


def test_api_contract_names_closed_year_period_without_changing_formula() -> None:
    response = _to_response(_analysis(VIEW_CLOSED_YEAR))

    assert response.indicator_contract["pe_basic"].reference_period == (
        "closed_fiscal_year"
    )


def test_contract_only_names_persisted_indicator_fields() -> None:
    assert set(INDICATOR_CONTRACT) <= set(indicator_names())


def test_api_response_exposes_filed_regime_provenance() -> None:
    response = _to_response(_analysis(VIEW_TTM))

    assert response.filed_regime is AccountingRegime.CORPORATE
    assert response.regime_source is RegimeSource.FILED


def test_api_response_exposes_b3_price_provenance() -> None:
    response = _to_response(_analysis(VIEW_TTM))

    assert response.price_source_code == "PETR4"
    assert response.price_source_session == date(2026, 8, 14)


def test_api_response_exposes_share_class_resolution_reason() -> None:
    mapping = ShareClassMapping(
        class_id="95.123.456/0001-78:ON",
        symbol="PETR3",
        kind=ShareKind.COMMON,
        per_share_class=PerShareClass.ORDINARY,
        resolution_reason=ShareClassMappingReason.B3_CODE_PRECEDENCE,
        code_evidence=(TickerCodeEvidence("PETR3", source="b3_get_detail"),),
        evidence=(
            "cvm_fca.placeholder",
            "b3.get_detail",
            "b3.listed_supplement",
            "b3.cotahist",
        ),
    )

    response = _to_response(
        replace(_analysis(VIEW_TTM), share_class_mappings=(mapping,))
    )

    assert response.share_class_mappings[0].resolution_reason == ("b3_code_precedence")


def test_api_response_exposes_raw_bpp_debt_evidence() -> None:
    response = _to_response(_analysis(VIEW_TTM))

    assert response.issuer == "Petroleo Teste S.A."
    assert response.cd_cvm == "9512"
    assert response.cnpj == "95.123.456/0001-78"
    assert response.debt_evidence_snapshot is DebtEvidenceSnapshot.CURRENT
    assert response.debt_evidence is not None
    assert response.debt_evidence.used_lines[0].code == "2.01.04"
    assert response.debt_evidence.used_lines[0].instrument is (
        DebtInstrument.LOANS_FINANCING
    )
    assert response.debt_evidence.used_lines[0].classification is (
        DebtLineClassification.INCLUDED
    )
    assert response.debt_evidence.excluded_lines[0].code == "2.02.02.02.07"
    assert response.debt_evidence.excluded_lines[0].instrument is DebtInstrument.LEASES
    assert response.debt_evidence.excluded_lines[0].classification is (
        DebtLineClassification.EXCLUDED
    )
    assert response.debt_evidence.primary_blocker is (
        DebtBlocker.INCOMPLETE_DEBT_COVERAGE
    )
    assert response.debt_evidence.secondary_blockers == [
        DebtBlocker.MISSING_NON_CURRENT_AGGREGATE
    ]


def test_api_marks_rows_without_debt_evidence_as_legacy() -> None:
    response = _to_response(
        replace(_analysis(VIEW_TTM), debt_evidence=None, debt_evidence_snapshot=None)
    )

    assert response.debt_evidence is None
    assert response.debt_evidence_snapshot is DebtEvidenceSnapshot.LEGACY


def test_legacy_values_are_preserved_without_promoting_market_alternatives() -> None:
    row = replace(
        _analysis(VIEW_TTM),
        calculation_contract_version=LEGACY_CALCULATION_CONTRACT,
        indicators=Indicators(
            eps_basic_market=Decimal("2"),
            pe_basic_market=Decimal("6"),
            null_reasons={
                "eps_basic": NullReason.MISSING_WEIGHTED_AVERAGE_SHARES,
                "eps_basic_market": NullReason.MISSING_SHARE_COUNT,
            },
        ),
    )
    response = _to_response(row)
    wire = response.model_dump(mode="json")
    assert response.calculation_contract_version == LEGACY_CALCULATION_CONTRACT
    assert response.indicators.eps_basic is None
    assert response.indicators.pe_basic is None
    assert response.indicator_contract["pe_basic"].denominator == "cpc41_basic_eps"
    assert response.indicator_contract["pe_basic"].share_basis == (
        "cpc41_weighted_average_class_rights"
    )
    assert "eps" not in wire["indicators"]
    assert "eps_basic_market" not in wire["indicators"]
    assert "pe_basic_market" not in wire["indicators"]
    assert "eps" not in wire["indicators"]
    assert "eps_basic_market" not in wire["indicators"]["null_reasons"]
    assert wire["indicators"]["null_reasons"]["eps_basic"] == (
        "missing_weighted_average_shares"
    )
    assert all(
        "tier" not in contract for contract in wire["indicator_contract"].values()
    )
    assert row.indicators.eps_basic_market == Decimal("2")


def test_unknown_version_does_not_claim_a_current_formula() -> None:
    response = _to_response(
        replace(_analysis(VIEW_TTM), calculation_contract_version="future")
    )
    assert response.indicator_contract == {}


def test_api_schema_matches_selected_public_indicator_names() -> None:
    metadata = {
        "null_reasons",
        "source_account_evidence",
        "cpc41_window_provenance",
        "bank_regulatory_provenance",
    }
    assert set(IndicatorsResponse.model_fields) - metadata == set(
        public_indicator_names()
    )


def test_previous_equivalent_evidence_version_keeps_formula_metadata() -> None:
    response = _to_response(
        replace(
            _analysis(VIEW_TTM), calculation_contract_version="equivalent_evidence_v1"
        )
    )
    assert response.calculation_contract_version == "equivalent_evidence_v1"
    assert response.indicator_contract
    assert response.indicator_contract["pe_basic"].basis == "security_selected_evidence"
    assert response.indicator_contract["pe_basic"].denominator == "selected_basic_eps"
    assert response.indicator_contract["pe_basic"].share_basis == (
        "selected_weighted_average_class_rights"
    )


def test_superseded_capital_versions_keep_their_formula_metadata() -> None:
    for version in ("equivalent_evidence_v2", "equivalent_evidence_v3"):
        response = _to_response(
            replace(_analysis(VIEW_TTM), calculation_contract_version=version)
        )
        assert response.calculation_contract_version == version
        assert response.indicator_contract
        contract = response.indicator_contract["pe_basic"]
        assert contract.basis == "security_selected_evidence"
        assert contract.denominator == "selected_basic_eps"
        assert contract.share_basis == "selected_weighted_average_class_rights"
