"""Failed calls stay durable, classed, and selectively resumable."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

from smaug.ingestion.application.failures import (
    IngestionFailureService,
    classify_failure,
)
from smaug.ingestion.application.ingest import (
    FailureContext,
    IngestPortfolioUseCase,
    OutcomeStatus,
    RetryPolicy,
)
from smaug.ingestion.domain.failures import (
    FailureOccurrence,
    IngestionFailureClass,
    IngestionFailureStatus,
)
from smaug.ingestion.domain.ports import RawFetchResult
from smaug.ingestion.domain.runs import ParserIdentity
from smaug.shared.errors import (
    CvmDownloadError,
    SourceAuthError,
    SourceBatchValidationError,
    SourceForbiddenError,
    SourceNotFoundError,
    SourceTimeoutError,
)
from smaug.shared.events import EventBus
from tests.fakes import (
    FakeDataSource,
    FakeIngestionFailureRepository,
    FakeRawIngestionRepository,
)

NOW = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)
PARSER = ParserIdentity("test.source", 1)


def _occurrence(
    failure_class: IngestionFailureClass = IngestionFailureClass.TRANSIENT,
) -> FailureOccurrence:
    return FailureOccurrence(
        ticker="PETR4",
        registrant="9512",
        source="cvm",
        module="DRE",
        year=2024,
        artifact_id="sha256:" + "a" * 64,
        parser=PARSER,
        failure_class=failure_class,
        attempt_count=3,
        first_failed_at=NOW,
        last_failed_at=NOW + timedelta(seconds=3),
        detail="connection closed",
    )


async def test_failure_service_keeps_attempt_history_after_retry_and_resolution() -> (
    None
):
    repository = FakeIngestionFailureRepository()
    service = IngestionFailureService(
        repository,
        clock=lambda: NOW + timedelta(days=1),
        id_factory=lambda: "failure-123",
    )

    recorded = await service.record("run-original", _occurrence())
    retried = await service.record(
        "run-retry",
        replace(_occurrence(), attempt_count=2, detail="timeout again"),
        retry_of=recorded.failure_id,
    )
    resolved = await service.resolve(retried.failure_id, run_id="run-resolved")

    assert resolved.status is IngestionFailureStatus.RESOLVED
    assert resolved.resolution_run_id == "run-resolved"
    assert resolved.attempt_count == 5
    assert [attempt.run_id for attempt in resolved.attempts] == [
        "run-original",
        "run-retry",
    ]
    assert [attempt.detail for attempt in resolved.attempts] == [
        "connection closed",
        "timeout again",
    ]


async def test_only_transient_failures_are_automatically_eligible() -> None:
    repository = FakeIngestionFailureRepository()
    ids = iter(("failure-transient", "failure-permanent"))
    service = IngestionFailureService(repository, id_factory=lambda: next(ids))
    transient = await service.record("run-123", _occurrence())
    permanent = await service.record(
        "run-123",
        replace(_occurrence(IngestionFailureClass.PERMANENT), ticker="NADA3"),
    )

    eligible = await service.eligible_for_run(
        "run-123", current_parsers={"DRE": PARSER}, current_sources={"DRE": "cvm"}
    )
    explicit = await service.eligible_for_run(
        "run-123",
        current_parsers={"DRE": PARSER},
        current_sources={"DRE": "cvm"},
        retry_permanent=True,
    )
    changed_parser = await service.eligible_for_run(
        "run-123",
        current_parsers={"DRE": ParserIdentity("test.source", 2)},
        current_sources={"DRE": "cvm"},
    )
    changed_source = await service.eligible_for_run(
        "run-123", current_parsers={"DRE": PARSER}, current_sources={"DRE": "b3"}
    )

    assert [failure.failure_id for failure in eligible] == [transient.failure_id]
    assert {failure.failure_id for failure in explicit} == {
        transient.failure_id,
        permanent.failure_id,
    }
    assert {failure.failure_id for failure in changed_parser} == {
        transient.failure_id,
        permanent.failure_id,
    }
    assert {failure.failure_id for failure in changed_source} == {
        transient.failure_id,
        permanent.failure_id,
    }


def test_source_error_classes_are_stable_retry_categories() -> None:
    assert (
        classify_failure(SourceTimeoutError("timeout"))
        is IngestionFailureClass.TRANSIENT
    )
    assert (
        classify_failure(SourceNotFoundError("missing"))
        is IngestionFailureClass.PERMANENT
    )
    assert (
        classify_failure(SourceForbiddenError("restricted"))
        is IngestionFailureClass.PERMANENT
    )
    assert (
        classify_failure(SourceBatchValidationError("bad schema"))
        is IngestionFailureClass.VALIDATION
    )
    assert (
        classify_failure(SourceAuthError("denied"))
        is IngestionFailureClass.FATAL_SHARED_SOURCE
    )
    assert (
        classify_failure(CvmDownloadError("archive unavailable"))
        is IngestionFailureClass.FATAL_SHARED_SOURCE
    )


class _FlakySource:
    """Fails enough times to exercise the generic per-call retry boundary."""

    parser_identity = PARSER

    def __init__(self) -> None:
        self.calls = 0

    async def fetch(self, ticker: str, module: str) -> list[RawFetchResult]:
        self.calls += 1
        raise SourceTimeoutError(f"attempt {self.calls}")


async def test_transient_call_uses_bounded_backoff_and_records_attempt_count() -> None:
    source = _FlakySource()
    failures: list[FailureOccurrence] = []
    delays: list[float] = []

    async def failure_sink(failure: FailureOccurrence) -> None:
        failures.append(failure)

    async def sleep(seconds: float) -> None:
        delays.append(seconds)

    use_case = IngestPortfolioUseCase(
        source,
        FakeRawIngestionRepository(),
        EventBus(),
        ["DRE"],
        run_id="run-123",
        failure_sink=failure_sink,
        failure_context=FailureContext(
            year=2024,
            registrants={"PETR4": "9512"},
            sources={"DRE": "cvm"},
            parsers={"DRE": PARSER},
        ),
        retry_policy=RetryPolicy(
            max_attempts=3,
            initial_delay_seconds=1,
            maximum_delay_seconds=10,
            jitter_ratio=0,
        ),
        sleep=sleep,
    )

    outcomes = await use_case.execute(["PETR4"])

    assert [outcome.status for outcome in outcomes] == [OutcomeStatus.ERROR]
    assert source.calls == 3
    assert delays == [1, 2]
    assert len(failures) == 1
    assert failures[0].attempt_count == 3
    assert failures[0].failure_class is IngestionFailureClass.TRANSIENT
    assert failures[0].registrant == "9512"


async def test_permanent_absence_is_recorded_without_generic_retry() -> None:
    failures: list[FailureOccurrence] = []
    source = FakeDataSource(errors={("PETR4", "DRE"): SourceNotFoundError("not filed")})

    async def failure_sink(failure: FailureOccurrence) -> None:
        failures.append(failure)

    use_case = IngestPortfolioUseCase(
        source,
        FakeRawIngestionRepository(),
        EventBus(),
        ["DRE"],
        run_id="run-123",
        failure_sink=failure_sink,
        failure_context=FailureContext(
            year=2024,
            registrants={"PETR4": "9512"},
            sources={"DRE": "cvm"},
            parsers={"DRE": PARSER},
        ),
    )

    outcomes = await use_case.execute(["PETR4"])

    assert [outcome.status for outcome in outcomes] == [OutcomeStatus.SKIPPED]
    assert source.calls == [("PETR4", "DRE")]
    assert failures[0].failure_class is IngestionFailureClass.PERMANENT
    assert failures[0].attempt_count == 1
