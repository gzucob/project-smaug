"""Daily price history preserves capital-event dates, gaps and read boundaries."""

from collections.abc import Sequence
from datetime import date
from decimal import Decimal

import httpx
import pytest

from smaug.analysis.application.price_history import (
    BuildPriceHistoryUseCase,
    ReadPriceHistoryUseCase,
)
from smaug.analysis.domain.capital import RestatementStep
from smaug.analysis.domain.price_history import PriceHistory, TradedClose
from smaug.analysis.infrastructure.sql_price_history import _to_entity, _to_row
from smaug.entrypoints import api


class MemoryHistory:
    def __init__(self) -> None:
        self.snapshots: list[PriceHistory] = []

    async def save(self, history: PriceHistory) -> None:
        self.snapshots.append(history)

    async def latest(self, ticker: str) -> PriceHistory | None:
        return next(
            (item for item in reversed(self.snapshots) if item.ticker == ticker), None
        )


class HistorySource:
    async def history_year(
        self, ticker: str, year: int
    ) -> tuple[Sequence[TradedClose], str | None]:
        if year != 2024:
            return (), "price_symbol_not_found"
        return (
            TradedClose(date(2024, 4, 15), "OLD3", Decimal("60")),
            TradedClose(date(2024, 4, 16), ticker, Decimal("30")),
        ), None


class Shares:
    async def restatement_timeline(self, ticker: str) -> Sequence[RestatementStep]:
        return (RestatementStep(date(2024, 4, 16), Decimal(2)),)


async def test_split_adjusts_only_sessions_before_ex_date() -> None:
    repository = MemoryHistory()
    history = await BuildPriceHistoryUseCase(
        HistorySource(), Shares(), repository
    ).execute(" new3 ", 2023, 2025)

    assert history.ticker == "NEW3"
    assert [point.adjusted for point in history.points] == [Decimal(30), Decimal(30)]
    assert [point.factor for point in history.points] == [Decimal(2), Decimal(1)]
    assert [point.code for point in history.points] == ["OLD3", "NEW3"]
    assert [gap.year for gap in history.gaps] == [2023, 2025]
    assert all(gap.reason == "price_symbol_not_found" for gap in history.gaps)
    assert await ReadPriceHistoryUseCase(repository).execute("new3") == history
    assert _to_entity(_to_row(history)) == history


async def test_rebuild_appends_instead_of_overwriting_history() -> None:
    repository = MemoryHistory()
    builder = BuildPriceHistoryUseCase(HistorySource(), Shares(), repository)
    await builder.execute("NEW3", 2024, 2024)
    latest = await builder.execute("NEW3", 2023, 2025)
    assert len(repository.snapshots) == 2
    assert await repository.latest("NEW3") == latest


async def test_invalid_window_does_not_save_a_snapshot() -> None:
    repository = MemoryHistory()
    with pytest.raises(ValueError, match="history years"):
        await BuildPriceHistoryUseCase(HistorySource(), Shares(), repository).execute(
            "NEW3", 2009, 2024
        )
    assert not repository.snapshots


async def test_failed_source_does_not_publish_a_partial_snapshot() -> None:
    class FailingSource(HistorySource):
        async def history_year(
            self, ticker: str, year: int
        ) -> tuple[Sequence[TradedClose], str | None]:
            if year == 2025:
                raise RuntimeError("archive unavailable")
            return await super().history_year(ticker, year)

    repository = MemoryHistory()
    with pytest.raises(RuntimeError, match="archive unavailable"):
        await BuildPriceHistoryUseCase(FailingSource(), Shares(), repository).execute(
            "NEW3", 2024, 2025
        )
    assert not repository.snapshots


async def test_api_reads_persisted_history_without_building_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = MemoryHistory()
    history = await BuildPriceHistoryUseCase(
        HistorySource(), Shares(), repository
    ).execute("NEW3", 2024, 2024)
    monkeypatch.setattr(api, "_price_history", ReadPriceHistoryUseCase(repository))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api.app), base_url="http://test"
    ) as client:
        response = await client.get("/prices/new3/history")
        missing = await client.get("/prices/MISSING3/history")
    assert response.status_code == 200
    payload = response.json()
    assert payload["price_basis"] == "split_adjusted"
    assert payload["source"] == "B3/COTAHIST"
    assert payload["points"][0]["as_traded"] == "60"
    assert payload["points"][0]["adjusted"] == "30"
    assert payload["computed_at"] == history.computed_at.isoformat().replace(
        "+00:00", "Z"
    )
    assert missing.status_code == 404
    assert missing.json()["detail"] == "price_history_not_prepared"
    assert len(repository.snapshots) == 1
