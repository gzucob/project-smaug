"""Ingestion use case: store, publish, and resilience (plan §5.1)."""

from smaug.ingestion.application.ingest import IngestPortfolioUseCase, OutcomeStatus
from smaug.ingestion.domain.events import RawIngestionStored
from smaug.ingestion.domain.ports import RawFetchResult
from smaug.ingestion.domain.runs import ParserIdentity
from smaug.shared.errors import (
    CvmDownloadError,
    SourceAuthError,
    SourceForbiddenError,
    SourceNotFoundError,
)
from smaug.shared.events import EventBus
from tests.fakes import FakeDataSource, FakeRawIngestionRepository, no_sleep


class _MultiPeriodSource:
    """A source whose single call returns several periods (like a CVM ITR)."""

    parser_identity = ParserIdentity("test.multi-period", 1)

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
        _MultiPeriodSource(3),
        repo,
        bus,
        ["BPA"],
        run_id="run-1",
        delay_seconds=0,
        sleep=no_sleep,
    )
    outcomes = await use_case.execute(["PETR4"])

    assert [o.status for o in outcomes] == [OutcomeStatus.STORED]
    assert len(repo.items) == 3  # one stored document per filed quarter
    assert len(events) == 3
    assert {item.run_id for item in repo.items} == {"run-1"}
    assert "3 period" in outcomes[0].detail


async def test_should_store_and_publish_for_each_module() -> None:
    repo = FakeRawIngestionRepository()
    bus = EventBus()
    events: list[RawIngestionStored] = []
    bus.subscribe(RawIngestionStored, lambda event: events.append(event))  # type: ignore[arg-type]

    use_case = IngestPortfolioUseCase(
        FakeDataSource(),
        repo,
        bus,
        ["m1", "m2"],
        run_id="run-1",
        delay_seconds=0,
        sleep=no_sleep,
    )
    outcomes = await use_case.execute(["PETR4"])

    assert [o.status for o in outcomes] == [
        OutcomeStatus.STORED,
        OutcomeStatus.STORED,
    ]
    assert len(repo.items) == 2
    assert len(events) == 2


async def test_should_skip_module_on_404_and_keep_going() -> None:
    source = FakeDataSource(errors={("PETR4", "m1"): SourceNotFoundError("nope")})
    repo = FakeRawIngestionRepository()

    use_case = IngestPortfolioUseCase(
        source,
        repo,
        EventBus(),
        ["m1", "m2"],
        run_id="run-1",
        delay_seconds=0,
        sleep=no_sleep,
    )
    outcomes = await use_case.execute(["PETR4"])

    assert outcomes[0].status is OutcomeStatus.SKIPPED
    assert outcomes[1].status is OutcomeStatus.STORED
    assert len(repo.items) == 1


async def test_should_skip_module_on_403_plan_restriction_and_keep_going() -> None:
    source = FakeDataSource(errors={("BBAS3", "m1"): SourceForbiddenError("plan")})
    repo = FakeRawIngestionRepository()

    use_case = IngestPortfolioUseCase(
        source,
        repo,
        EventBus(),
        ["m1", "m2"],
        run_id="run-1",
        delay_seconds=0,
        sleep=no_sleep,
    )
    outcomes = await use_case.execute(["BBAS3"])

    assert outcomes[0].status is OutcomeStatus.SKIPPED
    assert outcomes[0].http_status == 403
    assert outcomes[1].status is OutcomeStatus.STORED
    assert len(repo.items) == 1


async def test_should_abort_run_on_auth_error_before_next_ticker() -> None:
    source = FakeDataSource(errors={("PETR4", "m1"): SourceAuthError("bad token")})
    repo = FakeRawIngestionRepository()

    use_case = IngestPortfolioUseCase(
        source,
        repo,
        EventBus(),
        ["m1", "m2"],
        run_id="run-1",
        delay_seconds=0,
        sleep=no_sleep,
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
        source,
        repo,
        EventBus(),
        ["m1", "m2"],
        run_id="run-1",
        delay_seconds=0,
        sleep=no_sleep,
    )
    outcomes = await use_case.execute(["PETR4", "VALE3"])

    assert outcomes[-1].status is OutcomeStatus.ABORTED
    assert all(o.ticker == "PETR4" for o in outcomes)  # never reached VALE3
    assert repo.items == []


class _RegistrantSource:
    """A CVM-shaped source: every result names the registrant that filed it."""

    parser_identity = ParserIdentity("test.registrant", 1)

    async def fetch(self, ticker: str, module: str) -> list[RawFetchResult]:
        return [
            RawFetchResult(
                module=module,
                request={"cvm_code": "9512"},
                http_status=200,
                payload={},
                cvm_code="9512",
            )
        ]


async def test_only_paced_modules_sleep_between_calls() -> None:
    # #214: a module reading an already-downloaded CVM archive touches no
    # network per ticker and owes the run no pause at all — only a module named
    # in ``paced_modules`` (a live, per-ticker B3 endpoint) does.
    calls: list[float] = []

    async def _counting_sleep(seconds: float) -> None:
        calls.append(seconds)

    use_case = IngestPortfolioUseCase(
        FakeDataSource(),
        FakeRawIngestionRepository(),
        EventBus(),
        ["m1", "m2"],
        run_id="run-1",
        delay_seconds=2.0,
        paced_modules=frozenset({"m2"}),
        sleep=_counting_sleep,
    )

    await use_case.execute(["PETR4"])

    assert calls == [2.0]  # only m2 slept; m1 is unpaced


async def test_no_module_is_paced_by_default() -> None:
    # The old behaviour paced every module unconditionally; the default is now
    # the opposite — pacing is opt-in, named explicitly by the caller.
    calls: list[float] = []

    async def _counting_sleep(seconds: float) -> None:
        calls.append(seconds)

    use_case = IngestPortfolioUseCase(
        FakeDataSource(),
        FakeRawIngestionRepository(),
        EventBus(),
        ["m1", "m2"],
        run_id="run-1",
        sleep=_counting_sleep,
    )

    await use_case.execute(["PETR4"])

    assert calls == []


async def test_the_registrant_travels_from_the_source_onto_what_is_stored() -> None:
    # The whole read path keys on it (ADR 0030), so a source that names the filer
    # and a store that drops it would leave the mirror unreadable by company.
    repo = FakeRawIngestionRepository()
    use_case = IngestPortfolioUseCase(
        client=_RegistrantSource(),
        repository=repo,
        event_bus=EventBus(),
        modules=["BPA"],
        run_id="run-1",
        source="cvm",
        sleep=no_sleep,
    )

    await use_case.execute(["PETR3"])

    assert [i.cvm_code for i in repo.items] == ["9512"]
    # The ticker still records which code the collection was requested under.
    assert [i.ticker for i in repo.items] == ["PETR3"]
