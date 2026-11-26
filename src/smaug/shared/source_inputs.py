"""Pin completed source revisions and read their immutable MongoDB rows."""

import asyncio
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from smaug.shared.errors import SourceMalformedError, SourceNotFoundError

SYNC_RUN_PREFIX = "financial-sync:"


class SourceCollection(Protocol):
    # Mongo cursors are driver-specific and also supplied by in-memory test stores.
    def find(
        self,
        filter: Mapping[str, Any],
        /,
        projection: Mapping[str, int] | None = None,
    ) -> Any: ...


@dataclass(frozen=True)
class SourceRevision:
    artifact_id: str
    parser_version: str
    dataset: str
    year: int
    source_url: str
    member_counts: Mapping[str, int]
    rejected_price_codes: frozenset[str] = frozenset()
    unscoped_price_rejection: bool = False


def _source_revision(doc: Mapping[str, Any]) -> SourceRevision:
    return SourceRevision(
        artifact_id=doc["artifact_id"],
        parser_version=doc["parser_version"],
        dataset=doc["dataset"],
        year=doc["year"],
        source_url=doc["source_url"],
        member_counts=dict(doc["member_counts"]),
        rejected_price_codes=frozenset(doc.get("rejected_price_codes", ())),
        unscoped_price_rejection=doc.get("unscoped_price_rejection", False),
    )


