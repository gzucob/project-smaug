"""PostgreSQL retention, concurrency, and committed-revision invariants."""

import asyncio
import importlib.util
import os
import uuid
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import Connection, func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from smaug.analysis.domain.entities import VIEW_CLOSED_YEAR, TickerAnalysis
from smaug.analysis.domain.indicators import Indicators, NullReason
from smaug.analysis.infrastructure.sql_repository import (
    SqlAlchemyAnalysisRepository,
    _to_row,
)
from smaug.analysis.infrastructure.sql_summary import SqlAnalysisSummaryReader
from smaug.analysis.infrastructure.sqlalchemy_models import Base, TickerAnalysisRow
from smaug.portfolio.domain.taxonomy import Classification
from smaug.portfolio.infrastructure.sqlalchemy_models import (
    PortfolioBase,
    PortfolioTickerRow,
)


@pytest.mark.skipif(
    not os.environ.get("SMAUG_TEST_POSTGRES_URI"),
    reason="Set SMAUG_TEST_POSTGRES_URI to test retention in an isolated schema",
)
async def test_migration_retains_results_and_saves_replace_atomically() -> None:
    dsn = os.environ["SMAUG_TEST_POSTGRES_URI"]
    schema = "retention_check_" + uuid.uuid4().hex
    admin = create_async_engine(dsn)
    async with admin.begin() as conn:
        await conn.execute(text("CREATE SCHEMA " + schema))
    engine = create_async_engine(
        dsn, connect_args={"server_settings": {"search_path": schema}}
    )
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await conn.run_sync(PortfolioBase.metadata.create_all)
            await conn.execute(text("DROP INDEX uq_ticker_analysis_current_ttm"))
            await conn.execute(text("DROP INDEX uq_ticker_analysis_current_year"))
            await conn.execute(text("DROP TABLE ui_data_revision"))
        now = datetime(2026, 10, 12, tzinfo=UTC)
        base = TickerAnalysis(
            ticker="TEST3",
            classification=Classification("Test", None, None),
            reference_date=date(2026, 3, 31),
            computed_at=now,
            indicators=Indicators(roe=Decimal(".2"), market_cap=Decimal("100")),
        )
        current = replace(
            base,
            reference_date=date(2026, 6, 30),
            computed_at=now + timedelta(seconds=1),
            indicators=Indicators(
                roe=None, null_reasons={"roe": NullReason.SOURCE_ACCOUNT_ABSENT}
            ),
        )
        year1 = replace(base, view=VIEW_CLOSED_YEAR, reference_date=date(2024, 12, 31))
        year2 = replace(base, view=VIEW_CLOSED_YEAR, reference_date=date(2025, 12, 31))
        year2new = replace(
            year2, computed_at=now + timedelta(seconds=1), indicators=current.indicators
        )
        async with sessions() as s:
            s.add_all(
                [
                    _to_row(base),
                    _to_row(current),
                    _to_row(year1),
                    _to_row(year2),
                    _to_row(year2new),
                ]
            )
            await s.commit()
        async with engine.connect() as c:
            expected = dict(
                (
                    await c.execute(
                        text(
                            "SELECT DISTINCT ON (ticker, view, "
                            "CASE WHEN view='closed_year' THEN reference_date END) "
                            "id, md5(to_jsonb(t)::text) FROM ticker_analysis t "
                            "ORDER BY ticker, view, "
                            "CASE WHEN view='closed_year' THEN reference_date END, "
                            "computed_at DESC, id DESC"
                        )
                    )
                ).all()
            )
        migration = (
            Path(__file__).resolve().parents[1]
            / "alembic/versions/0044_current_analysis_retention.py"
        )
        spec = importlib.util.spec_from_file_location("migration", migration)
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        def migrate(c: Connection) -> None:
            with Operations.context(MigrationContext.configure(c)):
                module.upgrade()

        async with engine.begin() as c:
            await c.run_sync(migrate)
        async with engine.connect() as c:
            actual = dict(
                (
                    await c.execute(
                        text("SELECT id,md5(to_jsonb(t)::text) FROM ticker_analysis t")
                    )
                ).all()
            )
            assert actual == expected, (actual, expected)
        repo = SqlAlchemyAnalysisRepository(sessions)
        assert await repo.latest("TEST3") == current
        assert await repo.history("TEST3") == [year1, year2new]
        summary = (await SqlAnalysisSummaryReader(sessions).all_latest())[0]
        assert summary.roe is None
        assert summary.market_cap is None
        newer = replace(
            current,
            computed_at=now + timedelta(seconds=5),
            reference_date=date(2026, 9, 30),
            indicators=Indicators(roe=Decimal(".3")),
        )
        await asyncio.gather(repo.save(newer), repo.save(base))
        assert await repo.latest("TEST3") == newer
        await repo.save(current)
        assert await repo.latest("TEST3") == newer
        changed_year = replace(
            year2new,
            computed_at=now + timedelta(seconds=6),
            indicators=newer.indicators,
        )
        await repo.save(changed_year)
        assert await repo.history("TEST3") == [year1, changed_year]
        async with sessions() as s:
            assert (
                await s.scalar(select(func.count()).select_from(TickerAnalysisRow))
            ) == 3
            revision = await s.scalar(text("SELECT generation FROM ui_data_revision"))
            assert revision > 1
            s.add(PortfolioTickerRow(ticker="TEST3", added_at=now))
            await s.flush()
            await s.rollback()
            assert (
                await s.scalar(text("SELECT generation FROM ui_data_revision"))
                == revision
            )
            s.add(PortfolioTickerRow(ticker="TEST3", added_at=now))
            await s.commit()
            assert (
                await s.scalar(text("SELECT generation FROM ui_data_revision"))
                == revision + 1
            )

    finally:
        await engine.dispose()
        async with admin.begin() as c:
            await c.execute(text("DROP SCHEMA " + schema + " CASCADE"))
        await admin.dispose()
