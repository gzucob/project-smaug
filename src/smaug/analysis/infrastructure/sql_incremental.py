"""Persist the incremental checkpoint on the publication connection."""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from smaug.analysis.domain.incremental import RefreshCheckpoint
from smaug.analysis.infrastructure.sqlalchemy_models import RefreshCheckpointRow


class SqlRefreshCheckpoint:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def read(self) -> RefreshCheckpoint | None:
        async with self._sessions() as session:
            row = await session.get(RefreshCheckpointRow, 1)
        return (
            RefreshCheckpoint(row.raw_id, row.archives, frozenset(row.retry_tickers))
            if row is not None
            else None
        )

    async def defer_ui_revision(self) -> None:
        """Leave portfolio writes free while the transaction prepares results."""
        async with self._sessions() as session:
            await session.execute(text("SET LOCAL smaug.incremental_refresh = 'on'"))
            await session.commit()

    async def save(
        self, checkpoint: RefreshCheckpoint, *, publish: bool = False
    ) -> None:
        async with self._sessions() as session:
            await session.merge(
                RefreshCheckpointRow(
                    id=1,
                    raw_id=checkpoint.raw_id,
                    archives=dict(checkpoint.archives),
                    retry_tickers=sorted(checkpoint.retry_tickers),
                )
            )
            if publish:
                await session.execute(
                    text(
                        "UPDATE ui_data_revision "
                        "SET generation = generation + 1 WHERE id = 1"
                    )
                )
            await session.commit()
