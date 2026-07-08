"""Async SQLAlchemy engine/session for the Phase 2 derived-data store."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from smaug.shared.config import Settings


def create_engine(settings: Settings) -> AsyncEngine:
    """Build the async engine from ``POSTGRES_URI`` (composition-root helper)."""
    return create_async_engine(settings.postgres_uri)


def create_session_factory(
    engine: AsyncEngine,
) -> async_sessionmaker[AsyncSession]:
    """Session factory that keeps attributes usable after commit."""
    return async_sessionmaker(engine, expire_on_commit=False)
