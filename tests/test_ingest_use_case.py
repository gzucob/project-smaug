"""Ingestion use case: store, publish, and resilience (plan §5.1)."""

from smaug.ingestion.application.ingest import IngestPortfolioUseCase, OutcomeStatus
from smaug.ingestion.domain.events import RawIngestionStored
from smaug.ingestion.domain.ports import RawFetchResult
from smaug.shared.errors import (
    BrapiAuthError,
    BrapiForbiddenError,
    BrapiNotFoundError,
    CvmDownloadError,
)
from smaug.shared.events import EventBus
from tests.fakes import FakeDataSource, FakeRawIngestionRepository, no_sleep


class _MultiPeriodSource:
    """A source whose single call returns several periods (like a CVM ITR)."""

    def __init__(self, periods: int) -> None:
        self._periods = periods

    async def fetch(self, ticker: str, module: str) -> list[RawFetchResult]:
        return [
            RawFetchResult(
                module=module,
                request={"reference_date": f"2025-{3 * (i + 1):02d}-30"},
                http_status=200,
                payload={"reference_date": f"2025-{3 * (i + 1):02d}-30"},
            )
            for i in range(self._periods)
        ]


async def test_should_store_and_publish_one_document_per_period() -> None:
    repo = FakeRawIngestionRepository()
    bus = EventBus()
    events: list[RawIngestionStored] = []
    bus.subscribe(RawIngestionStored, lambda event: events.append(event))  # type: ignore[arg-type]

    use_case = IngestPortfolioUseCase(
        _MultiPeriodSource(3), repo, bus, ["BPA"], delay_seconds=0, sleep=no_sleep
    )
    outcomes = await use_case.execute(["PETR4"])

    assert [o.status for o in outcomes] == [OutcomeStatus.STORED]
    assert len(repo.items) == 3  # one stored document per filed quarter
    assert len(events) == 3
    assert "3 period" in outcomes[0].detail


async def test_should_store_and_publish_for_each_module() -> None:
    repo = FakeRawIngestionRepository()
    bus = EventBus()
    events: list[RawIngestionStored] = []
    bus.subscribe(RawIngestionStored, lambda event: events.append(event))  # type: ignore[arg-type]

    use_case = IngestPortfolioUseCase(
        FakeDataSource(), repo, bus, ["m1", "m2"], delay_seconds=0, sleep=no_sleep
    )
    outcomes = await use_case.execute(["PETR4"])

    assert [o.status for o in outcomes] == [
        OutcomeStatus.STORED,
        OutcomeStatus.STORED,
    ]
    assert len(repo.items) == 2
    assert len(events) == 2


async def test_should_skip_module_on_404_and_keep_going() -> None:
    source = FakeDataSource(errors={("PETR4", "m1"): BrapiNotFoundError("nope")})
    repo = FakeRawIngestionRepository()

    use_case = IngestPortfolioUseCase(
        source, repo, EventBus(), ["m1", "m2"], delay_seconds=0, sleep=no_sleep
    )
    outcomes = await use_case.execute(["PETR4"])

    assert outcomes[0].status is OutcomeStatus.SKIPPED
    assert outcomes[1].status is OutcomeStatus.STORED
    assert len(repo.items) == 1


async def test_should_skip_module_on_403_plan_restriction_and_keep_going() -> None:
    source = FakeDataSource(errors={("BBAS3", "m1"): BrapiForbiddenError("plan")})
    repo = FakeRawIngestionRepository()

    use_case = IngestPortfolioUseCase(
        source, repo, EventBus(), ["m1", "m2"], delay_seconds=0, sleep=no_sleep
    )
    outcomes = await use_case.execute(["BBAS3"])

    assert outcomes[0].status is OutcomeStatus.SKIPPED
    assert outcomes[0].http_status == 403
    assert outcomes[1].status is OutcomeStatus.STORED
    assert len(repo.items) == 1


async def test_should_abort_run_on_auth_error_before_next_ticker() -> None:
    source = FakeDataSource(errors={("PETR4", "m1"): BrapiAuthError("bad token")})
    repo = FakeRawIngestionRepository()

    use_case = IngestPortfolioUseCase(
        source, repo, EventBus(), ["m1", "m2"], delay_seconds=0, sleep=no_sleep
    )
    outcomes = await use_case.execute(["PETR4", "VALE3"])

    assert outcomes[-1].status is OutcomeStatus.ABORTED
    assert all(o.ticker == "PETR4" for o in outcomes)  # never reached VALE3
    assert repo.items == []


async def test_should_abort_run_when_cvm_zip_download_definitively_fails() -> None:
    # The yearly ZIP is shared by every ticker: once its download is a lost
    # cause, every remaining call would fail identically — stop, don't crash.
    source = FakeDataSource(
        errors={("PETR4", "m1"): CvmDownloadError("giving up after 3 attempts")}
    )
    repo = FakeRawIngestionRepository()

    use_case = IngestPortfolioUseCase(
        source, repo, EventBus(), ["m1", "m2"], delay_seconds=0, sleep=no_sleep
    )
    outcomes = await use_case.execute(["PETR4", "VALE3"])

    assert outcomes[-1].status is OutcomeStatus.ABORTED
    assert all(o.ticker == "PETR4" for o in outcomes)  # never reached VALE3
    assert repo.items == []
