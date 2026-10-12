"""Durable status of the local CLI's automatic price refresh."""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

RefreshIssueReason = Literal[
    "history_unavailable",
    "history_incomplete",
    "quote_unavailable",
    "class_quote_unavailable",
    "analysis_failed",
    "source_error",
    "unexpected_error",
]


@dataclass(frozen=True)
class PriceRefreshIssue:
    ticker: str
    reason: RefreshIssueReason


@dataclass(frozen=True)
class PriceRefreshStatus:
    state: Literal["idle", "running", "completed", "failed"] = "idle"
    started_at: datetime | None = None
    checked_at: datetime | None = None
    completed_at: datetime | None = None
    updated_count: int | None = None
    unchanged_count: int | None = None
    issues: tuple[PriceRefreshIssue, ...] = ()


class PriceRefreshStatusRepository(Protocol):
    async def read(self) -> PriceRefreshStatus: ...

    async def save(self, status: PriceRefreshStatus) -> None: ...
