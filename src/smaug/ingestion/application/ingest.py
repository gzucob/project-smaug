"""Ingestion use case: fetch -> save -> publish with bounded archive concurrency.

Orchestration only. It owns no HTTP and no Mongo details — it talks to the
source and to the repository *interface*, and publishes a domain event
on the shared bus. Resilience follows plan §5.1: 401 stops the run, plan/rate
limits stop the run, 404 (unknown) and 403 (plan-restricted) skip the call, and
any single failure never takes the other tickers down with it. A definitive CVM
ZIP download failure also stops the run — the file is shared by the whole year,
so every remaining call would fail identically.

``paced_modules`` names which modules actually deserve the post-call sleep
(#214). It used to apply unconditionally — a flat per-call throttle inherited
from brapi (removed, ADR 0041), where every module fetch really was one
rate-limited HTTP request per ticker. Since the move to CVM's yearly ZIPs and
B3's own series, most modules read an already-downloaded, in-memory-indexed
archive and touch no network per ticker at all; only a module the composition
root names here waits between calls. The default is empty — pacing is opt-in,
not a tax the use case levies on every source by default.
"""

from __future__ import annotations

import asyncio
import json
import random
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from time import perf_counter

from smaug.ingestion.application.failures import (
    classify_failure,
    sanitize_failure_detail,
)
from smaug.ingestion.domain.entities import RawIngestion
from smaug.ingestion.domain.events import RawIngestionStored
from smaug.ingestion.domain.failures import (
    FailureOccurrence,
    IngestionFailureClass,
)
from smaug.ingestion.domain.ports import CacheAwareRawDataSource, RawDataSource
from smaug.ingestion.domain.repositories import RawIngestionRepository
from smaug.ingestion.domain.runs import ParserIdentity
from smaug.shared.errors import (
    SourceError,
    SourceForbiddenError,
)
from smaug.shared.events import EventBus
from smaug.shared.logging import get_logger

logger = get_logger(__name__)

Clock = Callable[[], datetime]
Sleeper = Callable[[float], Awaitable[None]]
RandomSource = Callable[[], float]
ArtifactIdResolver = Callable[[str], Awaitable[str | None]]


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _random_value() -> float:
    return float(random.random())


class OutcomeStatus(StrEnum):
    """Result of a single ticker/module fetch attempt."""

    STORED = "stored"
    UNCHANGED = "unchanged"
    SKIPPED = "skipped"
    ERROR = "error"
    QUARANTINED = "quarantined"
    ABORTED = "aborted"


@dataclass(frozen=True)
class FetchOutcome:
    """One line of the collection log (plan §5.1)."""

    ticker: str
    module: str
    status: OutcomeStatus
    http_status: int | None
    detail: str
    source_seconds: float = 0.0
    parse_seconds: float = 0.0
    store_seconds: float = 0.0
    retry_wait_seconds: float = 0.0
    payload_bytes: int = 0
    rows: int = 0
    cache_hits: int = 0
    cache_misses: int = 0
    artifact_id: str | None = None


OutcomeSink = Callable[[FetchOutcome], Awaitable[None]]
FailureSink = Callable[[FailureOccurrence], Awaitable[None]]
ResolutionSink = Callable[[str, str], Awaitable[None]]


@dataclass(frozen=True)
class RetryPolicy:
    """Bounded scheduling policy for isolated transient source calls."""

    max_attempts: int = 3
    initial_delay_seconds: float = 1.0
    maximum_delay_seconds: float = 10.0
    jitter_ratio: float = 0.2

    def delay_for(self, retry_number: int, random_value: float) -> float:
        """Return exponential delay with symmetric bounded jitter."""
        base = min(
            self.initial_delay_seconds * (2 ** (retry_number - 1)),
            self.maximum_delay_seconds,
        )
        jitter = 1 + self.jitter_ratio * ((2 * random_value) - 1)
        return float(base * jitter)


@dataclass(frozen=True)
class FailureContext:
    """Source facts needed to persist a failed call outside source adapters."""

    year: int
    registrants: dict[str, str]
    sources: dict[str, str]
    parsers: dict[str, ParserIdentity]
    artifact_id_for: ArtifactIdResolver | None = None


@dataclass(frozen=True)
class _FailedFetch:
    """An exhausted source call, preserving generic retry timing facts."""

    error: SourceError
    attempt_count: int
    first_failed_at: datetime
    last_failed_at: datetime
    source_seconds: float
    retry_wait_seconds: float
    cache_hits: int
    cache_misses: int


