"""Execution provenance for ingestion commands."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class IngestionRunStatus(StrEnum):
    """Lifecycle states persisted for an ingestion command."""

    RUNNING = "running"
    COMPLETED = "completed"
    COMPLETED_WITH_ERRORS = "completed_with_errors"
    ABORTED = "aborted"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


class TickerScope(StrEnum):
    """How the command selected its companies."""

    PORTFOLIO = "portfolio"
    EXPLICIT = "explicit"
    ALL = "all"


@dataclass(frozen=True)
class ParserIdentity:
    """Stable parser identity, independent of its Python package or class."""

    name: str
    version: int


@dataclass(frozen=True)
class IngestionRunParameters:
    """Declared command parameters and their resolved collection scope."""

    ticker_scope: TickerScope
    tickers: tuple[str, ...]
    years: tuple[int, ...]
    document: str
    modules: tuple[str, ...]
    force: bool
    verbose: bool


@dataclass(frozen=True)
class IngestionRunCounts:
    """Terminal call counts, using the ingestion use case's outcome unit."""

    planned: int = 0
    excluded: int = 0
    stored: int = 0
    skipped: int = 0
    error: int = 0
    aborted: int = 0

    @property
    def attempted(self) -> int:
        """Number of source calls with a known outcome."""
        return self.stored + self.skipped + self.error + self.aborted

    @property
    def remaining(self) -> int:
        """Planned calls that have no known terminal outcome yet."""
        return max(self.planned - self.excluded - self.attempted, 0)


@dataclass(frozen=True)
class IngestionRun:
    """One durable ingestion command and its best-known result."""

    run_id: str
    started_at: datetime
    ended_at: datetime | None
    status: IngestionRunStatus
    parameters: IngestionRunParameters
    application_commit: str
    parsers: tuple[ParserIdentity, ...]
    artifact_ids: tuple[str, ...] = ()
    counts: IngestionRunCounts = IngestionRunCounts()
    failure: str | None = None
