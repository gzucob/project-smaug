"""Offline recovery, interrupted runs and restart throttling."""

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest

from smaug.analysis.application.price_history import BuildPriceHistoryUseCase
from smaug.analysis.application.refresh_prices import (
    CheckPricesUseCase,
    RefreshPricesUseCase,
)
from smaug.analysis.domain.entities import TickerAnalysis
from smaug.analysis.domain.financials import MarketData
from smaug.analysis.domain.indicators import CALCULATION_CONTRACT_VERSION, Indicators
from smaug.analysis.domain.price_refresh import PriceRefreshIssue, PriceRefreshStatus
from smaug.analysis.infrastructure.b3_prices import CotahistArchive
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
    analyzer.execute.return_value.failed = ("NEW3",)
    refresher = RefreshPricesUseCase(builder, histories, AsyncMock(), prices, analyzer)
    assert await refresher.execute(("NEW3",)) == 1
    assert not histories.snapshots


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
            issues=(PriceRefreshIssue("AESO3", "history_unavailable"),),
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
        {"ticker": "AESO3", "reason": "history_unavailable"}
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
