"""Admit validated source archives without deriving financial values."""

from typing import Literal

from smaug.ingestion.domain.source_inputs import (
    SourceInputAcquirer,
    SourceInputReader,
    SourceInputRepository,
)


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


class PrepareSourceInputsUseCase:
    """Explicitly ingest initial source coverage before database-only consumers."""

    def __init__(
        self, acquirer: SourceInputAcquirer, mirror: MirrorSourceInputs
    ) -> None:
        self._acquirer = acquirer
        self._mirror = mirror

    async def execute(self, *, fca_through: int, prices_through: int) -> None:
        datasets: tuple[tuple[Literal["FCA", "COTAHIST"], int, int], ...] = (
            ("FCA", 2010, fca_through),
            ("COTAHIST", 1986, prices_through),
        )
        for dataset, since, through in datasets:
            for year in range(since, through + 1):
                reader = await self._acquirer.acquire(dataset, year)
                await self._mirror.execute(reader)
