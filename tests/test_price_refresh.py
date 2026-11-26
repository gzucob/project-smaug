"""Offline recovery, interrupted runs and restart throttling."""

import os
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from smaug.analysis.application.incremental import refresh_incrementally
from smaug.analysis.application.price_history import BuildPriceHistoryUseCase
from smaug.analysis.application.refresh_prices import (
    CheckPricesUseCase,
    RefreshPricesUseCase,
)
from smaug.analysis.domain.entities import TickerAnalysis
from smaug.analysis.domain.financials import MarketData
from smaug.analysis.domain.incremental import RefreshCheckpoint, RefreshScope
from smaug.analysis.domain.indicators import CALCULATION_CONTRACT_VERSION, Indicators
from smaug.analysis.domain.outcomes import (
    AnalysisRun,
    AnalysisStatus,
    NoAnalysisReason,
    TickerOutcome,
)
from smaug.analysis.domain.price_refresh import (
    PriceRefreshIssue,
    PriceRefreshStatus,
    PriceRefreshStatusRepository,
)
from smaug.analysis.infrastructure.b3_prices import CotahistArchive
from smaug.analysis.infrastructure.sql_incremental import SqlRefreshCheckpoint
from smaug.analysis.infrastructure.sql_price_refresh import SqlPriceRefreshRepository
from smaug.analysis.infrastructure.sqlalchemy_models import (
    PriceRefreshRow,
    RefreshCheckpointRow,
)
from smaug.portfolio.domain.taxonomy import Classification
from tests.test_price_history import HistorySource, MemoryHistory, Shares


class MemoryStatus:
    def __init__(self, status: PriceRefreshStatus | None = None) -> None:
        self.status = status or PriceRefreshStatus()

    async def read(self) -> PriceRefreshStatus:
        return self.status

    async def save(self, status: PriceRefreshStatus) -> None:
        self.status = status


async def test_months_offline_and_interrupted_run_trigger_immediate_check() -> None:
    old = datetime.now(UTC) - timedelta(days=60)
    repository = MemoryStatus(
        PriceRefreshStatus(state="running", started_at=old, completed_at=old)
    )
    refresh = AsyncMock(return_value=0)
    checker = CheckPricesUseCase(repository, refresh, timedelta(hours=6))
    assert await checker.execute() == 0
    refresh.assert_awaited_once()
    assert repository.status.state == "completed"
    assert repository.status.completed_at > old
    assert await checker.execute() == 0
    refresh.assert_awaited_once()


async def test_failed_check_preserves_completion_and_can_retry() -> None:
    old = datetime.now(UTC) - timedelta(days=60)
    repository = MemoryStatus(PriceRefreshStatus(completed_at=old))
    refresh = AsyncMock(side_effect=[RuntimeError("offline"), 0])
    checker = CheckPricesUseCase(repository, refresh, timedelta(hours=6))
    assert await checker.execute() == 1
    assert repository.status.state == "failed"
    assert repository.status.completed_at == old
    assert await checker.execute() == 0
    assert repository.status.state == "failed"
    assert refresh.await_count == 1
    repository.status = replace(repository.status, checked_at=old, started_at=old)
    assert await checker.execute() == 0
    assert repository.status.state == "completed"
    assert refresh.await_count == 2


@pytest.mark.parametrize(
    ("partial_tape", "unfinished_cvm"), [(True, False), (False, True)]
)
async def test_partial_source_writes_remain_unpublished(
    partial_tape: bool, unfinished_cvm: bool
) -> None:
    previous = RefreshCheckpoint(None, {})
    current = RefreshCheckpoint("published-row", {"COTAHIST:2026": "new"})
    publish = AsyncMock()
    cvm = AsyncMock(return_value=int(unfinished_cvm))
    result = await refresh_incrementally(
        previous,
        sync_prices=AsyncMock(return_value=int(partial_tape)),
        sync_fundamentals=cvm,
        checkpoint=AsyncMock(return_value=current),
        changes=AsyncMock(return_value=RefreshScope(frozenset({"123"}), partial_tape)),
        has_unfinished_disclosures=AsyncMock(return_value=unfinished_cvm),
        publish=publish,
    )
    assert result == 1
    cvm.assert_awaited_once()
    publish.assert_not_awaited()


