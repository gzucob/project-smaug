"""Durable facts about source calls that did not complete."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from smaug.ingestion.domain.runs import ParserIdentity


class IngestionFailureClass(StrEnum):
    """Why one source call could not produce a raw mirror record."""

    TRANSIENT = "transient"
    PERMANENT = "permanent"
    VALIDATION = "validation"
    FATAL_SHARED_SOURCE = "fatal_shared_source"


class IngestionFailureStatus(StrEnum):
    """Whether a previously failed call still needs operator attention."""

    OPEN = "open"
    RESOLVED = "resolved"


@dataclass(frozen=True)
class FailureAttempt:
    """One command's exhausted attempt to complete an ingestion call."""

    run_id: str
    first_failed_at: datetime
    last_failed_at: datetime
    attempt_count: int
    failure_class: IngestionFailureClass
    detail: str
    artifact_id: str | None
    parser: ParserIdentity


@dataclass(frozen=True)
class FailureOccurrence:
    """A failed call awaiting durable recording by the application service."""

    ticker: str
    registrant: str | None
    source: str
    module: str
    year: int
    artifact_id: str | None
    parser: ParserIdentity
    failure_class: IngestionFailureClass
    attempt_count: int
    first_failed_at: datetime
    last_failed_at: datetime
    detail: str


@dataclass(frozen=True)
class IngestionFailure:
    """An unresolved call, retaining every failed command attempt."""

    failure_id: str
    origin_run_id: str
    ticker: str
    registrant: str | None
    source: str
    module: str
    year: int
    artifact_id: str | None
    parser: ParserIdentity
    failure_class: IngestionFailureClass
    attempt_count: int
    first_failed_at: datetime
    last_failed_at: datetime
    detail: str
    attempts: tuple[FailureAttempt, ...]
    status: IngestionFailureStatus = IngestionFailureStatus.OPEN
    resolved_at: datetime | None = None
    resolution_run_id: str | None = None
