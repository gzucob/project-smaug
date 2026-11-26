"""Admit validated source archives without deriving financial values."""

import logging
from collections.abc import Awaitable, Callable, Iterable, Iterator, Mapping
from dataclasses import replace
from typing import Literal

from smaug.ingestion.domain.source_inputs import (
    SourceInputAcquirer,
    SourceInputReader,
    SourceInputRepository,
    SourceInputRow,
)

logger = logging.getLogger(__name__)


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
            rejected = 0
            codes: set[str] = set()
            unscoped = False

            def track_rejections() -> Iterator[SourceInputRow]:
                nonlocal rejected, unscoped
                for row in rows:
                    if "parse_error" in row.fields:
                        rejected += 1
                        if row.fields.get("price_relevance") != "non_spot":
                            if code := row.fields.get("CODNEG"):
                                codes.add(code)
                            else:
                                unscoped = True
                    yield row

            await self._repository.write_rows(archive, track_rejections())
            if rejected:
                archive = replace(
                    archive,
                    rejected_rows=rejected,
                    rejected_price_codes=tuple(sorted(codes)),
                    unscoped_price_rejection=unscoped,
                )
            await self._repository.complete(archive, reader.member_counts())
            if rejected:
                logger.warning(
                    "COTAHIST %d %s contains %d rejected rows (details in the mirror)",
                    archive.year,
                    archive.artifact_id,
                    rejected,
                )
            return True
        finally:
            reader.close()


def _verify_session_coverage(
    rows: Iterable[SourceInputRow], previous_sessions: set[str]
) -> Iterator[SourceInputRow]:
    missing = previous_sessions.copy()
    for row in rows:
        if row.fields["TIPREG"] in {"01", "rejected"}:
            missing.discard(row.fields.get("DATA", ""))
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
        retry_years: frozenset[int] = frozenset(),
    ) -> None:
        # The newest mirrored year can be partial, even when it is now a past year.
        # Check it again across a year boundary, and recover any earlier holes.
        years = {year for year in mirrored if year <= through}
        frontier = max(years, default=1986)
        for year in range(1986, through + 1):
            if year < frontier and year in years and year not in retry_years:
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