async def test_fundamentals_can_publish_when_b3_is_offline_without_partial_writes() -> (
    None
):
    previous = RefreshCheckpoint(None, {})
    current = RefreshCheckpoint("published-row", {})
    scope = RefreshScope(frozenset({"123"}), False)
    publish = AsyncMock(return_value=0)
    result = await refresh_incrementally(
        previous,
        sync_prices=AsyncMock(side_effect=OSError),
        sync_fundamentals=AsyncMock(return_value=0),
        checkpoint=AsyncMock(return_value=current),
        changes=AsyncMock(return_value=scope),
        has_unfinished_disclosures=AsyncMock(return_value=False),
        publish=publish,
    )
    assert result == 1
    publish.assert_awaited_once_with(current, scope)


@pytest.mark.skipif(
    not os.environ.get("SMAUG_TEST_POSTGRES_URI"),
    reason="Set SMAUG_TEST_POSTGRES_URI to test atomic price refresh publication",
)
async def test_price_refresh_rolls_back_saved_results_before_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from smaug.entrypoints import cli

    uri = os.environ["SMAUG_TEST_POSTGRES_URI"]
    admin = create_async_engine(uri)
    schema = "price_refresh_test_" + uuid4().hex
    async with admin.begin() as connection:
        await connection.execute(text(f"CREATE SCHEMA {schema}"))
    engine = create_async_engine(
        uri, connect_args={"server_settings": {"search_path": schema}}
    )
    status = SqlPriceRefreshRepository(
        async_sessionmaker(engine, expire_on_commit=False)
    )
    prior = RefreshCheckpoint(None, {})
    current = RefreshCheckpoint(None, {"COTAHIST:2026": "new"})
    scope = RefreshScope(frozenset({"123"}), True)
    checkpoints = SqlRefreshCheckpoint(
        async_sessionmaker(engine, expire_on_commit=False)
    )
    calls = 0
    issue = PriceRefreshIssue(
        "NEW3",
        "analysis_failed",
        AnalysisStatus.SKIPPED,
        NoAnalysisReason.NO_FOUR_QUARTER_WINDOW,
        "Missing quarterly disclosures",
    )

    async def published() -> list[int]:
        async with engine.connect() as connection:
            result = await connection.scalars(
                text("SELECT value FROM published_results")
            )
            return sorted(result.all())

    async def analyze(
        tickers: tuple[str, ...],
        *,
        refresh_prices: bool,
        refresh_scope: RefreshScope,
        refresh_status_repository: PriceRefreshStatusRepository,
        write_sessions: async_sessionmaker[AsyncSession],
        group_transaction: object,
    ) -> int:
        assert not tickers
        nonlocal calls
        calls += 1
        assert refresh_prices
        assert refresh_scope == scope
        assert refresh_status_repository is status
        async with write_sessions() as session:
            await session.execute(text("INSERT INTO published_results VALUES (2)"))
            await session.commit()
        assert await published() == [1]
        assert await checkpoints.read() == prior
        if calls == 1:
            await refresh_status_repository.save(
                PriceRefreshStatus(state="failed", issues=(issue,))
            )
            return 1
        async with write_sessions() as session:
            await session.execute(text("INSERT INTO published_results VALUES (3)"))
            await session.commit()
        await refresh_status_repository.save(PriceRefreshStatus(updated_count=2))
        return 0

    monkeypatch.setattr(cli, "_run_analyze", analyze)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(RefreshCheckpointRow.__table__.create)
            await connection.run_sync(PriceRefreshRow.__table__.create)
            await connection.execute(
                text("CREATE TABLE ui_data_revision (id int, generation int)")
            )
            await connection.execute(
                text("CREATE TABLE published_results (value integer)")
            )
            await connection.execute(text("INSERT INTO published_results VALUES (1)"))
        await checkpoints.save(prior)
        assert await cli._publish_refresh(engine, status, prior, current, scope) == 1
        assert await published() == [1]
        assert await checkpoints.read() == prior
        assert (await status.read()).issues == (issue,)
        assert await cli._publish_refresh(engine, status, prior, current, scope) == 0
        assert await published() == [1, 2, 3]
        assert await checkpoints.read() == current
        assert await cli._publish_refresh(engine, status, current, current, scope) == 0
        assert calls == 2

        async def analyze_partial(
            _: tuple[str, ...],
            *,
            refresh_prices: bool,
            refresh_scope: RefreshScope,
            refresh_status_repository: PriceRefreshStatusRepository,
            write_sessions: async_sessionmaker[AsyncSession],
            group_transaction: Callable[[], AbstractAsyncContextManager[object]],
        ) -> int:
            assert refresh_prices
            histories = MemoryHistory()
            builder = BuildPriceHistoryUseCase(HistorySource(), Shares(), histories)
            prices = AsyncMock()
            prices.get.return_value = MarketData(
                price=Decimal(30), price_source_session=date(2024, 4, 16)
            )
            analyses = AsyncMock()
            analyses.latest.return_value = None
            analyzer = AsyncMock()

            async def analyze_group(tickers: list[str]) -> AnalysisRun:
                async with write_sessions() as session:
                    await session.execute(
                        text("INSERT INTO published_results VALUES (:value)"),
                        {"value": 4 if tickers == ["GOOD3"] else 99},
                    )
                    await session.commit()
                if tickers != ["GOOD3"]:
                    return AnalysisRun(
                        (
                            TickerOutcome(
                                "BAD4",
                                AnalysisStatus.ERROR,
                                (),
                                "failed after another class wrote",
                            ),
                        )
                    )
                entity = TickerAnalysis(
                    ticker="GOOD3",
                    classification=Classification(setor="Industrial"),
                    reference_date=date(2024, 12, 31),
                    computed_at=datetime.now(UTC),
                    indicators=Indicators(),
                )
                return AnalysisRun(
                    (TickerOutcome("GOOD3", AnalysisStatus.ANALYZED, (entity,)),)
                )

            analyzer.execute.side_effect = analyze_group
            symbols = (
                ("BAD3", "BAD4", "GOOD3")
                if refresh_scope.compare_prices
                else tuple(sorted(refresh_scope.retry_tickers))
            )
            refresh = RefreshPricesUseCase(
                builder,
                histories,
                analyses,
                prices,
                analyzer,
                refresh_status_repository,
            )
            assert (
                await refresh.execute(
                    symbols,
                    issuers={"BAD3": "issuer", "BAD4": "issuer"},
                    group_transaction=group_transaction,
                )
                == 1
            )
            return 0

        monkeypatch.setattr(cli, "_run_analyze", analyze_partial)
        changed = RefreshCheckpoint(None, {"COTAHIST:2026": "later"})
        assert await cli._publish_refresh(engine, status, current, changed, scope) == 0
        assert await published() == [1, 2, 3, 4]
        pending = replace(changed, retry_tickers=frozenset({"BAD3", "BAD4"}))
        assert await checkpoints.read() == pending
        assert (await status.read()).updated_count == 1
        assert (
            await cli._publish_refresh(
                engine, status, pending, changed, RefreshScope(frozenset(), False)
            )
            == 0
        )
        assert await published() == [1, 2, 3, 4]
        assert await checkpoints.read() == pending
        assert (await status.read()).updated_count == 0
    finally:
        await engine.dispose()
        async with admin.begin() as connection:
            await connection.execute(text(f"DROP SCHEMA {schema} CASCADE"))
        await admin.dispose()


