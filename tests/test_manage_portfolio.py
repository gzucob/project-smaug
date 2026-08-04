"""ManagePortfolioUseCase: shape validation on add, pass-through otherwise."""

from datetime import UTC, datetime

import pytest

from smaug.portfolio.application.manage_portfolio import ManagePortfolioUseCase
from smaug.portfolio.domain.entities import PortfolioTicker
from smaug.shared.errors import UnknownTickerError


class FakePortfolioRepository:
    def __init__(self) -> None:
        self.entries: dict[str, PortfolioTicker] = {}

    async def list(self) -> list[PortfolioTicker]:
        return sorted(self.entries.values(), key=lambda p: p.added_at)

    async def add(self, ticker: str) -> PortfolioTicker:
        existing = self.entries.get(ticker)
        if existing is not None:
            return existing
        entry = PortfolioTicker(
            ticker=ticker, added_at=datetime(2026, 8, 4, tzinfo=UTC)
        )
        self.entries[ticker] = entry
        return entry

    async def remove(self, ticker: str) -> None:
        self.entries.pop(ticker, None)


async def test_add_uppercases_and_trims_before_storing() -> None:
    repo = FakePortfolioRepository()
    use_case = ManagePortfolioUseCase(repo)

    entry = await use_case.add(" petr4 ")

    assert entry.ticker == "PETR4"
    assert [p.ticker for p in await use_case.list()] == ["PETR4"]


async def test_add_is_idempotent_and_keeps_the_original_added_at() -> None:
    repo = FakePortfolioRepository()
    use_case = ManagePortfolioUseCase(repo)

    first = await use_case.add("PETR4")
    second = await use_case.add("PETR4")

    assert first == second
    assert len(await use_case.list()) == 1


async def test_add_rejects_a_ticker_with_the_wrong_shape() -> None:
    use_case = ManagePortfolioUseCase(FakePortfolioRepository())

    with pytest.raises(UnknownTickerError, match="NOTATICKER"):
        await use_case.add("NOTATICKER")


async def test_remove_is_idempotent_when_the_ticker_is_absent() -> None:
    use_case = ManagePortfolioUseCase(FakePortfolioRepository())

    await use_case.remove("PETR4")  # no raise

    assert await use_case.list() == []
