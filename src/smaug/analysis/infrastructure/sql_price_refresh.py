"""Refresh status and a connection-scoped PostgreSQL worker lock."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import cast

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from smaug.analysis.domain.outcomes import AnalysisStatus, NoAnalysisReason
from smaug.analysis.domain.price_refresh import (
    PriceRefreshIssue,
    PriceRefreshStatus,
    RefreshIssueReason,
)
from smaug.analysis.infrastructure.sqlalchemy_models import PriceRefreshRow

# All local API processes share one CLI worker per database.
_LOCK_KEY = 73492041


class SqlPriceRefreshRepository:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def read(self) -> PriceRefreshStatus:
        async with self._sessions() as session:
            row = await session.get(PriceRefreshRow, 1)
            if row is None:
                return PriceRefreshStatus()
            state = row.state
            if state == "running":
                locked = await session.scalar(
                    text(
                        "SELECT EXISTS (SELECT 1 FROM pg_locks "
                        "WHERE locktype = 'advisory' AND classid = 0 "
                        "AND objid = :key AND objsubid = 1 AND granted "
                        "AND database = (SELECT oid FROM pg_database "
                        "WHERE datname = current_database()))"
                    ),
                    {"key": _LOCK_KEY},
                )
                if not locked:
                    state = "failed"
            return PriceRefreshStatus(
                state=state,
                started_at=row.started_at,
                checked_at=row.checked_at,
                completed_at=row.completed_at,
                updated_count=row.updated_count,
                unchanged_count=row.unchanged_count,
                issues=tuple(_to_issue(item) for item in row.issues or []),
            )

    async def save(self, status: PriceRefreshStatus) -> None:
        async with self._sessions() as session:
            await session.merge(
                PriceRefreshRow(
                    id=1,
                    state=status.state,
                    started_at=status.started_at,
                    checked_at=status.checked_at,
                    completed_at=status.completed_at,
                    updated_count=status.updated_count,
                    unchanged_count=status.unchanged_count,
                    issues=[_to_json(issue) for issue in status.issues],
                )
            )
            await session.commit()


def _to_issue(item: dict[str, str]) -> PriceRefreshIssue:
    return PriceRefreshIssue(
        item["ticker"],
        cast(RefreshIssueReason, item["reason"]),
        AnalysisStatus(item["analysis_status"]) if "analysis_status" in item else None,
        NoAnalysisReason(item["no_analysis_reason"])
        if "no_analysis_reason" in item
        else None,
        item.get("detail"),
    )


def _to_json(issue: PriceRefreshIssue) -> dict[str, str]:
    data = {"ticker": issue.ticker, "reason": issue.reason}
    if issue.analysis_status is not None:
        data["analysis_status"] = issue.analysis_status.value
    if issue.no_analysis_reason is not None:
        data["no_analysis_reason"] = issue.no_analysis_reason.value
    if issue.detail is not None:
        data["detail"] = issue.detail
    return data


@asynccontextmanager
async def price_refresh_lock(
    engine: AsyncEngine, *, wait: bool = False
) -> AsyncIterator[bool]:
    """Hold the lock until shutdown; PostgreSQL releases it on process death."""
    async with engine.connect() as connection:
        params = {"key": _LOCK_KEY}
        if wait:
            await connection.execute(text("SELECT pg_advisory_lock(:key)"), params)
            locked = True
        else:
            locked = bool(
                await connection.scalar(
                    text("SELECT pg_try_advisory_lock(:key)"), params
                )
            )
        try:
            yield locked
        finally:
            if locked:
                await connection.execute(
                    text("SELECT pg_advisory_unlock(:key)"), {"key": _LOCK_KEY}
                )
