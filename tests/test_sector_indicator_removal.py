"""Retired sector metrics disappear without leaking historical JSON."""

import importlib.util
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import sqlalchemy as sa
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext

from smaug.analysis.domain.entities import TickerAnalysis
from smaug.analysis.domain.financials import SourceAccountEvidence, SourceAccountStatus
from smaug.analysis.domain.indicators import Indicators, NullReason
from smaug.analysis.infrastructure.sql_repository import _to_entity, _to_row
from smaug.entrypoints.api import _to_response
from smaug.portfolio.domain.taxonomy import Classification


def test_historical_sector_metadata_is_filtered_by_sql_and_api() -> None:
    analysis = TickerAnalysis(
        ticker="BBAS3",
        classification=Classification("Financeiro", "Bancos", None),
        reference_date=date(2025, 12, 31),
        computed_at=datetime(2026, 10, 4, tzinfo=UTC),
        indicators=Indicators(
            roe=Decimal("0.15"),
            null_reasons={
                "net_interest_margin": NullReason.MISSING_REGULATORY_DISCLOSURE,
                "dividend_yield": NullReason.MISSING_PRICE,
            },
            source_account_evidence=(
                SourceAccountEvidence(
                    field="bank_net_interest[2025-12-31]",
                    statement="DRE",
                    status=SourceAccountStatus.MAPPED,
                    consumer_indicators=("net_interest_margin",),
                ),
                SourceAccountEvidence(
                    field="equity",
                    statement="BPP",
                    status=SourceAccountStatus.MAPPED,
                    consumer_indicators=("roe",),
                ),
            ),
        ),
    )
    restored = _to_entity(_to_row(analysis))
    assert restored.indicators.roe == Decimal("0.15")
    assert restored.indicators.null_reasons == {
        "dividend_yield": NullReason.MISSING_PRICE
    }
    assert [item.field for item in restored.indicators.source_account_evidence] == [
        "equity"
    ]
    for entity in (analysis, restored):
        response = _to_response(entity)
        assert "net_interest_margin" not in response.indicators.model_dump()
        assert "net_interest_margin" not in response.indicators.null_reasons
        assert [item.field for item in response.indicators.source_account_evidence] == [
            "equity"
        ]


def test_removal_migration_preserves_other_results_and_restores_nullable_schema() -> (
    None
):
    path = (
        Path(__file__).resolve().parents[1]
        / "alembic/versions/0034_remove_sector_indicators.py"
    )
    spec = importlib.util.spec_from_file_location("sector_removal_migration", path)
    assert spec is not None
    assert spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    retired = (
        "net_interest_margin",
        "efficiency_ratio",
        "cost_of_risk",
        "loss_ratio",
        "combined_ratio",
    )
    engine = sa.create_engine("sqlite:///:memory:")
    try:
        with engine.begin() as connection:
            table = sa.Table(
                "ticker_analysis",
                sa.MetaData(),
                sa.Column("id", sa.Integer(), primary_key=True),
                sa.Column("roe", sa.Numeric()),
                sa.Column("null_reasons", sa.JSON()),
                sa.Column("bank_regulatory_provenance", sa.JSON()),
                *(sa.Column(name, sa.Numeric()) for name in retired),
            )
            table.create(connection)
            connection.execute(
                table.insert().values(
                    id=1, roe=Decimal("0.15"), null_reasons={"loss_ratio": "x"}
                )
            )
            context = MigrationContext.configure(connection)
            with Operations.context(context):
                migration.upgrade()
            remaining = {
                item["name"]
                for item in sa.inspect(connection).get_columns("ticker_analysis")
            }
            assert remaining == {"id", "roe", "null_reasons"}
            assert (
                connection.execute(sa.text("SELECT roe FROM ticker_analysis")).scalar()
                == 0.15
            )
            assert (
                connection.execute(
                    sa.text("SELECT null_reasons FROM ticker_analysis")
                ).scalar()
                == '{"loss_ratio": "x"}'
            )
            with Operations.context(context):
                migration.downgrade()
            restored = sa.inspect(connection).get_columns("ticker_analysis")
            assert {item["name"] for item in restored} == set(table.columns.keys())
            assert all(item["nullable"] for item in restored if item["name"] in retired)
            assert (
                connection.execute(
                    sa.text("SELECT net_interest_margin FROM ticker_analysis")
                ).scalar()
                is None
            )
    finally:
        engine.dispose()
