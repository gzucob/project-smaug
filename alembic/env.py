"""Alembic environment — async, URL from settings, no file-based logging config."""

from __future__ import annotations

import asyncio
from typing import Any

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

from smaug.analysis.infrastructure.sqlalchemy_models import Base
from smaug.shared.config import get_settings

target_metadata = Base.metadata


def _url() -> str:
    return get_settings().postgres_uri


def run_migrations_offline() -> None:
    context.configure(
        url=_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def _do_run_migrations(connection: Any) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    engine = create_async_engine(_url())
    async with engine.connect() as connection:
        await connection.run_sync(_do_run_migrations)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
