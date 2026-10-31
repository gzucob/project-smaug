"""Raw source rows and completed archive admission boundaries."""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True, slots=True)
class SourceInputRow:
    member: str
    ordinal: int
    fields: Mapping[str, str]
    columns: tuple[str, ...] = ()
    cells: tuple[str, ...] = ()


@dataclass(frozen=True)
class SourceInputArchive:
    artifact_id: str
    sha256: str
    byte_size: int
    source_url: str
    dataset: str
    year: int
    observed_at: datetime
    parser_version: str = "source-inputs-v1"
    acquisition: str = "local_import"
    downloaded_at: datetime | None = None
    etag: str | None = None
    last_modified: str | None = None


class SourceInputRepository(Protocol):
    async def completed(self, archive: SourceInputArchive) -> bool: ...

    async def write_rows(
        self, archive: SourceInputArchive, rows: Iterable[SourceInputRow]
    ) -> None: ...

    async def complete(
        self, archive: SourceInputArchive, member_counts: Mapping[str, int]
    ) -> None: ...
