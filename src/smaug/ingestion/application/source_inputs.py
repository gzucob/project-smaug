"""Admit validated source archives without deriving financial values."""

from collections.abc import Awaitable, Callable, Iterable, Iterator, Mapping
from typing import Literal

from smaug.ingestion.domain.source_inputs import (
    SourceInputAcquirer,
    SourceInputReader,
    SourceInputRepository,
    SourceInputRow,
)


class MirrorSourceInputs:
    def __init__(self, repository: SourceInputRepository) -> None:
        self._repository = repository

    async def execute(
        self,
        reader: SourceInputReader,
        *,
        expected: tuple[str, int] | None = None,
        previous_sessions: Callable[[], Awaitable[set[str]]] | None = None,
    ) -> bool:
        try:
            archive = await reader.inspect()
            if expected is not None and (archive.dataset, archive.year) != expected:
                raise ValueError(f"unexpected source archive for {expected}")
            if await self._repository.completed(archive):
                return False
            rows = reader.rows()
            if previous_sessions is not None:
                rows = _verify_session_coverage(rows, await previous_sessions())
            await self._repository.write_rows(archive, rows)
            await self._repository.complete(archive, reader.member_counts())
            return True
        finally:
            reader.close()


def _verify_session_coverage(
    rows: Iterable[SourceInputRow], previous_sessions: set[str]
) -> Iterator[SourceInputRow]:
    missing = previous_sessions.copy()
    for row in rows:
        if row.fields["TIPREG"] == "01":
            missing.discard(row.fields["DATA"])
        yield row
    if missing:
        raise ValueError(
            f"COTAHIST publication omits mirrored sessions: {sorted(missing)}"
        )


class SyncCotahistUseCase:
    """Refresh the last mirrored B3 year and recover missing annual tapes."""

    def __init__(
        self, acquirer: SourceInputAcquirer, mirror: MirrorSourceInputs
    ) -> None:
        self._acquirer = acquirer
        self._mirror = mirror

    async def execute(
        self,
        mirrored: Mapping[int, Callable[[], Awaitable[set[str]]]],
        *,
        through: int,
    ) -> None:
        # The newest mirrored year can be partial, even when it is now a past year.
        # Check it again across a year boundary, and recover any earlier holes.
        years = {year for year in mirrored if year <= through}
        frontier = max(years, default=1986)
        for year in range(1986, through + 1):
            if year < frontier and year in years:
                continue
            reader = await self._acquirer.acquire("COTAHIST", year)
            await self._mirror.execute(
                reader,
                expected=("COTAHIST", year),
                previous_sessions=mirrored.get(year),
            )


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