class MongoSourceInputs:
    """Select completed revisions once; never reacquire an unavailable source."""

    def __init__(self, archives: SourceCollection, rows: SourceCollection) -> None:
        self._archives = archives
        self._rows = rows
        self._revisions: dict[tuple[str, int], SourceRevision] | None = None
        self._pin_lock = asyncio.Lock()

    async def pin(self) -> Mapping[tuple[str, int], SourceRevision]:
        if self._revisions is not None:
            return self._revisions
        async with self._pin_lock:
            return await self._pin_revisions()

    async def _pin_revisions(self) -> Mapping[tuple[str, int], SourceRevision]:
        if self._revisions is None:
            docs = (
                await self._archives.find(
                    {
                        "parser_version": "source-inputs-v1",
                        "validation": "zip-crc-and-lossless-row-layout-v1",
                    }
                )
                .sort([("observed_at", 1), ("artifact_id", 1)])
                .to_list(None)
            )
            revisions = {}
            for doc in docs:
                if (
                    doc["dataset"] == "COTAHIST"
                    and doc.get("price_basis") != "as_traded"
                ):
                    continue
                revision = _source_revision(doc)
                revisions[(revision.dataset, revision.year)] = revision
            self._revisions = revisions
        return self._revisions

    async def revision(
        self, dataset: str, year: int, *, artifact_id: str | None = None
    ) -> SourceRevision:
        revision = (await self.pin()).get((dataset, year))
        if revision is None:
            raise SourceNotFoundError(f"missing_mirrored_source:{dataset}:{year}")
        if artifact_id is not None and revision.artifact_id != artifact_id:
            raise SourceNotFoundError(f"missing_mirrored_revision:{artifact_id}")
        return revision

    async def completed_revision(self, artifact_id: str) -> SourceRevision:
        docs = await self._archives.find(
            {"artifact_id": artifact_id, "parser_version": "source-inputs-v1"}
        ).to_list(1)
        if not docs:
            raise SourceNotFoundError(f"missing_mirrored_revision:{artifact_id}")
        return _source_revision(docs[0])

    async def price_rejections(self) -> tuple[frozenset[str], bool]:
        tapes = [
            revision
            for revision in (await self.pin()).values()
            if revision.dataset == "COTAHIST"
        ]
        return (
            frozenset(code for tape in tapes for code in tape.rejected_price_codes),
            any(tape.unscoped_price_rejection for tape in tapes),
        )

    async def _documents(
        self, revision: SourceRevision, member: str
    ) -> AsyncIterator[dict[str, Any]]:
        expected = revision.member_counts.get(member)
        if expected is None:
            raise SourceNotFoundError(f"missing_mirrored_member:{member}")
        cursor = self._rows.find(
            {
                "artifact_id": revision.artifact_id,
                "parser_version": revision.parser_version,
                "member": member,
            }
        ).sort("ordinal", 1)
        count = 0
        async for doc in cursor:
            count += 1
            if doc["ordinal"] != count:
                raise SourceMalformedError(f"incomplete_mirrored_member:{member}")
            if not isinstance(doc.get("fields"), Mapping):
                raise SourceMalformedError(f"malformed_mirrored_member:{member}")
            yield doc
        if count != expected:
            raise SourceMalformedError(f"incomplete_mirrored_member:{member}")

    async def rows(
        self, revision: SourceRevision, member: str
    ) -> AsyncIterator[dict[str, str]]:
        async for doc in self._documents(revision, member):
            yield dict(doc["fields"])

    async def cotahist_rows(
        self, revision: SourceRevision, member: str
    ) -> AsyncIterator[dict[str, str]]:
        """Validate the complete member server-side, then stream spot observations.

        Admitted rows are immutable and have a unique archive/parser/member/ordinal
        index. Count plus endpoints therefore proves the same ordinal continuity as
        the full streaming reader, including rows the price reducer does not use.
        """
        aggregate = getattr(self._rows, "aggregate", None)
        if aggregate is None:
            # Minimal stores retain the full validation/reduction path.
            async for row in self.rows(revision, member):
                yield row
            return
        expected = revision.member_counts.get(member)
        if expected is None:
            raise SourceNotFoundError(f"missing_mirrored_member:{member}")
        query = {
            "artifact_id": revision.artifact_id,
            "parser_version": revision.parser_version,
            "member": member,
        }
        malformed = {
            "$or": [
                {"$ne": [{"$type": "$fields"}, "object"]},
                {"$eq": [{"$type": "$fields.TIPREG"}, "missing"]},
                {
                    "$and": [
                        {"$eq": ["$fields.TIPREG", "01"]},
                        {"$eq": [{"$type": "$fields.TPMERC"}, "missing"]},
                    ]
                },
            ]
        }
        cursor = await aggregate(
            [
                {"$match": query},
                {
                    "$group": {
                        "_id": None,
                        "count": {"$sum": 1},
                        "first": {"$min": "$ordinal"},
                        "last": {"$max": "$ordinal"},
                        "invalid_ordinal": {
                            "$sum": {
                                "$cond": [
                                    {
                                        "$cond": [
                                            {"$isNumber": "$ordinal"},
                                            {
                                                "$eq": [
                                                    "$ordinal",
                                                    {"$trunc": "$ordinal"},
                                                ]
                                            },
                                            False,
                                        ]
                                    },
                                    0,
                                    1,
                                ]
                            }
                        },
                        "malformed": {"$sum": {"$cond": [malformed, 1, 0]}},
                    }
                },
            ]
        )
        stats = await cursor.to_list(1)
        if not stats and expected == 0:
            return
        if (
            not stats
            or stats[0]["count"] != expected
            or stats[0]["first"] != 1
            or stats[0]["last"] != expected
            or stats[0]["invalid_ordinal"]
        ):
            raise SourceMalformedError(f"incomplete_mirrored_member:{member}")
        if stats[0]["malformed"]:
            raise SourceMalformedError(f"malformed_mirrored_member:{member}")
        fields = (
            "TIPREG",
            "TPMERC",
            "PREULT",
            "FATCOT",
            "DATA",
            "CODNEG",
            "DISMES",
            "ESPECI",
            "NOMRES",
            "CODISI",
            "CODBDI",
        )
        cursor = self._rows.find(
            {**query, "fields.TIPREG": "01", "fields.TPMERC": "010"},
            {"_id": 0, **{f"fields.{field}": 1 for field in fields}},
        ).sort("ordinal", 1)
        async for doc in cursor:
            yield dict(doc["fields"])

    async def fca(self, revision: SourceRevision) -> dict[str, list[dict[str, str]]]:
        members = {}
        for member in (
            f"fca_cia_aberta_geral_{revision.year}.csv",
            f"fca_cia_aberta_valor_mobiliario_{revision.year}.csv",
        ):
            if member not in revision.member_counts:
                continue
            rows = []
            line_number = 1
            async for doc in self._documents(revision, member):
                if doc["ordinal"] == 1:
                    line_number += _line_breaks(doc["columns"])
                cells = doc["cells"]
                line_number += 1 + _line_breaks(cells)
                # DictReader skips blank physical rows. Reproduce its physical
                # line number from lossless cells, including quoted newlines.
                if not cells:
                    continue
                rows.append(
                    {**doc["fields"], "__source_line_number__": str(line_number)}
                )
            members[member] = rows
        return members


def _line_breaks(cells: list[str]) -> int:
    return sum(
        cell.count("\n") + cell.count("\r") - cell.count("\r\n") for cell in cells
    )
