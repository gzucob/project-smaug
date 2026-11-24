"""Regression coverage for the directed, weekly CVM disclosure sync."""

import os
from datetime import UTC, date, datetime
from uuid import uuid4

import pytest
from pymongo import AsyncMongoClient

from smaug.ingestion.application.disclosures import (
    SyncFinancialDisclosures,
    sync_identity_archives,
)
from smaug.ingestion.domain.disclosures import (
    CHECK_INTERVAL,
    MAX_PENDING_CHECKS,
    SYNC_RUN_PREFIX,
    ArchiveRevision,
    DisclosureArchive,
    DisclosureEdition,
    DisclosureIssuer,
    DisclosurePeriod,
    expected_periods,
)
from smaug.ingestion.domain.entities import RawIngestion, RawIngestionWrite
from smaug.ingestion.domain.ports import RawFetchResult
from smaug.ingestion.infrastructure.disclosures import MongoDisclosureInventory
from smaug.shared.events import EventBus

ISSUER = DisclosureIssuer("123", "TEST3", "00.000.000/0001-00")
Q1 = DisclosurePeriod(ISSUER.code, "ITR", date(2026, 3, 31))
Q2 = DisclosurePeriod(ISSUER.code, "ITR", date(2026, 6, 30))
PREVIOUS_Q3 = DisclosurePeriod(ISSUER.code, "ITR", date(2025, 9, 30))
DFP = DisclosurePeriod(ISSUER.code, "DFP", date(2025, 12, 31))


class Inventory:
    def __init__(self) -> None:
        self.mirrored = {Q1: 1, DFP: 1, PREVIOUS_Q3: 1}
        self.checks: dict[DisclosurePeriod, int] = {}
        self.missing_versions: dict[tuple[DisclosurePeriod, int], int] = {}
        self.next_check: dict[DisclosureArchive, datetime] = {}
        self.checkpoints: dict[DisclosureArchive, ArchiveRevision | None] = {}

    async def coverage(self, codes: tuple[str, ...]) -> dict[DisclosurePeriod, int]:
        return dict(self.mirrored)

    async def claim(self, archive: DisclosureArchive, now: datetime) -> bool:
        if archive in self.next_check and now < self.next_check[archive]:
            return False
        self.next_check[archive] = now + CHECK_INTERVAL
        return True

    async def revision(
        self, archive: DisclosureArchive, scope: str
    ) -> ArchiveRevision | None:
        return self.checkpoints.get(archive)

    async def complete(
        self, period: DisclosurePeriod, version: int, now: datetime
    ) -> None:
        self.mirrored[period] = version
        self.checks.pop(period, None)

    async def pending(self, period: DisclosurePeriod, now: datetime) -> None:
        self.checks[period] = min(self.checks.get(period, 0) + 1, MAX_PENDING_CHECKS)

    async def unresolved(self, archive: DisclosureArchive) -> set[DisclosurePeriod]:
        return {
            period
            for period, count in self.checks.items()
            if period.kind == archive.kind
            and period.reference.year == archive.year
            and count < MAX_PENDING_CHECKS
        }

    async def settle(
        self, archive: DisclosureArchive, scope: str, revision: ArchiveRevision | None
    ) -> None:
        self.checkpoints[archive] = revision

    async def exhausted_versions(
        self, archive: DisclosureArchive
    ) -> set[tuple[DisclosurePeriod, int]]:
        return {
            (period, version)
            for (period, version), checks in self.missing_versions.items()
            if checks >= MAX_PENDING_CHECKS
            and period.kind == archive.kind
            and period.reference.year == archive.year
        }

    async def catalog_only(
        self, period: DisclosurePeriod, version: int, now: datetime
    ) -> None:
        key = (period, version)
        self.missing_versions[key] = self.missing_versions.get(key, 0) + 1


