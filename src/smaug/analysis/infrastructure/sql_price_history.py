"""PostgreSQL storage for append-only daily price history snapshots."""

from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from smaug.analysis.domain.price_history import (
    HistoricalClose,
    PriceHistory,
    PriceHistoryGap,
)
from smaug.analysis.infrastructure.sqlalchemy_models import PriceHistoryRow


def _to_row(history: PriceHistory) -> PriceHistoryRow:
    return PriceHistoryRow(
        ticker=history.ticker,
        computed_at=history.computed_at,
        start_year=history.start_year,
        end_year=history.end_year,
        contract_version=history.contract_version,
        price_basis=history.price_basis,
        source=history.source,
        points=[
            {
                "session": item.session.isoformat(),
                "code": item.code,
                "as_traded": str(item.as_traded),
                "adjusted": str(item.adjusted),
                "factor": str(item.factor),
            }
            for item in history.points
        ],
        gaps=[{"year": item.year, "reason": item.reason} for item in history.gaps],
    )


def _to_entity(row: PriceHistoryRow) -> PriceHistory:
    return PriceHistory(
        ticker=row.ticker,
        computed_at=row.computed_at,
        start_year=row.start_year,
        end_year=row.end_year,
        contract_version=row.contract_version,
        price_basis=row.price_basis,
        source=row.source,
        points=tuple(
            HistoricalClose(
                date.fromisoformat(item["session"]),
                item["code"],
                Decimal(item["as_traded"]),
                Decimal(item["adjusted"]),
                Decimal(item["factor"]),
            )
            for item in row.points
        ),
        gaps=tuple(
            PriceHistoryGap(int(item["year"]), str(item["reason"])) for item in row.gaps
        ),
    )


class SqlPriceHistoryRepository:
    """Persist only from the CLI; the API invokes latest exclusively."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def save(self, history: PriceHistory) -> None:
        async with self._sessions() as session:
            session.add(_to_row(history))
            await session.commit()

    async def latest(self, ticker: str) -> PriceHistory | None:
        async with self._sessions() as session:
            row = await session.scalar(
                select(PriceHistoryRow)
                .where(PriceHistoryRow.ticker == ticker)
                .order_by(PriceHistoryRow.id.desc())
                .limit(1)
            )
            return None if row is None else _to_entity(row)
