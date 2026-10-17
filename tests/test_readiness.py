"""Operational readiness distinguishes an empty migrated DB from broken services."""

from typing import Any
from unittest.mock import AsyncMock, MagicMock

from alembic.config import Config
from alembic.script import ScriptDirectory

from smaug.shared.readiness import ReadinessReader


def reader(*, populated: bool = False, schema: str | None = None) -> ReadinessReader:
    heads = ScriptDirectory.from_config(Config("alembic.ini")).get_heads()
    rows = MagicMock()
    rows.scalars.return_value = heads if schema is None else [schema]
    session = AsyncMock()
    session.execute.side_effect = [MagicMock(), rows]
    session.scalar.return_value = populated
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=session)
    context.__aexit__ = AsyncMock(return_value=False)
    sessions = MagicMock(return_value=context)
    mongo: Any = MagicMock()
    mongo.command = AsyncMock(return_value={"ok": 1})
    return ReadinessReader(sessions, mongo)


async def test_fresh_migrated_installation_is_ready_without_prepared_data() -> None:
    result = await reader().read()
    assert result == {
        "ready": True,
        "mongo": "ok",
        "postgres": "ok",
        "schema": "ok",
        "data": "empty",
    }


async def test_prepared_data_and_schema_mismatch_are_distinct() -> None:
    assert (await reader(populated=True).read())["data"] == "prepared"
    result = await reader(schema="old_schema").read()
    assert result["ready"] is False
    assert result["schema"] == "migration_required"


async def test_unavailable_databases_fail_closed_without_leaking_details() -> None:
    sessions = MagicMock(side_effect=RuntimeError("private database details"))
    mongo: Any = MagicMock()
    mongo.command = AsyncMock(side_effect=RuntimeError("private Mongo details"))
    result = await ReadinessReader(sessions, mongo).read()
    assert result["ready"] is False
    assert result["mongo"] == "unavailable"
    assert result["postgres"] == "unavailable"
    assert "private" not in str(result)
