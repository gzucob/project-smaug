"""Failure classification and durable retry-inventory lifecycle."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

from smaug.ingestion.domain.failures import (
    FailureAttempt,
    FailureOccurrence,
    IngestionFailure,
    IngestionFailureClass,
    IngestionFailureStatus,
)
from smaug.ingestion.domain.repositories import IngestionFailureRepository
from smaug.ingestion.domain.runs import ParserIdentity
from smaug.shared.errors import (
    CvmDownloadError,
    SourceAuthError,
    SourceBatchValidationError,
    SourceError,
    SourceForbiddenError,
    SourceNotFoundError,
    SourceRateLimitError,
)

Clock = Callable[[], datetime]
IdFactory = Callable[[], str]


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _new_id() -> str:
    return str(uuid4())


def classify_failure(error: SourceError) -> IngestionFailureClass:
    """Classify a source exception without changing its collection semantics."""
    if isinstance(error, (SourceNotFoundError, SourceForbiddenError)):
        return IngestionFailureClass.PERMANENT
    if isinstance(error, SourceBatchValidationError):
        return IngestionFailureClass.VALIDATION
    if isinstance(error, (SourceAuthError, SourceRateLimitError, CvmDownloadError)):
        return IngestionFailureClass.FATAL_SHARED_SOURCE
    return IngestionFailureClass.TRANSIENT


def sanitize_failure_detail(error: BaseException) -> str:
    """Keep an operator-useful, bounded error summary out of terminal formatting."""
    detail = " ".join(str(error).split())
    return detail[:500] if detail else type(error).__name__


class IngestionFailureService:
    """Append retries and resolve durable failed-call records."""

    def __init__(
        self,
        repository: IngestionFailureRepository,
        *,
        clock: Clock = _utc_now,
        id_factory: IdFactory = _new_id,
    ) -> None:
        self._repository = repository
        self._clock = clock
        self._id_factory = id_factory

    async def record(
        self,
        run_id: str,
        occurrence: FailureOccurrence,
        *,
        retry_of: str | None = None,
    ) -> IngestionFailure:
        """Store an initial failure or append an exhausted retry to it."""
        attempt = _attempt(run_id, occurrence)
        if retry_of is None:
            return await self._repository.add(
                IngestionFailure(
                    failure_id=self._id_factory(),
                    origin_run_id=run_id,
                    ticker=occurrence.ticker,
                    registrant=occurrence.registrant,
                    source=occurrence.source,
                    module=occurrence.module,
                    year=occurrence.year,
                    artifact_id=occurrence.artifact_id,
                    parser=occurrence.parser,
                    failure_class=occurrence.failure_class,
                    attempt_count=occurrence.attempt_count,
                    first_failed_at=occurrence.first_failed_at,
                    last_failed_at=occurrence.last_failed_at,
                    detail=occurrence.detail,
                    attempts=(attempt,),
                )
            )
        failure = await self._required(retry_of)
        if failure.status is not IngestionFailureStatus.OPEN:
            raise ValueError(f"ingestion failure is already resolved: {retry_of}")
        return await self._repository.update(
            replace(
                failure,
                artifact_id=occurrence.artifact_id,
                parser=occurrence.parser,
                failure_class=occurrence.failure_class,
                attempt_count=failure.attempt_count + occurrence.attempt_count,
                last_failed_at=occurrence.last_failed_at,
                detail=occurrence.detail,
                attempts=(*failure.attempts, attempt),
            )
        )

    async def resolve(self, failure_id: str, *, run_id: str) -> IngestionFailure:
        """Mark a successful retry resolved while keeping every failed attempt."""
        failure = await self._required(failure_id)
        if failure.status is IngestionFailureStatus.RESOLVED:
            return failure
        return await self._repository.update(
            replace(
                failure,
                status=IngestionFailureStatus.RESOLVED,
                resolved_at=self._clock(),
                resolution_run_id=run_id,
            )
        )

    async def eligible_for_run(
        self,
        run_id: str,
        *,
        current_parsers: Mapping[str, ParserIdentity],
        current_sources: Mapping[str, str],
        retry_permanent: bool = False,
    ) -> tuple[IngestionFailure, ...]:
        """Select automatic retries without reopening known permanent absences."""
        failures = await self._repository.open_for_run(run_id)
        return tuple(
            failure
            for failure in failures
            if _is_eligible(
                failure,
                current_parsers=current_parsers,
                current_sources=current_sources,
                retry_permanent=retry_permanent,
            )
        )

    async def recent(self, limit: int = 20) -> tuple[IngestionFailure, ...]:
        """Return recent records for operator diagnosis."""
        return await self._repository.recent(limit)

    async def _required(self, failure_id: str) -> IngestionFailure:
        failure = await self._repository.get(failure_id)
        if failure is None:
            raise LookupError(f"ingestion failure not found: {failure_id}")
        return failure


def _attempt(run_id: str, occurrence: FailureOccurrence) -> FailureAttempt:
    return FailureAttempt(
        run_id=run_id,
        first_failed_at=occurrence.first_failed_at,
        last_failed_at=occurrence.last_failed_at,
        attempt_count=occurrence.attempt_count,
        failure_class=occurrence.failure_class,
        detail=occurrence.detail,
        artifact_id=occurrence.artifact_id,
        parser=occurrence.parser,
    )


def _is_eligible(
    failure: IngestionFailure,
    *,
    current_parsers: Mapping[str, ParserIdentity],
    current_sources: Mapping[str, str],
    retry_permanent: bool,
) -> bool:
    parser_changed = current_parsers.get(failure.module) != failure.parser
    source_changed = current_sources.get(failure.module) != failure.source
    if (
        parser_changed
        or source_changed
        or failure.failure_class is IngestionFailureClass.TRANSIENT
    ):
        return True
    return retry_permanent and failure.failure_class is IngestionFailureClass.PERMANENT