async def test_recovery_saves_all_missed_sessions_then_skips_unchanged_data() -> None:
    histories = MemoryHistory()
    builder = BuildPriceHistoryUseCase(HistorySource(), Shares(), histories)
    full = await builder.execute("NEW3", 2024, 2024, persist=False)
    await histories.save(replace(full, points=full.points[:1]))
    quote = MarketData(
        price=Decimal(30),
        price_as_traded=Decimal(30),
        price_source_code="NEW3",
        price_source_session=date(2024, 4, 16),
    )
    analysis = TickerAnalysis(
        ticker="NEW3",
        classification=Classification(setor="Industrial"),
        reference_date=date(2024, 12, 31),
        computed_at=datetime.now(UTC),
        indicators=Indicators(),
        calculation_contract_version=CALCULATION_CONTRACT_VERSION,
        price=quote.price,
        price_as_traded=quote.price_as_traded,
        price_source_code=quote.price_source_code,
        price_source_session=quote.price_source_session,
    )
    prices = AsyncMock()
    prices.get.return_value = quote
    analyses = AsyncMock()
    analyses.latest.return_value = analysis
    analyzer = AsyncMock()
    analyzer.execute.return_value.failed = ()
    analyzer.execute.return_value.analyses = (analysis,)
    status = MemoryStatus()
    refresher = RefreshPricesUseCase(
        builder, histories, analyses, prices, analyzer, status_repository=status
    )
    assert await refresher.execute(("NEW3",)) == 0
    assert histories.snapshots[-1].points == full.points
    assert len(histories.snapshots) == 2
    assert status.status.updated_count == 1
    assert status.status.unchanged_count == 0
    assert not status.status.issues
    assert await refresher.execute(("NEW3",)) == 0
    assert len(histories.snapshots) == 2
    analyzer.execute.assert_awaited_once()
    assert status.status.updated_count == 0
    assert status.status.unchanged_count == 1


