"""SQL row <-> entity mapping for the null-reason map (no database connection).

``_to_row`` / ``_to_entity`` are pure attribute mappers, so they are exercised
directly on a transient ORM instance — what matters is that the ``NullReason``
enum survives the round trip as plain strings, and that rows persisted before
the vocabulary existed (NULL column) degrade to "unclassified" ({}).
"""

from datetime import UTC, date, datetime
from decimal import Decimal

from smaug.analysis.domain.entities import VIEW_TTM, TickerAnalysis
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
from smaug.analysis.domain.indicators import Indicators, NullReason
from smaug.analysis.infrastructure.sql_repository import _to_entity, _to_row
from smaug.portfolio.domain.taxonomy import Classification


def _analysis() -> TickerAnalysis:
    return TickerAnalysis(
        ticker="BBAS3",
        classification=Classification(
            "Financeiro", "Intermediários Financeiros", "Bancos"
        ),
        reference_date=date(2024, 12, 31),
        computed_at=datetime(2026, 7, 10, tzinfo=UTC),
        view=VIEW_TTM,
        price_basis="b3_latest_close",
        share_count_basis="cvm_latest_filed_outstanding_current_base",
        liquidity_basis="cpc03_cash_and_cash_equivalents",
        debt_basis="cvm_bpp_explicit_interest_bearing",
        roic_tax_basis="br_statutory_34pct",
        filed_regime=AccountingRegime.BANK,
        regime_source=RegimeSource.FILED,
        issuer_name="Banco Teste S.A.",
        cd_cvm="1234",
        cnpj="12.345.678/0001-90",
        debt_evidence=DebtCoverageEvidence(
            regime=AccountingRegime.BANK,
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
                    "2.01.05.02.06",
                    "Passivos financeiros",
                    Decimal("25"),
                    DebtLineRole.EXCLUDED_LIABILITY,
                    DebtBlocker.AMBIGUOUS_FINANCIAL_LIABILITY,
                    DebtInstrument.GENERIC_FINANCIAL_LIABILITY,
                ),
            ),
            included_instruments=("2.01.04",),
            primary_blocker=DebtBlocker.INCOMPLETE_DEBT_COVERAGE,
            secondary_blockers=(DebtBlocker.AMBIGUOUS_FINANCIAL_LIABILITY,),
        ),
        debt_evidence_snapshot=DebtEvidenceSnapshot.CURRENT,
        indicators=Indicators(
            roe=Decimal("0.2"),
            loss_ratio=Decimal("0.72"),
            combined_ratio=Decimal("0.94"),
            null_reasons={
                "net_debt": NullReason.INAPPLICABLE_REGIME,
                "fcf": NullReason.SOURCE_ACCOUNT_UNMAPPED,
            },
        ),
    )


def test_null_reasons_round_trip_through_the_row() -> None:
    row = _to_row(_analysis())

    assert row.null_reasons == {
        "net_debt": "inapplicable_regime",
        "fcf": "source_account_unmapped",
    }

    entity = _to_entity(row)

    assert entity.indicators.null_reasons == {
        "net_debt": NullReason.INAPPLICABLE_REGIME,
        "fcf": NullReason.SOURCE_ACCOUNT_UNMAPPED,
    }
    assert entity.indicators.roe == Decimal("0.2")
    assert entity.indicators.loss_ratio == Decimal("0.72")
    assert entity.indicators.combined_ratio == Decimal("0.94")
    assert entity.price_basis == "b3_latest_close"
    assert entity.share_count_basis == "cvm_latest_filed_outstanding_current_base"
    assert entity.liquidity_basis == "cpc03_cash_and_cash_equivalents"
    assert entity.debt_basis == "cvm_bpp_explicit_interest_bearing"
    assert entity.roic_tax_basis == "br_statutory_34pct"
    assert row.filed_regime == "bank"
    assert row.regime_source == "filed"
    assert entity.filed_regime is AccountingRegime.BANK
    assert entity.regime_source is RegimeSource.FILED
    assert row.issuer_name == "Banco Teste S.A."
    assert row.issuer_cd_cvm == "1234"
    assert row.issuer_cnpj == "12.345.678/0001-90"
    assert row.debt_evidence_snapshot == "current"
    assert row.debt_evidence is not None
    assert row.debt_evidence["used_lines"][0]["code"] == "2.01.04"
    assert row.debt_evidence["used_lines"][0]["instrument"] == "loans_financing"
    assert row.debt_evidence["used_lines"][0]["classification"] == "included"
    assert row.debt_evidence["excluded_lines"][0]["reason"] == (
        "ambiguous_financial_liability"
    )
    assert row.debt_evidence["excluded_lines"][0]["instrument"] == (
        "generic_financial_liability"
    )
    assert row.debt_evidence["excluded_lines"][0]["classification"] == "ambiguous"
    assert entity.issuer_name == "Banco Teste S.A."
    assert entity.cd_cvm == "1234"
    assert entity.cnpj == "12.345.678/0001-90"
    assert entity.debt_evidence == _analysis().debt_evidence
    assert entity.debt_evidence is not None
    assert entity.debt_evidence.used_lines[0].classification is (
        DebtLineClassification.INCLUDED
    )
    assert entity.debt_evidence_snapshot is DebtEvidenceSnapshot.CURRENT


def test_pre_vocabulary_rows_degrade_to_unclassified() -> None:
    row = _to_row(_analysis())
    row.null_reasons = None  # a row persisted before migration 0005

    assert _to_entity(row).indicators.null_reasons == {}
