"""Durable status of the local CLI's automatic price refresh."""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

from smaug.analysis.domain.outcomes import AnalysisStatus, NoAnalysisReason

RefreshIssueReason = Literal[
    "history_unavailable",
    "history_incomplete",
    "quote_unavailable",
    "class_quote_unavailable",
    "analysis_failed",
    "identity_unresolved",
    "ingestion_deferred",
    "issuer_refresh_deferred",
    "record_rejected",
    "source_error",
    "unexpected_error",
]


@dataclass(frozen=True)
class PriceRefreshIssue:
    ticker: str
    reason: RefreshIssueReason
    analysis_status: AnalysisStatus | None = None
    no_analysis_reason: NoAnalysisReason | None = None
    detail: str | None = None


@dataclass(frozen=True)
class PriceRefreshStatus:
    state: Literal["idle", "running", "completed", "failed"] = "idle"
    started_at: datetime | None = None
    checked_at: datetime | None = None
    completed_at: datetime | None = None
    updated_count: int | None = None
    unchanged_count: int | None = None
    issues: tuple[PriceRefreshIssue, ...] = ()

    @property
    def retry_tickers(self) -> frozenset[str]:
        return frozenset(
            issue.ticker
            for issue in self.issues
            if issue.ticker not in {"B3", "CVM"} and issue.reason != "record_rejected"
        )


class PriceRefreshStatusRepository(Protocol):
    async def read(self) -> PriceRefreshStatus: ...

    async def save(self, status: PriceRefreshStatus) -> None: ...