async def test_a_quote_change_recalculates_both_classes_with_their_issuer() -> None:
    quote = MarketData(
        price=Decimal(30),
        price_as_traded=Decimal(30),
        price_source_code="NEW3",
        price_source_session=date(2024, 4, 16),
    )
    history = MemoryHistory()
    builder = BuildPriceHistoryUseCase(HistorySource(), Shares(), history)
    symbols = ("NEW3", "NEW4", "OTHER3")
    saved = {symbol: await builder.execute(symbol, persist=False) for symbol in symbols}
    stored = AsyncMock()
    stored.latest.side_effect = saved.__getitem__
    analysis = TickerAnalysis(
        ticker="NEW3",
        classification=Classification(setor="Industrial"),
        reference_date=date(2024, 12, 31),
        computed_at=datetime.now(UTC) - timedelta(days=20),
        indicators=Indicators(),
        price=quote.price,
        price_as_traded=quote.price_as_traded,
        price_source_code=quote.price_source_code,
        price_source_session=quote.price_source_session,
    )
    analyses = AsyncMock()
    analyses.latest.return_value = analysis
    prices = AsyncMock()
    prices.get.side_effect = (quote, replace(quote, price=Decimal(35)), quote)
    analyzer = AsyncMock()
    analyzer.execute.return_value.failed = ()
    analyzer.execute.return_value.analyses = (
        analysis,
        replace(analysis, ticker="NEW4"),
    )
    status = MemoryStatus()
    refresher = RefreshPricesUseCase(
        builder, stored, analyses, prices, analyzer, status
    )

    assert await refresher.execute(symbols, issuers={"NEW3": "123", "NEW4": "123"}) == 0
    analyzer.execute.assert_awaited_once_with(["NEW3", "NEW4"])
    assert status.status.updated_count == 2
    assert status.status.unchanged_count == 1
    stored.save.assert_not_awaited()


