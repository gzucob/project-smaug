"""Committed SQL changes and raw evidence both invalidate the UI token."""

from typing import cast
from unittest.mock import AsyncMock, MagicMock

from pymongo.asynchronous.database import AsyncDatabase
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from smaug.shared.ui_revision import UiRevisionReader


async def test_revision_changes_after_commit_ingestion_and_approval() -> None:
    session = MagicMock()
    session.scalar = AsyncMock(return_value=1)
    sessions = MagicMock()
    sessions.return_value.__aenter__ = AsyncMock(return_value=session)
    raw = MagicMock()
    raw.find_one = AsyncMock(return_value={"_id": "raw-1"})
    validation = MagicMock()
    validation.find_one = AsyncMock(
        side_effect=lambda query, *_args, **_options: (
            {"_id": "validation-1"} if not query else None
        )
    )
    mongo = {
        "raw_ingestions": raw,
        "ingestion_validations": validation,
    }
    reader = UiRevisionReader(
        cast("async_sessionmaker[AsyncSession]", sessions),
        cast("AsyncDatabase[dict[str, object]]", mongo),
    )

    original = await reader.latest()
    assert await reader.latest() == original
    session.scalar.return_value = 2
    committed = await reader.latest()
    assert committed != original
    raw.find_one.return_value = {"_id": "raw-2"}
    ingested = await reader.latest()
    assert ingested != committed
    validation.find_one.side_effect = lambda query, *_args, **_options: (
        {"_id": "validation-1", "approved_at": "now"}
        if query
        else {"_id": "validation-1"}
    )
    assert await reader.latest() != ingested
