"""Ingestion-run lifecycle records completion, failures, and interruption."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from itertools import chain, repeat

import pytest

from smaug.ingestion.application.ingest import FetchOutcome, OutcomeStatus
from smaug.ingestion.application.runs import IngestionRunService
from smaug.ingestion.domain.runs import (
    IngestionRun,
    IngestionRunParameters,
    IngestionRunStatus,
    ParserIdentity,
    TickerScope,
)
from tests.fakes import FakeIngestionRunRepository

STARTED = datetime(2026, 8, 6, 12, 0, tzinfo=UTC)
OutcomeSink = Callable[[FetchOutcome], Awaitable[None]]


def _parameters() -> IngestionRunParameters:
    return IngestionRunParameters(
        ticker_scope=TickerScope.EXPLICIT,
        tickers=("PETR4",),
        years=(2024,),
        document="DFP",
        modules=("DRE",),
        force=False,
        verbose=False,
    )


def _service(repository: FakeIngestionRunRepository) -> IngestionRunService:
    times = chain((STARTED,), repeat(STARTED + timedelta(seconds=3)))
    return IngestionRunService(
        repository, clock=lambda: next(times), id_factory=lambda: "run-123"
    )


async def test_completed_run_records_scope_provenance_and_counts() -> None:
    repository = FakeIngestionRunRepository()
    service = _service(repository)
    seen_run_ids: list[str] = []

    async def operation(run_id: str, outcome_sink: OutcomeSink) -> None:
        seen_run_ids.append(run_id)
        await service.plan_calls(run_id, 3)
        await service.exclude_calls(run_id, 1)
        await service.record_artifact(run_id, "sha256:" + "a" * 64)
        await service.record_artifact(run_id, "sha256:" + "a" * 64)
        await outcome_sink(
            FetchOutcome("PETR4", "DRE", OutcomeStatus.STORED, 200, "1 period")
        )
        assert repository.items[run_id].status is IngestionRunStatus.RUNNING
        assert repository.items[run_id].counts.stored == 1
        await outcome_sink(
            FetchOutcome("PETR4", "BPA", OutcomeStatus.SKIPPED, 404, "missing")
        )

    await service.execute(
        _parameters(),
        application_commit="abc123",
        parsers=(ParserIdentity("cvm.statements.csv", 1),),
        operation=operation,
    )

    run = repository.items["run-123"]
    assert seen_run_ids == ["run-123"]
    assert run.status is IngestionRunStatus.COMPLETED
    assert run.ended_at == STARTED + timedelta(seconds=3)
    assert run.counts.stored == 1
    assert run.counts.skipped == 1
    assert run.counts.planned == 3
    assert run.counts.excluded == 1
    assert run.counts.remaining == 0
    assert run.application_commit == "abc123"
    assert run.parsers == (ParserIdentity("cvm.statements.csv", 1),)
    assert run.artifact_ids == ("sha256:" + "a" * 64,)


async def test_error_outcome_completes_with_errors_and_abort_is_terminal() -> None:
    for outcome_status, expected in (
        (OutcomeStatus.ERROR, IngestionRunStatus.COMPLETED_WITH_ERRORS),
        (OutcomeStatus.ABORTED, IngestionRunStatus.ABORTED),
    ):
        repository = FakeIngestionRunRepository()
        service = _service(repository)

        async def operation(
            _run_id: str,
            outcome_sink: OutcomeSink,
            status: OutcomeStatus = outcome_status,
        ) -> None:
            await outcome_sink(FetchOutcome("PETR4", "DRE", status, None, "bad"))

        await service.execute(
            _parameters(),
            application_commit="abc123",
            parsers=(),
            operation=operation,
        )

        assert repository.items["run-123"].status is expected


async def test_unexpected_exception_is_persisted_and_reraised() -> None:
    repository = FakeIngestionRunRepository()
    service = _service(repository)

    async def operation(_run_id: str, outcome_sink: OutcomeSink) -> None:
        await outcome_sink(
            FetchOutcome("PETR4", "DRE", OutcomeStatus.STORED, 200, "ok")
        )
        raise ValueError("broken parser")

    with pytest.raises(ValueError, match="broken parser"):
        await service.execute(
            _parameters(),
            application_commit="abc123",
            parsers=(),
            operation=operation,
        )

    run = repository.items["run-123"]
    assert run.status is IngestionRunStatus.FAILED
    assert run.counts.stored == 1
    assert run.failure == "ValueError: broken parser"


async def test_cancellation_is_persisted_as_interrupted_and_reraised() -> None:
    repository = FakeIngestionRunRepository()
    service = _service(repository)

    async def operation(_run_id: str, _outcome_sink: OutcomeSink) -> None:
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await service.execute(
            _parameters(),
            application_commit="abc123",
            parsers=(),
            operation=operation,
        )

    run = repository.items["run-123"]
    assert run.status is IngestionRunStatus.INTERRUPTED
    assert run.failure == "CancelledError: "


async def test_running_marker_exists_before_operation_finishes() -> None:
    repository = FakeIngestionRunRepository()
    service = _service(repository)
    run = await service.start(_parameters(), application_commit="abc123", parsers=())

    assert repository.items[run.run_id].status is IngestionRunStatus.RUNNING
    assert repository.items[run.run_id].ended_at is None


async def test_cancellation_during_completion_still_marks_interrupted() -> None:
    class CancelFirstUpdateRepository(FakeIngestionRunRepository):
        def __init__(self) -> None:
            super().__init__()
            self.cancelled = False

        async def update(self, run: IngestionRun) -> IngestionRun:
            if not self.cancelled:
                self.cancelled = True
                raise asyncio.CancelledError
            return await super().update(run)

    repository = CancelFirstUpdateRepository()
    service = _service(repository)

    async def operation(_run_id: str, _outcome_sink: OutcomeSink) -> None:
        return None

    with pytest.raises(asyncio.CancelledError):
        await service.execute(
            _parameters(),
            application_commit="abc123",
            parsers=(),
            operation=operation,
        )

    assert repository.items["run-123"].status is IngestionRunStatus.INTERRUPTED