class Source:
    def __init__(self) -> None:
        self.versions = {Q1: (1,), DFP: (1,)}
        self.revision = ArchiveRevision(etag='"snapshot"')
        self.downloads: list[dict[DisclosurePeriod, tuple[int, ...]]] = []
        self.catalog_only: set[tuple[DisclosurePeriod, int]] = set()

    async def inspect(
        self, archive: DisclosureArchive, revision: ArchiveRevision | None
    ) -> DisclosureEdition:
        versions = {
            period: versions
            for period, versions in self.versions.items()
            if period.kind == archive.kind
            and period.reference.year == archive.year
            and revision != self.revision
        }
        return DisclosureEdition(self.revision, versions)

    async def read(
        self,
        archive: DisclosureArchive,
        edition: DisclosureEdition,
        targets: dict[DisclosurePeriod, tuple[int, ...]],
    ) -> dict[DisclosurePeriod, list[RawFetchResult]]:
        self.downloads.append(targets)
        return {
            period: [
                RawFetchResult(
                    module=module,
                    source="cvm",
                    request={
                        "reference_date": period.reference.isoformat(),
                        "version": version,
                    },
                    http_status=200,
                    payload={},
                    cvm_code=period.code,
                )
                for version in versions
                if (period, version) not in self.catalog_only
                for module in ("DRE", "BPA")
            ]
            for period, versions in targets.items()
        }


class RawMirror:
    def __init__(self) -> None:
        self.rows: dict[tuple[str, str, int], RawIngestion] = {}
        self.fail_on_bpa = False

    async def add(self, ingestion: RawIngestion) -> RawIngestionWrite:
        if self.fail_on_bpa and ingestion.module == "BPA":
            raise RuntimeError("interrupted write")
        key = (
            ingestion.module,
            ingestion.request["reference_date"],
            ingestion.request["version"],
        )
        if key in self.rows:
            return RawIngestionWrite(self.rows[key], created=False)
        self.rows[key] = ingestion
        return RawIngestionWrite(ingestion, created=True)

    async def find_latest(
        self, ticker: str, module: str, *, cvm_code: str | None = None
    ) -> RawIngestion | None:
        raise AssertionError("sync uses a version inventory")


async def test_only_new_versions_are_read_after_the_weekly_window() -> None:
    inventory, source, raw = Inventory(), Source(), RawMirror()
    now = datetime(2026, 8, 15, tzinfo=UTC)
    sync = SyncFinancialDisclosures(
        inventory, source, raw, EventBus(), clock=lambda: now
    )

    first = await sync.execute((ISSUER,), today=now.date(), run_id="first")
    assert first.filings_updated == 0
    assert source.downloads == []
    assert inventory.checks == {Q2: 1}

    source.versions.update({Q1: (1, 2), Q2: (1,)})
    source.revision = ArchiveRevision(etag='"updated"')
    early = await sync.execute((ISSUER,), today=now.date(), run_id="early")
    assert early.archives_checked == 0
    assert inventory.checks == {Q2: 1}

    now += CHECK_INTERVAL
    refreshed = await sync.execute((ISSUER,), today=now.date(), run_id="refreshed")
    assert refreshed.filings_updated == 2
    assert refreshed.rows_stored == 4
    assert source.downloads == [{Q1: (2,), Q2: (1,)}]
    assert inventory.mirrored == {DFP: 1, PREVIOUS_Q3: 1, Q1: 2, Q2: 1}
    assert inventory.checks == {}

    now += CHECK_INTERVAL
    unchanged = await sync.execute((ISSUER,), today=now.date(), run_id="unchanged")
    assert unchanged.rows_stored == 0
    assert len(source.downloads) == 1


async def test_interrupted_period_retries_without_marking_coverage_complete() -> None:
    inventory, source, raw = Inventory(), Source(), RawMirror()
    now = datetime(2026, 8, 15, tzinfo=UTC)
    sync = SyncFinancialDisclosures(
        inventory, source, raw, EventBus(), clock=lambda: now
    )
    source.versions[Q1] = (2,)
    raw.fail_on_bpa = True
    with pytest.raises(RuntimeError, match="interrupted write"):
        await sync.execute((ISSUER,), today=now.date(), run_id="interrupted")
    assert inventory.mirrored[Q1] == 1
    assert len(raw.rows) == 1

    now += CHECK_INTERVAL
    raw.fail_on_bpa = False
    result = await sync.execute((ISSUER,), today=now.date(), run_id="retried")
    assert result.rows_stored == 1
    assert inventory.mirrored[Q1] == 2


def test_non_december_fiscal_year_omits_its_final_quarter() -> None:
    periods = expected_periods(
        DisclosureArchive("ITR", 2026), ISSUER, date(2025, 3, 31)
    )
    assert [period.reference for period in periods] == [
        date(2026, 6, 30),
        date(2026, 9, 30),
        date(2026, 12, 31),
    ]


