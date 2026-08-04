"""PostgreSQL implementation of ``PortfolioRepository`` (async SQLAlchemy, #151)."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from smaug.portfolio.domain.entities import PortfolioTicker
from smaug.portfolio.infrastructure.sqlalchemy_models import PortfolioTickerRow


def _to_row(entity: PortfolioTicker) -> PortfolioTickerRow:
    return PortfolioTickerRow(ticker=entity.ticker, added_at=entity.added_at)


def _to_entity(row: PortfolioTickerRow) -> PortfolioTicker:
    return PortfolioTicker(ticker=row.ticker, added_at=row.added_at)


class SqlAlchemyPortfolioRepository:
    """Persists and reads back the user's favorited tickers."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def list(self) -> list[PortfolioTicker]:
        stmt = select(PortfolioTickerRow).order_by(PortfolioTickerRow.added_at)
        async with self._session_factory() as session:
            rows = (await session.execute(stmt)).scalars().all()
        return [_to_entity(row) for row in rows]

    async def add(self, ticker: str) -> PortfolioTicker:
        # Upsert: favoriting an already-favorited ticker keeps its original
        # ``added_at`` rather than raising a unique-violation or bumping it — so
        # the row read back afterwards is the true persisted state either way,
        # never a guessed "now" the conflict branch would have made up.
        stmt = insert(PortfolioTickerRow).values(
            ticker=ticker, added_at=datetime.now(UTC)
        )
        stmt = stmt.on_conflict_do_nothing(index_elements=["ticker"])
        async with self._session_factory() as session:
            await session.execute(stmt)
            await session.commit()
            row = await session.get(PortfolioTickerRow, ticker)
        if row is None:  # pragma: no cover - can't happen: just inserted or existed
            raise RuntimeError(f"portfolio row for {ticker} vanished after upsert")
        return _to_entity(row)

    async def remove(self, ticker: str) -> None:
        async with self._session_factory() as session:
            await session.execute(
                delete(PortfolioTickerRow).where(PortfolioTickerRow.ticker == ticker)
            )
            await session.commit()
