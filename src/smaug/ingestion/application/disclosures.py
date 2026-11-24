"""Synchronize CVM presentations independently of market prices and analysis."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from hashlib import sha256

from smaug.ingestion.domain.disclosures import (
    PUBLISHED_YEARS,
    DisclosureArchive,
    DisclosureInventory,
    DisclosureIssuer,
    DisclosureKind,
    DisclosurePeriod,
    DisclosureSource,
    IdentityArchiveSource,
    expected_by,
    expected_periods,
    response_version,
    shift_months,
)
from smaug.ingestion.domain.entities import RawIngestion
from smaug.ingestion.domain.events import RawIngestionStored
from smaug.ingestion.domain.repositories import RawIngestionRepository
from smaug.shared.errors import SourceError
from smaug.shared.events import EventBus
from smaug.shared.logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class DisclosureSyncResult:
    archives_checked: int
    filings_updated: int
    rows_stored: int
    failures: int


async def sync_identity_archives(
    inventory: DisclosureInventory,
    source: IdentityArchiveSource,
    now: datetime,
) -> int:
    """Admit only changed FCA catalogs within CVM's publication window."""
    failures = 0
    for year in range(now.year - PUBLISHED_YEARS + 1, now.year + 1):
        archive = DisclosureArchive("FCA", year)
        if not await inventory.claim(archive, now):
            continue
        try:
            previous = await inventory.revision(archive, "identity")
            revision = await source.refresh_identity(archive, previous)
            await inventory.settle(archive, "identity", revision)
        except SourceError as error:
            failures += 1
            logger.warning("CVM %s: %s", archive.name, error)
    return failures


class SyncFinancialDisclosures:
    """Compare published editions against MongoDB and append new filing versions."""

    def __init__(
        self,
        inventory: DisclosureInventory,
        source: DisclosureSource,
        raw: RawIngestionRepository,
        events: EventBus,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._inventory = inventory
        self._source = source
        self._raw = raw
        self._events = events
        self._clock = clock

    async def execute(
        self, issuers: tuple[DisclosureIssuer, ...], *, today: date, run_id: str
    ) -> DisclosureSyncResult:
        by_code = {issuer.code: issuer for issuer in issuers}
        if not by_code:
            return DisclosureSyncResult(0, 0, 0, 0)
        now = self._clock()
        kinds: tuple[DisclosureKind, ...] = ("DFP", "ITR", "FRE")
        archives = [
            DisclosureArchive(kind, year)
            for kind in kinds
            for year in range(today.year - PUBLISHED_YEARS + 1, today.year + 1)
        ]
        coverage: dict[DisclosurePeriod, int] | None = None
        fiscal_closes: dict[str, date] = {}
        latest: dict[tuple[str, str], date] = {}
        scope = sha256(",".join(sorted(by_code)).encode()).hexdigest()
        recent = shift_months(today, -12)
        checked = updated = stored = failures = 0
        for archive in archives:
            if not await self._inventory.claim(archive, now):
                continue
            checked += 1
            if coverage is None:
                coverage = await self._inventory.coverage(tuple(by_code))
                for period in coverage:
                    key = (period.code, period.kind)
                    latest[key] = max(period.reference, latest.get(key, date.min))
                fiscal_closes = {
                    code: reference
                    for (code, kind), reference in latest.items()
                    if kind == "DFP"
                }
            try:
                revision = await self._inventory.revision(archive, scope)
                edition = await self._source.inspect(archive, revision)
                exhausted = (
                    await self._inventory.exhausted_versions(archive)
                    if edition.versions
                    else set()
                )
                targets = {
                    period: unseen
                    for period, versions in edition.versions.items()
                    if period.code in by_code
                    and (
                        unseen := tuple(
                            v
                            for v in versions
                            if v > coverage.get(period, 0)
                            and (period, v) not in exhausted
                        )
                    )
                }
                rows = (
                    await self._source.read(archive, edition, targets)
                    if targets
                    else {}
                )
                for period, versions in targets.items():
                    materialized = {
                        response_version(response) for response in rows[period]
                    }
                    for response in rows[period]:
                        written = await self._raw.add(
                            RawIngestion(
                                ticker=by_code[period.code].ticker,
                                source=response.source,
                                module=response.module,
                                fetched_at=now,
                                request=response.request,
                                http_status=response.http_status,
                                payload=response.payload,
                                run_id=run_id,
                                artifact_id=response.artifact_id,
                                cvm_code=period.code,
                            )
                        )
                        if written.created:
                            stored += 1
                            fact = written.ingestion
                            self._events.publish(
                                RawIngestionStored(
                                    fact.ticker,
                                    fact.module,
                                    fact.fetched_at,
                                    fact.http_status,
                                )
                            )
                    if materialized:
                        version = max(materialized)
                        await self._inventory.complete(period, version, now)
                        coverage[period] = version
                        if period.kind == "DFP":
                            fiscal_closes[period.code] = max(
                                period.reference,
                                fiscal_closes.get(period.code, date.min),
                            )
                        updated += 1
                    for version in versions:
                        if version > coverage.get(period, 0):
                            await self._inventory.catalog_only(period, version, now)
                pending = await self._inventory.unresolved(archive)
                for issuer in by_code.values():
                    close = fiscal_closes.get(issuer.code, date(today.year, 12, 31))
                    for period in expected_periods(archive, issuer, close):
                        # Seed recent gaps even when a later period is covered.
                        if (
                            period not in coverage
                            and expected_by(period) <= today
                            and (
                                period.reference >= recent
                                or (
                                    (issuer.code, archive.kind) in latest
                                    and period.reference
                                    > latest[issuer.code, archive.kind]
                                )
                            )
                        ):
                            pending.add(period)
                for period in pending:
                    if period.code not in by_code:
                        continue
                    if period in coverage:
                        await self._inventory.complete(period, coverage[period], now)
                    else:
                        await self._inventory.pending(period, now)
                await self._inventory.settle(archive, scope, edition.revision)
            except SourceError as error:
                failures += 1
                logger.warning("CVM %s: %s", archive.name, error)
        return DisclosureSyncResult(checked, updated, stored, failures)
