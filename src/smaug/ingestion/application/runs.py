"""Application service for ingestion-run lifecycle and diagnosis."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

from smaug.ingestion.application.ingest import FetchOutcome, OutcomeStatus
from smaug.ingestion.domain.repositories import IngestionRunRepository
from smaug.ingestion.domain.runs import (
    IngestionRun,
    IngestionRunCounts,
    IngestionRunParameters,
    IngestionRunStatus,
    ParserIdentity,
)

Clock = Callable[[], datetime]
IdFactory = Callable[[], str]
OutcomeSink = Callable[[FetchOutcome], Awaitable[None]]


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _new_id() -> str:
    return str(uuid4())


class IngestionRunService:
    """Start, finish, and query ingestion runs through a storage port."""

    def __init__(
        self,
        repository: IngestionRunRepository,
        *,
        clock: Clock = _utc_now,
        id_factory: IdFactory = _new_id,
    ) -> None:
        self._repository = repository
        self._clock = clock
        self._id_factory = id_factory

    async def start(
        self,
        parameters: IngestionRunParameters,
        *,
        application_commit: str,
        parsers: Sequence[ParserIdentity],
    ) -> IngestionRun:
        """Create the durable running marker before collection begins."""
        run = IngestionRun(
            run_id=self._id_factory(),
            started_at=self._clock(),
            ended_at=None,
            status=IngestionRunStatus.RUNNING,
            parameters=parameters,
            application_commit=application_commit,
            parsers=tuple(parsers),
        )
        return await self._repository.add(run)

    async def execute[T](
        self,
        parameters: IngestionRunParameters,
        *,
        application_commit: str,
        parsers: Sequence[ParserIdentity],
        operation: Callable[[str, OutcomeSink], Awaitable[T]],
    ) -> T:
        """Run collection under a lifecycle that survives failures and cancellation."""
        run = await self.start(
            parameters,
            application_commit=application_commit,
            parsers=parsers,
        )
        outcomes: list[FetchOutcome] = []

        async def outcome_sink(outcome: FetchOutcome) -> None:
            outcomes.append(outcome)
            await self.record_outcome(run.run_id, outcome)

        try:
            result = await operation(run.run_id, outcome_sink)
            await self.complete(run.run_id, outcomes)
        except (asyncio.CancelledError, KeyboardInterrupt) as exc:
            try:
                await asyncio.shield(
                    self.fail(run.run_id, outcomes, exc, interrupted=True)
                )
            finally:
                raise
        except Exception as exc:
            try:
                await self.fail(run.run_id, outcomes, exc)
            finally:
                raise
        return result

    async def record_outcome(self, run_id: str, outcome: FetchOutcome) -> IngestionRun:
        """Persist one known call result while the run is still in progress."""
        run = await self._required(run_id)
        field = outcome.status.value
        counts = replace(
            run.counts,
            **{field: getattr(run.counts, field) + 1},
        )
        return await self._repository.update(replace(run, counts=counts))

    async def plan_calls(self, run_id: str, count: int) -> IngestionRun:
        """Persist the command's full call space before source work begins."""
        run = await self._required(run_id)
        counts = replace(run.counts, planned=count)
        return await self._repository.update(replace(run, counts=counts))

    async def exclude_calls(self, run_id: str, count: int) -> IngestionRun:
        """Account for calls omitted because their source is already mirrored."""
        run = await self._required(run_id)
        counts = replace(run.counts, excluded=run.counts.excluded + count)
        return await self._repository.update(replace(run, counts=counts))

    async def resolve_tickers(
        self, run_id: str, tickers: Sequence[str]
    ) -> IngestionRun:
        """Record the concrete universe selected by an ``--all`` run."""
        run = await self._required(run_id)
        parameters = replace(run.parameters, tickers=tuple(tickers))
        return await self._repository.update(replace(run, parameters=parameters))

    async def record_artifact(self, run_id: str, artifact_id: str) -> IngestionRun:
        """Link immutable source content to a run with set semantics."""
        run = await self._required(run_id)
        artifact_ids = tuple(dict.fromkeys((*run.artifact_ids, artifact_id)))
        if artifact_ids == run.artifact_ids:
            return run
        return await self._repository.update(replace(run, artifact_ids=artifact_ids))

    async def complete(
        self, run_id: str, outcomes: Sequence[FetchOutcome]
    ) -> IngestionRun:
        """Persist a terminal state derived from all known call outcomes."""
        run = await self._required(run_id)
        counts = replace(
            _counts(outcomes),
            planned=run.counts.planned,
            excluded=run.counts.excluded,
        )
        if counts.aborted:
            status = IngestionRunStatus.ABORTED
        elif counts.remaining:
            status = IngestionRunStatus.FAILED
        elif counts.error or counts.quarantined:
            status = IngestionRunStatus.COMPLETED_WITH_ERRORS
        else:
            status = IngestionRunStatus.COMPLETED
        failure = (
            f"{counts.remaining} planned call(s) have no outcome"
            if counts.remaining
            else None
        )
        return await self._finish(run_id, status, counts=counts, failure=failure)

    async def fail(
        self,
        run_id: str,
        outcomes: Sequence[FetchOutcome],
        failure: BaseException,
        *,
        interrupted: bool = False,
    ) -> IngestionRun:
        """Persist the best-known state while allowing the failure to propagate."""
        status = (
            IngestionRunStatus.INTERRUPTED if interrupted else IngestionRunStatus.FAILED
        )
        detail = f"{type(failure).__name__}: {failure}"
        return await self._finish(
            run_id, status, counts=_counts(outcomes), failure=detail
        )

    async def get(self, run_id: str) -> IngestionRun | None:
        """Return one persisted run."""
        return await self._repository.get(run_id)

    async def recent(self, limit: int = 10) -> tuple[IngestionRun, ...]:
        """Return recent persisted runs for local diagnosis."""
        return await self._repository.recent(limit)

    async def _finish(
        self,
        run_id: str,
        status: IngestionRunStatus,
        *,
        counts: IngestionRunCounts,
        failure: str | None = None,
    ) -> IngestionRun:
        run = await self._required(run_id)
        counts = replace(
            counts,
            planned=run.counts.planned,
            excluded=run.counts.excluded,
        )
        terminal = replace(
            run,
            ended_at=self._clock(),
            status=status,
            counts=counts,
            failure=failure,
        )
        return await self._repository.update(terminal)

    async def _required(self, run_id: str) -> IngestionRun:
        run = await self._repository.get(run_id)
        if run is None:
            raise LookupError(f"ingestion run not found: {run_id}")
        return run


def _counts(outcomes: Sequence[FetchOutcome]) -> IngestionRunCounts:
    return IngestionRunCounts(
        stored=sum(outcome.status is OutcomeStatus.STORED for outcome in outcomes),
        unchanged=sum(
            outcome.status is OutcomeStatus.UNCHANGED for outcome in outcomes
        ),
        skipped=sum(outcome.status is OutcomeStatus.SKIPPED for outcome in outcomes),
        error=sum(outcome.status is OutcomeStatus.ERROR for outcome in outcomes),
        quarantined=sum(
            outcome.status is OutcomeStatus.QUARANTINED for outcome in outcomes
        ),
        aborted=sum(outcome.status is OutcomeStatus.ABORTED for outcome in outcomes),
    )