async def test_catalog_only_version_does_not_block_other_filings() -> None:
    inventory, source, raw = Inventory(), Source(), RawMirror()
    now = datetime(2026, 8, 15, tzinfo=UTC)
    sync = SyncFinancialDisclosures(
        inventory, source, raw, EventBus(), clock=lambda: now
    )
    source.versions.update({Q1: (1, 2), Q2: (1,)})
    source.catalog_only.add((Q1, 2))

    for attempt in range(MAX_PENDING_CHECKS + 1):
        source.revision = ArchiveRevision(etag=f'"week-{attempt}"')
        await sync.execute((ISSUER,), today=now.date(), run_id=str(attempt))
        now += CHECK_INTERVAL

    assert inventory.mirrored[Q1] == 1
    assert inventory.mirrored[Q2] == 1
    assert inventory.missing_versions == {(Q1, 2): MAX_PENDING_CHECKS}
    assert len(source.downloads) == MAX_PENDING_CHECKS

    source.versions[Q1] = (2, 3)
    source.revision = ArchiveRevision(etag='"amended"')
    await sync.execute((ISSUER,), today=now.date(), run_id="amended")
    assert source.downloads[-1] == {Q1: (3,)}
    assert inventory.mirrored[Q1] == 3


async def test_identity_catalog_revision_is_reused_after_the_weekly_lease() -> None:
    inventory = Inventory()
    now = datetime(2026, 8, 15, tzinfo=UTC)
    revision = ArchiveRevision(etag='"identity"')
    revisions: list[ArchiveRevision | None] = []

    class Identities:
        async def refresh_identity(
            self, archive: DisclosureArchive, previous: ArchiveRevision | None
        ) -> ArchiveRevision:
            assert archive.kind == "FCA"
            revisions.append(previous)
            return revision

    source = Identities()
    assert await sync_identity_archives(inventory, source, now) == 0
    checked = len(revisions)
    assert checked
    assert revisions == [None] * checked
    assert await sync_identity_archives(inventory, source, now) == 0
    assert len(revisions) == checked
    assert await sync_identity_archives(inventory, source, now + CHECK_INTERVAL) == 0
    assert revisions[checked:] == [revision] * checked


@pytest.mark.skipif(
    not os.environ.get("SMAUG_TEST_MONGO_URI"),
    reason="Set SMAUG_TEST_MONGO_URI for an isolated disclosure inventory",
)
async def test_mongo_inventory_tracks_coverage_and_attempts() -> None:
    client = AsyncMongoClient(os.environ["SMAUG_TEST_MONGO_URI"])
    database = client[f"disclosure_sync_{uuid4().hex}"]
    inventory = MongoDisclosureInventory(database.raw, database.disclosures)
    archive = DisclosureArchive("ITR", 2026)
    now = datetime(2026, 8, 15, tzinfo=UTC)
    try:
        raw = {
            "source": "cvm",
            "module": "BPA",
            "cvm_code": ISSUER.code,
            "http_status": 200,
        }
        filing = {"document_type": "ITR", "reference_date": Q1.reference.isoformat()}
        await database.raw.insert_many(
            [
                {**raw, "payload": {**filing, "version": 1}},
                {
                    **raw,
                    "payload": {**filing, "version": 2},
                    "run_id": f"{SYNC_RUN_PREFIX}interrupted",
                },
            ]
        )
        assert await inventory.coverage((ISSUER.code,)) == {Q1: 1}
        assert await inventory.claim(archive, now)
        assert not await inventory.claim(archive, now)
        assert await inventory.claim(archive, now + CHECK_INTERVAL)
        revision = ArchiveRevision(etag='"committed"')
        await inventory.settle(archive, "scope", revision)
        assert await inventory.revision(archive, "scope") == revision
        assert await inventory.revision(archive, "changed") is None

        for _ in range(MAX_PENDING_CHECKS + 1):
            await inventory.pending(Q2, now)
        status = await database.disclosures.find_one({"state": "exhausted"})
        assert status is not None
        assert status["checks"] == MAX_PENDING_CHECKS
        assert await inventory.unresolved(archive) == set()

        for _ in range(MAX_PENDING_CHECKS):
            await inventory.catalog_only(Q1, 2, now)
        assert await inventory.exhausted_versions(archive) == {(Q1, 2)}
        await inventory.complete(Q1, 2, now)
        await inventory.complete(Q2, 1, now)
        assert await inventory.exhausted_versions(archive) == set()
        assert await inventory.coverage((ISSUER.code,)) == {Q1: 2, Q2: 1}
    finally:
        await client.drop_database(database.name)
        await client.close()