async def test_rejected_record_keeps_issuer_values_while_other_securities_publish() -> (
    None
):
    histories = MemoryHistory()
    builder = BuildPriceHistoryUseCase(HistorySource(), Shares(), histories)
    previous = await builder.execute("NEW3", 2024, 2024)
    quote = MarketData(price=Decimal(30), price_source_session=date(2024, 4, 16))
    other = TickerAnalysis(
        ticker="OTHER3",
        classification=Classification(setor="Industrial"),
        reference_date=date(2024, 12, 31),
        computed_at=datetime.now(UTC),
        indicators=Indicators(),
    )
    analyses = AsyncMock()
    analyses.latest.return_value = None
    prices = AsyncMock()
    prices.get.return_value = quote
    analyzer = AsyncMock()
    analyzer.execute.return_value.failed = ()
    analyzer.execute.return_value.analyses = (other,)
    status = MemoryStatus()
    refresh = RefreshPricesUseCase(
        builder, histories, analyses, prices, analyzer, status
    )

    assert (
        await refresh.execute(
            ("NEW3", "NEW4", "OTHER3"),
            issuers={"NEW3": "123", "NEW4": "123"},
            blocked=frozenset({"NEW3"}),
        )
        == 0
    )
    prices.get.assert_awaited_once_with("OTHER3")
    analyzer.execute.assert_awaited_once_with(["OTHER3"])
    assert histories.snapshots[0] == previous
    assert histories.snapshots[1].ticker == "OTHER3"
    assert status.status.updated_count == 1
    assert status.status.issues == (
        PriceRefreshIssue("NEW3", "record_rejected"),
        PriceRefreshIssue("NEW4", "record_rejected"),
    )


async def test_unresolved_quote_does_not_replace_history_or_run_analysis() -> None:
    histories = MemoryHistory()
    builder = BuildPriceHistoryUseCase(HistorySource(), Shares(), histories)
    previous = await builder.execute("NEW3", 2024, 2024)
    prices = AsyncMock()
    prices.get.return_value.price = None
    analyses = AsyncMock()
    analyzer = AsyncMock()
    refresher = RefreshPricesUseCase(builder, histories, analyses, prices, analyzer)
    assert await refresher.execute(("NEW3",)) == 1
    assert histories.snapshots == [previous]
    analyzer.execute.assert_not_awaited()


def test_new_year_rejects_archive_cached_before_year_end(tmp_path: Path) -> None:
    archive = CotahistArchive(httpx.AsyncClient(), cache_dir=str(tmp_path))
    assert not archive._is_fresh(date(2025, 11, 1), 2025, date(2026, 2, 1))
    assert archive._is_fresh(date(2026, 1, 2), 2025, date(2026, 2, 1))
    assert not archive._is_fresh(date(2026, 1, 31), 2026, date(2026, 2, 1))


async def test_losing_saved_sessions_is_a_failure_before_analysis() -> None:
    histories = MemoryHistory()
    builder = BuildPriceHistoryUseCase(HistorySource(), Shares(), histories)
    full = await builder.execute("NEW3", 2024, 2024)
    builder.execute = AsyncMock(return_value=replace(full, points=full.points[:1]))
    analyzer = AsyncMock()
    refresher = RefreshPricesUseCase(
        builder, histories, AsyncMock(), AsyncMock(), analyzer
    )
    assert await refresher.execute(("NEW3",)) == 1
    assert histories.snapshots == [full]
    analyzer.execute.assert_not_awaited()


