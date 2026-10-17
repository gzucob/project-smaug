"""Read-only operational probes; no CVM/B3 requests or data preparation."""

import asyncio
import os
from typing import Any

from alembic.config import Config
from alembic.script import ScriptDirectory
from pymongo.asynchronous.database import AsyncDatabase
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


class ReadinessReader:
    """Check database connectivity and the migration shipped with this image."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        mongo: AsyncDatabase[dict[str, Any]],
    ) -> None:
        self._sessions = sessions
        self._mongo = mongo

    async def read(self) -> dict[str, str | bool]:
        """A fresh, migrated installation is ready even before ingestion."""
        status: dict[str, str | bool] = {
            "ready": False,
            "mongo": "unavailable",
            "postgres": "unavailable",
            "schema": "unavailable",
            "data": "unavailable",
        }
        try:
            async with asyncio.timeout(5):
                await self._mongo.command("ping")
            status["mongo"] = "ok"
        except Exception:
            pass
        try:
            config = Config(os.getenv("SMAUG_ALEMBIC_CONFIG", "alembic.ini"))
            expected = set(ScriptDirectory.from_config(config).get_heads())
            async with asyncio.timeout(5), self._sessions() as session:
                await session.execute(text("SELECT 1"))
                status["postgres"] = "ok"
                rows = await session.execute(
                    text("SELECT version_num FROM alembic_version")
                )
                actual = set(rows.scalars())
                status["schema"] = (
                    "ok" if expected and actual == expected else "migration_required"
                )
                if status["schema"] == "ok":
                    populated = await session.scalar(
                        text("SELECT EXISTS (SELECT 1 FROM ticker_analysis)")
                    )
                    status["data"] = "prepared" if populated else "empty"
        except Exception:
            pass
        status["ready"] = (
            all(status[key] == "ok" for key in ("mongo", "postgres", "schema"))
            and status["data"] != "unavailable"
        )
        return status
