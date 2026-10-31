"""Admit validated source archives without deriving financial values."""

from collections.abc import Iterable, Mapping
from typing import Protocol

from smaug.ingestion.domain.source_inputs import (
    SourceInputArchive,
    SourceInputRepository,
    SourceInputRow,
)


class SourceInputReader(Protocol):
    async def inspect(self) -> SourceInputArchive: ...

    def rows(self) -> Iterable[SourceInputRow]: ...

    def member_counts(self) -> Mapping[str, int]: ...

    def close(self) -> None: ...


class MirrorSourceInputs:
    def __init__(self, repository: SourceInputRepository) -> None:
        self._repository = repository

    async def execute(self, reader: SourceInputReader) -> bool:
        try:
            archive = await reader.inspect()
            if await self._repository.completed(archive):
                return False
            await self._repository.write_rows(archive, reader.rows())
            await self._repository.complete(archive, reader.member_counts())
            return True
        finally:
            reader.close()
