"""Versioned source-batch validation facts and their persistence boundary."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from smaug.ingestion.domain.runs import ParserIdentity


class BatchValidationStatus(StrEnum):
    """Whether a batch may enter the raw mirror."""

    ACCEPTED = "accepted"
    QUARANTINED = "quarantined"
    APPROVED = "approved"


@dataclass(frozen=True)
class ValidationRule:
    """One named, versioned rule applied to a source batch."""

    name: str
    version: int


@dataclass(frozen=True)
class ValidationFinding:
    """One reason a batch was quarantined."""

    code: str
    detail: str


@dataclass(frozen=True)
class SourceBatchValidation:
    """The reproducible validation result before it is attached to a run."""

    source: str
    batch: str
    parser: ParserIdentity
    rules: tuple[ValidationRule, ...]
    module: str | None = None
    artifact_id: str | None = None
    observations: Mapping[str, str | int | bool] = field(default_factory=dict)
    findings: tuple[ValidationFinding, ...] = ()
    # B3 returns JSON rather than a downloadable archive. This evidence is the
    # source response that made coverage impossible to establish, kept as filed.
    evidence: Mapping[str, object] = field(default_factory=dict)

    @property
    def status(self) -> BatchValidationStatus:
        """The admission decision implied by the recorded findings."""
        return (
            BatchValidationStatus.QUARANTINED
            if self.findings
            else BatchValidationStatus.ACCEPTED
        )

    @property
    def detail(self) -> str:
        """A compact diagnostic suitable for the collection log."""
        return "; ".join(f"{item.code}: {item.detail}" for item in self.findings)


@dataclass(frozen=True)
class IngestionValidationReport:
    """A validation fact tied to an ingestion run and persisted for diagnosis."""

    report_id: str
    run_id: str
    recorded_at: datetime
    validation: SourceBatchValidation
    status: BatchValidationStatus
    approved_at: datetime | None = None
    approval_note: str | None = None


class BatchValidationReporter(Protocol):
    """Receive validation facts from a source without coupling it to storage."""

    async def record(self, validation: SourceBatchValidation) -> None:
        """Persist one source-batch validation fact."""
        ...
