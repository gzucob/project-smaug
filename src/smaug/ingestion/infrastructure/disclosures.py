"""MongoDB inventory of expected disclosures and fully processed archive revisions."""

from datetime import date, datetime
from typing import Any, cast

from pymongo.asynchronous.collection import AsyncCollection
from pymongo.errors import DuplicateKeyError

from smaug.ingestion.domain.disclosures import (
    CHECK_INTERVAL,
    MAX_PENDING_CHECKS,
    SYNC_RUN_PREFIX,
    ArchiveRevision,
    DisclosureArchive,
    DisclosureKind,
    DisclosurePeriod,
    expected_by,
)


class MongoDisclosureInventory:
    """A weekly claim gates each archive across processes and persists progress."""

    def __init__(
        self,
        raw: AsyncCollection[dict[str, Any]],
        inventory: AsyncCollection[dict[str, Any]],
    ) -> None:
        self._raw = raw
        self._inventory = inventory

    async def coverage(self, codes: tuple[str, ...]) -> dict[DisclosurePeriod, int]:
        # Partial writes from this command are retried until completion has been
        # recorded. Existing explicit imports and prepared snapshots are the baseline.
        cursor = await self._raw.aggregate(
            [
                {
                    "$match": {
                        "source": "cvm",
                        "cvm_code": {"$in": list(codes)},
                        "http_status": 200,
                        "$or": [
                            {
                                "module": "BPA",
                                "payload.document_type": {"$in": ["ITR", "DFP"]},
                            },
                            {
                                "module": "CAPITAL",
                                "request.file": {"$regex": r"^fre_cia_aberta_"},
                            },
                        ],
                        "payload.version": {"$gt": 0},
                        "run_id": {"$not": {"$regex": f"^{SYNC_RUN_PREFIX}"}},
                    }
                },
                {
                    "$group": {
                        "_id": {
                            "code": "$cvm_code",
                            "kind": {"$ifNull": ["$payload.document_type", "FRE"]},
                            "reference": "$payload.reference_date",
                        },
                        "version": {"$max": "$payload.version"},
                    }
                },
            ]
        )
        versions = {
            _period(row["_id"]): int(row["version"])
            async for row in cursor
            if row["_id"].get("reference")
        }
        async for row in self._inventory.find(
            {"code": {"$in": list(codes)}, "state": "complete"},
            {"code": 1, "kind": 1, "reference": 1, "version": 1},
        ):
            period = _period(row)
            versions[period] = max(versions.get(period, 0), int(row["version"]))
        return versions

    async def claim(self, archive: DisclosureArchive, now: datetime) -> bool:
        try:
            result = await self._inventory.update_one(
                {"_id": archive.name, "next_check": {"$lte": now}},
                {"$set": {"next_check": now + CHECK_INTERVAL}},
                upsert=True,
            )
        except DuplicateKeyError:
            return False
        return bool(result.modified_count or result.upserted_id)

    async def revision(
        self, archive: DisclosureArchive, scope: str
    ) -> ArchiveRevision | None:
        row = await self._inventory.find_one({"_id": archive.name, "scope": scope})
        revision = row.get("revision") if row else None
        if revision is None:
            return None
        return ArchiveRevision(revision.get("etag"), revision.get("last_modified"))

    async def complete(
        self, period: DisclosurePeriod, version: int, now: datetime
    ) -> None:
        await self._inventory.update_one(
            {"_id": _period_id(period)},
            {
                "$set": {
                    **_identity(period),
                    "state": "complete",
                    "completed_at": now,
                },
                "$max": {"version": version},
            },
            upsert=True,
        )
        await self._inventory.update_many(
            {
                **_identity(period),
                "state": "catalog_only",
                "version": {"$lte": version},
            },
            {"$set": {"state": "covered", "completed_at": now}},
        )

    async def pending(self, period: DisclosurePeriod, now: datetime) -> None:
        identity = _identity(period)
        key = _period_id(period)
        await self._inventory.update_one(
            {"_id": key},
            {"$setOnInsert": {**identity, "state": "pending", "checks": 0}},
            upsert=True,
        )
        await self._inventory.update_one(
            {"_id": key, "state": "pending"},
            [
                {"$set": {"checks": {"$add": ["$checks", 1]}, "last_checked_at": now}},
                {
                    "$set": {
                        "state": {
                            "$cond": [
                                {"$gte": ["$checks", MAX_PENDING_CHECKS]},
                                "exhausted",
                                "pending",
                            ]
                        }
                    }
                },
            ],
        )

    async def unresolved(self, archive: DisclosureArchive) -> set[DisclosurePeriod]:
        return {
            _period(row)
            async for row in self._inventory.find(
                {"archive": archive.name, "state": "pending"},
                {"code": 1, "kind": 1, "reference": 1},
            )
        }

    async def exhausted_versions(
        self, archive: DisclosureArchive
    ) -> set[tuple[DisclosurePeriod, int]]:
        return {
            (_period(row), int(row["version"]))
            async for row in self._inventory.find(
                {
                    "archive": archive.name,
                    "state": "catalog_only",
                    "checks": {"$gte": MAX_PENDING_CHECKS},
                },
                {"code": 1, "kind": 1, "reference": 1, "version": 1},
            )
        }

    async def catalog_only(
        self, period: DisclosurePeriod, version: int, now: datetime
    ) -> None:
        await self._inventory.update_one(
            {"_id": f"{_period_id(period)}:v{version}"},
            {
                "$set": {
                    **_identity(period),
                    "version": version,
                    "state": "catalog_only",
                    "last_checked_at": now,
                },
                "$inc": {"checks": 1},
            },
            upsert=True,
        )

    async def settle(
        self, archive: DisclosureArchive, scope: str, revision: ArchiveRevision | None
    ) -> None:
        await self._inventory.update_one(
            {"_id": archive.name},
            {
                "$set": {
                    "scope": scope,
                    "revision": (
                        {"etag": revision.etag, "last_modified": revision.last_modified}
                        if revision is not None
                        else None
                    ),
                }
            },
        )


def _period_id(period: DisclosurePeriod) -> str:
    return f"{period.kind}:{period.code}:{period.reference.isoformat()}"


def _identity(period: DisclosurePeriod) -> dict[str, str]:
    identity = {
        "archive": DisclosureArchive(period.kind, period.reference.year).name,
        "code": period.code,
        "kind": period.kind,
        "reference": period.reference.isoformat(),
    }
    if period.kind in ("ITR", "DFP"):
        identity["expected_by"] = expected_by(period).isoformat()
    return identity


def _period(row: dict[str, Any]) -> DisclosurePeriod:
    return DisclosurePeriod(
        str(row["code"]),
        cast(DisclosureKind, row["kind"]),
        date.fromisoformat(str(row["reference"])),
    )