async def test_failed_analysis_leaves_history_pending_for_retry() -> None:
    histories = MemoryHistory()
    builder = BuildPriceHistoryUseCase(HistorySource(), Shares(), histories)
    prices = AsyncMock()
    prices.get.return_value.price = Decimal(30)
    prices.get.return_value.price_source_session = date(2024, 4, 16)
    analyzer = AsyncMock()
    analyzer.execute.return_value = AnalysisRun(
        (
            TickerOutcome(
                "NEW3",
                AnalysisStatus.SKIPPED,
                (),
                "No filed periods",
                NoAnalysisReason.NO_MIRRORED_FUNDAMENTALS,
            ),
            TickerOutcome(
                "NEW4", AnalysisStatus.ERROR, (), "ValueError: invalid market data"
            ),
        )
    )
    status = MemoryStatus()
    analyses = AsyncMock()
    analyses.latest.return_value = None
    refresher = RefreshPricesUseCase(
        builder, histories, analyses, prices, analyzer, status
    )
    assert (
        await refresher.execute(
            ("NEW3", "NEW4"), issuers={"NEW3": "123", "NEW4": "123"}
        )
        == 1
    )
    assert not histories.snapshots
    assert status.status.issues == (
        PriceRefreshIssue(
            "NEW3",
            "analysis_failed",
            AnalysisStatus.SKIPPED,
            NoAnalysisReason.NO_MIRRORED_FUNDAMENTALS,
            "No filed periods",
        ),
        PriceRefreshIssue(
            "NEW4",
            "analysis_failed",
            AnalysisStatus.ERROR,
            detail="ValueError: invalid market data",
        ),
    )


async def test_status_api_reads_completion_without_starting_a_refresh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from smaug.entrypoints import api

    completed = datetime.now(UTC)
    repository = MemoryStatus(
        PriceRefreshStatus(
            state="failed",
            checked_at=completed,
            updated_count=338,
            issues=(
                PriceRefreshIssue("AESO3", "history_unavailable"),
                PriceRefreshIssue(
                    "TEST3",
                    "analysis_failed",
                    AnalysisStatus.SKIPPED,
                    NoAnalysisReason.NO_MIRRORED_FUNDAMENTALS,
                    "No filed periods",
                ),
            ),
        )
    )
    monkeypatch.setattr(api, "SqlPriceRefreshRepository", lambda _: repository)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api.app), base_url="http://test"
    ) as client:
        response = await client.get("/prices/refresh-status")
    assert response.status_code == 200
    assert response.json()["state"] == "failed"
    assert response.json()["checked_at"] == completed.isoformat().replace("+00:00", "Z")
    assert response.json()["updated_count"] == 338
    assert response.json()["issues"] == [
        {"ticker": "AESO3", "reason": "history_unavailable"},
        {
            "ticker": "TEST3",
            "reason": "analysis_failed",
            "analysis_status": "skipped",
            "no_analysis_reason": "no_mirrored_fundamentals",
            "detail": "No filed periods",
        },
    ]


async def test_api_lifecycle_starts_and_cancels_the_local_cli_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio

    from smaug.entrypoints import api

    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def worker(interval: int) -> None:
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    monkeypatch.setattr(api._settings, "auto_price_refresh", True)
    reader = MagicMock()
    reader.read = AsyncMock(return_value={"ready": True, "data": "prepared"})
    monkeypatch.setattr(api, "ReadinessReader", MagicMock(return_value=reader))
    monkeypatch.setattr(api, "supervise_price_worker", worker)
    async with api._lifespan(api.app):
        await asyncio.wait_for(started.wait(), timeout=1)
    assert cancelled.is_set()


async def test_partial_check_keeps_counts_and_attempt_date() -> None:
    repository = MemoryStatus()

    async def partial_refresh() -> int:
        await repository.save(
            replace(
                repository.status,
                updated_count=2,
                unchanged_count=1,
                issues=(PriceRefreshIssue("AHEB5", "quote_unavailable"),),
            )
        )
        return 1

    checker = CheckPricesUseCase(repository, partial_refresh, timedelta(hours=6))
    assert await checker.execute() == 1
    assert repository.status.state == "failed"
    assert repository.status.checked_at is not None
    assert repository.status.completed_at is None
    assert repository.status.updated_count == 2
    assert repository.status.unchanged_count == 1
    assert repository.status.issues == (
        PriceRefreshIssue("AHEB5", "quote_unavailable"),
    )
