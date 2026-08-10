"""Application service for recording and approving source-batch validations."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

from smaug.ingestion.domain.repositories import IngestionValidationRepository
from smaug.ingestion.domain.validation import (
    BatchValidationStatus,
    IngestionValidationReport,
    SourceBatchValidation,
)

Clock = Callable[[], datetime]
IdFactory = Callable[[], str]


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _new_id() -> str:
    return str(uuid4())


class IngestionValidationService:
    """Keep validation rule versions, evidence, and operator decisions durable."""

    def __init__(
        self,
        repository: IngestionValidationRepository,
        *,
        clock: Clock = _utc_now,
        id_factory: IdFactory = _new_id,
    ) -> None:
        self._repository = repository
        self._clock = clock
        self._id_factory = id_factory

    async def record(
        self, run_id: str, validation: SourceBatchValidation
    ) -> IngestionValidationReport:
        """Record an accepted or quarantined batch against its collection run."""
        report = IngestionValidationReport(
            report_id=self._id_factory(),
            run_id=run_id,
            recorded_at=self._clock(),
            validation=validation,
            status=validation.status,
        )
        return await self._repository.add(report)

    async def recent(
        self, limit: int = 20, *, run_id: str | None = None
    ) -> tuple[IngestionValidationReport, ...]:
        """Return validation reports for the diagnostic CLI."""
        return await self._repository.recent(limit, run_id=run_id)

    async def approve(self, report_id: str, note: str) -> IngestionValidationReport:
        """Record review of a quarantine; approval never writes its raw payload."""
        report = await self._repository.get(report_id)
        if report is None:
            raise LookupError(f"ingestion validation report not found: {report_id}")
        if report.status is BatchValidationStatus.APPROVED:
            return report
        if report.status is not BatchValidationStatus.QUARANTINED:
            raise ValueError("only quarantined validation reports can be approved")
        approved = replace(
            report,
            status=BatchValidationStatus.APPROVED,
            approved_at=self._clock(),
            approval_note=note,
        )
        return await self._repository.update(approved)


class RunValidationReporter:
    """Bind a source's validation observer to the ingestion run being executed."""

    def __init__(self, service: IngestionValidationService, run_id: str) -> None:
        self._service = service
        self._run_id = run_id

    async def record(self, validation: SourceBatchValidation) -> None:
        """Persist one validation result under the bound run."""
        await self._service.record(self._run_id, validation)
