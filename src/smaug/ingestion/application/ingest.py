"""Ingestion use case: fetch -> save -> publish, one call at a time.

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
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from smaug.ingestion.domain.entities import RawIngestion
from smaug.ingestion.domain.events import RawIngestionStored
from smaug.ingestion.domain.ports import RawDataSource
from smaug.ingestion.domain.repositories import RawIngestionRepository
from smaug.shared.errors import (
    CvmDownloadError,
    SourceAuthError,
    SourceError,
    SourceForbiddenError,
    SourceNotFoundError,
    SourceRateLimitError,
)
from smaug.shared.events import EventBus
from smaug.shared.logging import get_logger

logger = get_logger(__name__)

Clock = Callable[[], datetime]
Sleeper = Callable[[float], Awaitable[None]]


def _utc_now() -> datetime:
    return datetime.now(UTC)


class OutcomeStatus(StrEnum):
    """Result of a single ticker/module fetch attempt."""

    STORED = "stored"
    SKIPPED = "skipped"
    ERROR = "error"
    ABORTED = "aborted"


@dataclass(frozen=True)
class FetchOutcome:
    """One line of the collection log (plan §5.1)."""

    ticker: str
    module: str
    status: OutcomeStatus
    http_status: int | None
    detail: str


OutcomeSink = Callable[[FetchOutcome], Awaitable[None]]


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
        clock: Clock = _utc_now,
        sleep: Sleeper = asyncio.sleep,
        outcome_sink: OutcomeSink | None = None,
    ) -> None:
        self._client = client
        self._repository = repository
        self._event_bus = event_bus
        self._modules = tuple(modules)
        self._run_id = run_id
        self._source = source
        self._delay_seconds = delay_seconds
        self._paced_modules = paced_modules
        self._clock = clock
        self._sleep = sleep
        self._outcome_sink = outcome_sink

    async def execute(self, tickers: Iterable[str]) -> list[FetchOutcome]:
        """Run the collection, returning one outcome per attempted call."""
        outcomes: list[FetchOutcome] = []
        for ticker in tickers:
            aborted = await self._collect_ticker(ticker, outcomes)
            if aborted:
                logger.warning("Aborting run after fatal error on %s", ticker)
                break
        return outcomes

    async def _collect_ticker(self, ticker: str, outcomes: list[FetchOutcome]) -> bool:
        """Collect every module for one ticker. Returns True if the run must stop."""
        for module in self._modules:
            try:
                outcome = await self._fetch_and_store(ticker, module)
            except (SourceAuthError, SourceRateLimitError, CvmDownloadError) as exc:
                # Fatal for the whole run: no point hammering the rest. The CVM
                # ZIP is shared by every ticker of the year, so its definitive
                # download failure dooms all remaining calls identically.
                await self._record(
                    outcomes,
                    FetchOutcome(ticker, module, OutcomeStatus.ABORTED, None, str(exc)),
                )
                return True
            except (SourceNotFoundError, SourceForbiddenError) as exc:
                # Skip just this call; keep collecting the others.
                # 404 = ticker/module unknown; 403 = ticker needs a higher plan.
                code = 403 if isinstance(exc, SourceForbiddenError) else 404
                logger.info("Skipping %s/%s: %s", ticker, module, exc)
                await self._record(
                    outcomes,
                    FetchOutcome(ticker, module, OutcomeStatus.SKIPPED, code, str(exc)),
                )
            except SourceError as exc:
                # Unexpected, but isolated: record and move on.
                logger.warning("Error on %s/%s: %s", ticker, module, exc)
                await self._record(
                    outcomes,
                    FetchOutcome(ticker, module, OutcomeStatus.ERROR, None, str(exc)),
                )
            else:
                await self._record(outcomes, outcome)
            if module in self._paced_modules:
                await self._sleep(self._delay_seconds)
        return False

    async def _fetch_and_store(self, ticker: str, module: str) -> FetchOutcome:
        # One source call may return several periods (CVM ITR = Q1/Q2/Q3); each
        # is a distinct filing and gets its own stored document and event.
        responses = await self._client.fetch(ticker, module)
        last_status: int | None = None
        for response in responses:
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
            stored = await self._repository.add(ingestion)
            self._event_bus.publish(
                RawIngestionStored(
                    ticker=stored.ticker,
                    module=stored.module,
                    fetched_at=stored.fetched_at,
                    http_status=stored.http_status,
                )
            )
            last_status = response.http_status
        count = len(responses)
        logger.info("Stored %s/%s: %d period(s)", ticker, module, count)
        return FetchOutcome(
            ticker, module, OutcomeStatus.STORED, last_status, f"{count} period(s)"
        )

    async def _record(
        self, outcomes: list[FetchOutcome], outcome: FetchOutcome
    ) -> None:
        outcomes.append(outcome)
        if self._outcome_sink is not None:
            await self._outcome_sink(outcome)
