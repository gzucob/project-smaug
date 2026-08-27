"""SQL row <-> entity mapping for analysis provenance (no database connection).

``_to_row`` / ``_to_entity`` are pure attribute mappers, so they are exercised
directly on a transient ORM instance — what matters is that the ``NullReason``
enum survives the round trip as plain strings, and that rows persisted before
the vocabulary existed (NULL column) degrade to "unclassified" ({}).
"""

from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy.dialects import postgresql
from sqlalchemy.sql import Select

from smaug.analysis.domain.entities import VIEW_TTM, TickerAnalysis
from smaug.analysis.domain.financials import (
    AccountingRegime,
    CapitalActionEvidence,
    CapitalComposition,
    ClassMarketValue,
    DebtBlocker,
    DebtCoverageEvidence,
    DebtEvidenceSnapshot,
    DebtIdentityStatus,
    DebtInstrument,
    DebtLineClassification,
    DebtLineEvidence,
    DebtLineRole,
    RegimeSource,
    ShareCountProvenance,
    ShareCounts,
)
from smaug.analysis.domain.indicators import Indicators, NullReason
from smaug.analysis.infrastructure.sql_repository import (
    SqlAlchemyAnalysisRepository,
    _legacy_row_condition,
    _to_entity,
    _to_row,
)
from smaug.portfolio.domain.share_classes import (
    EconomicRightsStatus,
    PerShareClass,
    ShareClassMapping,
    ShareClassMappingStatus,
    ShareKind,
    TickerCodeEvidence,
)
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
        price=Decimal("38.25"),
        price_source_code="AZZA3",
        price_source_session=date(2026, 8, 14),
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
    assert entity.price == Decimal("38.25")
    assert entity.price_source_code == "AZZA3"
    assert entity.price_source_session == date(2026, 8, 14)
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


def test_share_class_and_capital_provenance_round_trip() -> None:
    analysis = replace(
        _analysis(),
        share_class_mappings=(
            ShareClassMapping(
                class_id="12.345.678/0001-90:ON",
                symbol="BBAS3",
                kind=ShareKind.COMMON,
                per_share_class=PerShareClass.ORDINARY,
                code_evidence=(TickerCodeEvidence("BBAS3", (2018, 2019)),),
                evidence=("cvm_fca.share_class",),
            ),
            ShareClassMapping(
                class_id="12.345.678/0001-90:PN",
                symbol=None,
                kind=ShareKind.PREFERRED,
                per_share_class=PerShareClass.PREFERRED,
                status=ShareClassMappingStatus.UNRESOLVED,
                economic_rights=EconomicRightsStatus.UNRESOLVED,
                evidence=("cvm_fca.ambiguous_share_class",),
            ),
        ),
        class_market_values=(
            ClassMarketValue(
                class_id="12.345.678/0001-90:ON",
                symbol="BBAS3",
                per_share_class=PerShareClass.ORDINARY,
                price=Decimal("25"),
                shares=Decimal("100"),
                value=Decimal("2500"),
                price_basis="b3_latest_close",
                share_basis="cvm_latest_filed_outstanding_current_base",
            ),
        ),
        capital_provenance=ShareCountProvenance(
            requested_year=2025,
            filed_year=2025,
            status="resolved",
            issued=ShareCounts(common=Decimal("110"), total=Decimal("110")),
            outstanding=ShareCounts(common=Decimal("100"), total=Decimal("100")),
            treasury=CapitalComposition(
                issued_total=Decimal("110"), treasury_common=Decimal("10")
            ),
            restatement_factor=Decimal("2"),
            actions=(
                CapitalActionEvidence(
                    approval_date="2023-04-01",
                    kind="BONIFICACAO",
                    common_before=Decimal("50"),
                    common_after=Decimal("100"),
                    preferred_before=None,
                    preferred_after=None,
                    total_before=Decimal("50"),
                    total_after=Decimal("100"),
                ),
            ),
            evidence=("cvm_fre.issued", "cvm_dfp.treasury"),
        ),
    )

    row = _to_row(analysis)
    entity = _to_entity(row)

    assert row.share_class_mappings is not None
    assert row.share_class_mappings[1]["status"] == "unresolved"
    assert entity.share_class_mappings == analysis.share_class_mappings
    assert entity.class_market_values == analysis.class_market_values
    assert entity.capital_provenance == analysis.capital_provenance


class _ScopeResult:
    def one(self) -> tuple[int, int, int]:
        return (4, 2, 1)


class _ScopeSession:
    def __init__(self) -> None:
        self.statement: Select[tuple[object, ...]] | None = None

    async def __aenter__(self) -> "_ScopeSession":
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def execute(self, statement: Select[tuple[object, ...]]) -> _ScopeResult:
        self.statement = statement
        return _ScopeResult()


class _ScopeSessionFactory:
    def __init__(self, session: _ScopeSession) -> None:
        self.session = session

    def __call__(self) -> _ScopeSession:
        return self.session


async def test_storage_scope_is_aggregate_and_fallback_is_current() -> None:
    session = _ScopeSession()
    repository = SqlAlchemyAnalysisRepository(  # type: ignore[arg-type]
        _ScopeSessionFactory(session)
    )

    scope = await repository.storage_scope(("PETR4",))

    assert scope.persisted_rows == 4
    assert scope.stale_rows == 2
    assert scope.legacy_rows == 1
    assert session.statement is not None
    compiled = session.statement.compile(dialect=postgresql.dialect())
    assert len(tuple(session.statement.selected_columns)) == 3
    assert "sector_fallback" in compiled.params.values()
    assert "ticker_analysis.roe" not in str(compiled)


def test_legacy_sql_predicate_accepts_a_valid_sector_fallback() -> None:
    statement = _legacy_row_condition()
    compiled = statement.compile(dialect=postgresql.dialect())

    assert "sector_fallback" in compiled.params.values()
    # A fallback regime is current when all other attribution contracts exist;
    # this predicate only rejects a missing regime source or non-fallback gap.
    assert "filed_regime IS NULL" in str(compiled)
