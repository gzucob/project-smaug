"""Pin completed source revisions and read their immutable MongoDB rows."""

from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from smaug.shared.errors import SourceMalformedError, SourceNotFoundError


class SourceCollection(Protocol):
    # Mongo cursors are driver-specific and also supplied by in-memory test stores.
    def find(self, filter: Mapping[str, Any], /) -> Any: ...


@dataclass(frozen=True)
class SourceRevision:
    artifact_id: str
    parser_version: str
    dataset: str
    year: int
    source_url: str
    member_counts: Mapping[str, int]


class MongoSourceInputs:
    """Select completed revisions once; never reacquire an unavailable source."""

    def __init__(self, archives: SourceCollection, rows: SourceCollection) -> None:
        self._archives = archives
        self._rows = rows
        self._revisions: dict[tuple[str, int], SourceRevision] | None = None

    async def pin(self) -> Mapping[tuple[str, int], SourceRevision]:
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
                revision = SourceRevision(
                    artifact_id=doc["artifact_id"],
                    parser_version=doc["parser_version"],
                    dataset=doc["dataset"],
                    year=doc["year"],
                    source_url=doc["source_url"],
                    member_counts=dict(doc["member_counts"]),
                )
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
