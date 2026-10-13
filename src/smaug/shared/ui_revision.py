"""Read a small change token for persisted data displayed by the UI."""

import asyncio
import hashlib
from typing import Any

from pymongo.asynchronous.database import AsyncDatabase
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


class UiRevisionReader:
    """Combine committed SQL changes and append-only raw mirror insertions."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        mongo: AsyncDatabase[dict[str, Any]],  # Mongo driver document typing.
    ) -> None:
        self._sessions = sessions
        self._mongo = mongo

    async def latest(self) -> str:
        async with self._sessions() as session:
            generation = await session.scalar(
                text("SELECT generation FROM ui_data_revision WHERE id = 1")
            )
        # Both collections are append-only. Their primary-key indexes make these
        # probes independent of raw payload size; validation changes matter too.
        raw, validation, approval = await asyncio.gather(
            self._mongo["raw_ingestions"].find_one({}, {"_id": 1}, sort=[("_id", -1)]),
            self._mongo["ingestion_validations"].find_one(
                {}, {"_id": 1}, sort=[("_id", -1)]
            ),
            self._mongo["ingestion_validations"].find_one(
                {"approved_at": {"$ne": None}},
                {"_id": 1, "approved_at": 1},
                sort=[("approved_at", -1)],
            ),
        )
        token = f"{generation}:{raw}:{validation}:{approval}"
        return hashlib.sha256(token.encode()).hexdigest()