class IngestPortfolioUseCase:
    """Collect the configured modules for a set of tickers."""

    def __init__(
        self,
        client: RawDataSource,
        repository: RawIngestionRepository,
        event_bus: EventBus,
        modules: Sequence[str],
        *,
        run_id: str,
        source: str = "cvm",
        delay_seconds: float = 2.0,
        paced_modules: frozenset[str] = frozenset(),
        max_concurrency: int = 1,
        clock: Clock = _utc_now,
        sleep: Sleeper = asyncio.sleep,
        outcome_sink: OutcomeSink | None = None,
        failure_sink: FailureSink | None = None,
        resolution_sink: ResolutionSink | None = None,
        failure_context: FailureContext | None = None,
        retry_policy: RetryPolicy | None = None,
        random_source: RandomSource = _random_value,
    ) -> None:
        self._client = client
        self._repository = repository
        self._event_bus = event_bus
        self._modules = tuple(modules)
        self._run_id = run_id
        self._source = source
        self._delay_seconds = delay_seconds
        self._paced_modules = paced_modules
        if max_concurrency < 1:
            raise ValueError("max_concurrency must be at least one")
        self._max_concurrency = max_concurrency
        self._clock = clock
        self._sleep = sleep
        self._outcome_sink = outcome_sink
        self._failure_sink = failure_sink
        self._resolution_sink = resolution_sink
        self._failure_context = failure_context
        self._retry_policy = retry_policy or RetryPolicy()
        self._random_source = random_source
        self._cache_keys: set[str] = set()
        self._cache_lock = asyncio.Lock()
        self._paced_semaphore = asyncio.Semaphore(1)

    async def execute(self, tickers: Iterable[str]) -> list[FetchOutcome]:
        """Run the collection, returning one outcome per attempted call."""
        selected = tuple(tickers)
        if not selected:
            return []
        outcomes = await self._execute_bounded(selected, self._max_concurrency)
        for outcome in outcomes:
            await self._record(outcome)
        return outcomes

    async def _execute_bounded(
        self, tickers: tuple[str, ...], concurrency: int
    ) -> list[FetchOutcome]:
        """Collect through a fixed worker pool, retaining ticker input order."""
        queue: asyncio.Queue[tuple[int, str] | None] = asyncio.Queue()
        for position, ticker in enumerate(tickers):
            await queue.put((position, ticker))
        workers = min(concurrency, len(tickers))
        for _ in range(workers):
            await queue.put(None)

        semaphore = asyncio.Semaphore(concurrency)
        aborted = asyncio.Event()
        collected: list[tuple[FetchOutcome, ...] | None] = [None] * len(tickers)

        async def worker() -> None:
            while (item := await queue.get()) is not None:
                position, ticker = item
                try:
                    if aborted.is_set():
                        continue
                    async with semaphore:
                        ticker_outcomes, must_abort = await self._collect_ticker(ticker)
                    collected[position] = ticker_outcomes
                    if must_abort:
                        logger.warning("Aborting run after fatal error on %s", ticker)
                        aborted.set()
                finally:
                    queue.task_done()

        async with asyncio.TaskGroup() as group:
            for _ in range(workers):
                group.create_task(worker())

        return [
            outcome for batch in collected if batch is not None for outcome in batch
        ]

    async def _collect_ticker(
        self, ticker: str
    ) -> tuple[tuple[FetchOutcome, ...], bool]:
        """Collect every module for one ticker. Returns True if the run must stop."""
        outcomes: list[FetchOutcome] = []
        for module in self._modules:
            if module in self._paced_modules:
                # B3's public endpoints are a shared rate-limited resource. The
                # semaphore covers both the call and its cooldown, so archive
                # workers never turn per-ticker parallelism into a request burst.
                async with self._paced_semaphore:
                    outcome, failed = await self._fetch_with_retry(ticker, module)
                    await self._sleep(self._delay_seconds)
            else:
                outcome, failed = await self._fetch_with_retry(ticker, module)
            if failed is not None:
                error = failed.error
                failure_class = classify_failure(error)
                await self._record_failure(ticker, module, failed, failure_class)
                if failure_class is IngestionFailureClass.FATAL_SHARED_SOURCE:
                    # The CVM ZIP is shared by every ticker of the year, so its
                    # definitive source-level retry remains the authority.
                    outcomes.append(
                        FetchOutcome(
                            ticker,
                            module,
                            OutcomeStatus.ABORTED,
                            None,
                            str(error),
                            source_seconds=failed.source_seconds,
                            retry_wait_seconds=failed.retry_wait_seconds,
                            cache_hits=failed.cache_hits,
                            cache_misses=failed.cache_misses,
                        )
                    )
                    return tuple(outcomes), True
                if failure_class is IngestionFailureClass.PERMANENT:
                    # 404 = ticker/module unknown; 403 = source restriction.
                    code = 403 if isinstance(error, SourceForbiddenError) else 404
                    logger.info("Skipping %s/%s: %s", ticker, module, error)
                    outcomes.append(
                        FetchOutcome(
                            ticker,
                            module,
                            OutcomeStatus.SKIPPED,
                            code,
                            str(error),
                            source_seconds=failed.source_seconds,
                            retry_wait_seconds=failed.retry_wait_seconds,
                            cache_hits=failed.cache_hits,
                            cache_misses=failed.cache_misses,
                        )
                    )
                elif failure_class is IngestionFailureClass.VALIDATION:
                    logger.error("Quarantined %s/%s: %s", ticker, module, error)
                    outcomes.append(
                        FetchOutcome(
                            ticker,
                            module,
                            OutcomeStatus.QUARANTINED,
                            None,
                            str(error),
                            source_seconds=failed.source_seconds,
                            retry_wait_seconds=failed.retry_wait_seconds,
                            cache_hits=failed.cache_hits,
                            cache_misses=failed.cache_misses,
                        )
                    )
                else:
                    logger.warning("Error on %s/%s: %s", ticker, module, error)
                    outcomes.append(
                        FetchOutcome(
                            ticker,
                            module,
                            OutcomeStatus.ERROR,
                            None,
                            str(error),
                            source_seconds=failed.source_seconds,
                            retry_wait_seconds=failed.retry_wait_seconds,
                            cache_hits=failed.cache_hits,
                            cache_misses=failed.cache_misses,
                        )
                    )
            else:
                if outcome is None:
                    raise AssertionError("successful fetch has no outcome")
                outcomes.append(outcome)
                if self._resolution_sink is not None:
                    await self._resolution_sink(ticker, module)
        return tuple(outcomes), False

    async def _fetch_with_retry(
        self, ticker: str, module: str
    ) -> tuple[FetchOutcome | None, _FailedFetch | None]:
        """Retry only isolated transient calls; shared ZIP retries stay in source."""
        first_failed_at: datetime | None = None
        source_seconds = 0.0
        retry_wait_seconds = 0.0
        cache_hits = 0
        cache_misses = 0
        for attempt in range(1, self._retry_policy.max_attempts + 1):
            hit, miss = await self._cache_observation(module)
            cache_hits += hit
            cache_misses += miss
            started = perf_counter()
            try:
                outcome = await self._fetch_and_store(ticker, module)
            except SourceError as error:
                source_seconds += perf_counter() - started
                now = self._clock()
                if first_failed_at is None:
                    first_failed_at = now
                failure_class = classify_failure(error)
                if (
                    failure_class is IngestionFailureClass.TRANSIENT
                    and attempt < self._retry_policy.max_attempts
                ):
                    delay = self._retry_policy.delay_for(attempt, self._random_source())
                    retry_wait_seconds += delay
                    self._log_metric(
                        "ingestion.retry_wait",
                        ticker=ticker,
                        module=module,
                        retry_wait_seconds=delay,
                    )
                    await self._sleep(delay)
                    continue
                return None, _FailedFetch(
                    error,
                    attempt,
                    first_failed_at,
                    now,
                    source_seconds,
                    retry_wait_seconds,
                    cache_hits,
                    cache_misses,
                )
            else:
                total_source_seconds = source_seconds + outcome.source_seconds
                return (
                    replace(
                        outcome,
                        source_seconds=total_source_seconds,
                        parse_seconds=total_source_seconds if cache_misses else 0.0,
                        retry_wait_seconds=retry_wait_seconds,
                        cache_hits=cache_hits,
                        cache_misses=cache_misses,
                    ),
                    None,
                )
        raise AssertionError("bounded retry loop exhausted without a result")

    async def _record_failure(
        self,
        ticker: str,
        module: str,
        failed: _FailedFetch,
        failure_class: IngestionFailureClass,
    ) -> None:
        if self._failure_sink is None or self._failure_context is None:
            return
        artifact_id = getattr(failed.error, "quarantined_artifact_id", None)
        if (
            artifact_id is None
            and failure_class is not IngestionFailureClass.FATAL_SHARED_SOURCE
            and self._failure_context.artifact_id_for is not None
        ):
            try:
                artifact_id = await self._failure_context.artifact_id_for(module)
            except SourceError:
                # The source failure is the fact to persist. A best-effort lookup
                # must not create a second attempt merely to name absent bytes.
                pass
        parser = self._failure_context.parsers.get(module, self._client.parser_identity)
        await self._failure_sink(
            FailureOccurrence(
                ticker=ticker,
                registrant=self._failure_context.registrants.get(ticker),
                source=self._failure_context.sources.get(module, self._source),
                module=module,
                year=self._failure_context.year,
                artifact_id=artifact_id,
                parser=parser,
                failure_class=failure_class,
                attempt_count=failed.attempt_count,
                first_failed_at=failed.first_failed_at,
                last_failed_at=failed.last_failed_at,
                detail=sanitize_failure_detail(failed.error),
            )
        )

    async def _fetch_and_store(self, ticker: str, module: str) -> FetchOutcome:
        # One source call may return several periods (CVM ITR = Q1/Q2/Q3); each
        # is a distinct filing and gets its own stored document and event.
        source_started = perf_counter()
        responses = await self._client.fetch(ticker, module)
        source_seconds = perf_counter() - source_started
        last_status: int | None = None
        stored_count = 0
        unchanged_count = 0
        store_seconds = 0.0
        payload_bytes = 0
        for response in responses:
            payload_bytes += _serialized_size(response.payload)
            ingestion = RawIngestion(
                ticker=ticker,
                source=self._source,
                module=module,
                fetched_at=self._clock(),
                request=response.request,
                http_status=response.http_status,
                payload=response.payload,
                run_id=self._run_id,
                artifact_id=response.artifact_id,
                cvm_code=response.cvm_code,
            )
            store_started = perf_counter()
            written = await self._repository.add(ingestion)
            store_seconds += perf_counter() - store_started
            if written.created:
                stored = written.ingestion
                self._event_bus.publish(
                    RawIngestionStored(
                        ticker=stored.ticker,
                        module=stored.module,
                        fetched_at=stored.fetched_at,
                        http_status=stored.http_status,
                    )
                )
                stored_count += 1
            else:
                unchanged_count += 1
            last_status = response.http_status
        count = len(responses)
        logger.info(
            "Collected %s/%s: %d stored, %d unchanged",
            ticker,
            module,
            stored_count,
            unchanged_count,
        )
        status = OutcomeStatus.STORED if stored_count else OutcomeStatus.UNCHANGED
        return FetchOutcome(
            ticker,
            module,
            status,
            last_status,
            f"{stored_count} stored, {unchanged_count} unchanged ({count} period(s))",
            source_seconds=source_seconds,
            store_seconds=store_seconds,
            payload_bytes=payload_bytes,
            rows=count,
            artifact_id=next(
                (
                    response.artifact_id
                    for response in responses
                    if response.artifact_id is not None
                ),
                None,
            ),
        )

    async def _cache_observation(self, module: str) -> tuple[int, int]:
        if not isinstance(self._client, CacheAwareRawDataSource):
            return 0, 0
        key = self._client.cache_key(module)
        if key is None:
            return 0, 0
        async with self._cache_lock:
            if key in self._cache_keys:
                return 1, 0
            self._cache_keys.add(key)
            return 0, 1

    async def _record(self, outcome: FetchOutcome) -> None:
        self._log_metric(
            "ingestion.call",
            ticker=outcome.ticker,
            module=outcome.module,
            status=outcome.status.value,
            http_status=outcome.http_status,
            artifact_id=outcome.artifact_id,
            source_seconds=outcome.source_seconds,
            parse_seconds=outcome.parse_seconds,
            store_seconds=outcome.store_seconds,
            retry_wait_seconds=outcome.retry_wait_seconds,
            payload_bytes=outcome.payload_bytes,
            rows=outcome.rows,
            cache_hits=outcome.cache_hits,
            cache_misses=outcome.cache_misses,
        )
        if self._outcome_sink is not None:
            await self._outcome_sink(outcome)

    def _log_metric(self, event: str, **fields: object) -> None:
        context: dict[str, object] = {
            "event": event,
            "run_id": self._run_id,
            "year": self._failure_context.year if self._failure_context else None,
            **fields,
        }
        logger.info("%s", json.dumps(context, sort_keys=True, separators=(",", ":")))


def _serialized_size(payload: object) -> int:
    """Measure the exact UTF-8 representation kept by the raw mirror."""
    return len(
        json.dumps(payload, default=str, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    )
