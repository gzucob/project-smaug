"""Read B3 annual prices from admitted MongoDB rows, with no acquisition path."""

import asyncio
from collections.abc import Mapping

from smaug.analysis.infrastructure.quote_series import (
    YearQuotes,
    _Accumulator,
    accumulate_quotes,
)
from smaug.shared.errors import SourceMalformedError
from smaug.shared.source_inputs import MongoSourceInputs


class MongoCotahistArchive:
    def __init__(self, inputs: MongoSourceInputs) -> None:
        self._inputs = inputs
        self._years: dict[int, Mapping[str, YearQuotes]] = {}
        self._locks: dict[int, asyncio.Lock] = {}

    async def preload(self, *, concurrency: int = 3) -> None:
        """Warm admitted years with bounded cursors and per-year accumulators."""
        if not 1 <= concurrency <= 3:
            raise ValueError("archive concurrency must be between 1 and 3")
        semaphore = asyncio.Semaphore(concurrency)

        async def load(year: int) -> None:
            async with semaphore:
                try:
                    await self.year(year)
                except Exception:
                    # Prefetch cannot decide availability. The normal consumer
                    # retries failures and preserves its named error/null path.
                    pass

        revisions = await self._inputs.pin()
        async with asyncio.TaskGroup() as tasks:
            for dataset, year in revisions:
                if dataset == "COTAHIST":
                    tasks.create_task(load(year))

    async def year(self, year: int) -> Mapping[str, YearQuotes]:
        if year in self._years:
            return self._years[year]
        revision = await self._inputs.revision("COTAHIST", year)
        async with self._locks.setdefault(year, asyncio.Lock()):
            if year in self._years:
                return self._years[year]
            if len(revision.member_counts) != 1:
                raise SourceMalformedError(f"ambiguous_mirrored_cotahist:{year}")
            # Rows stream from MongoDB. Keep only per-code accumulators in memory.
            # The reducer accepts one shared accumulator map across cursor batches.
            totals: dict[str, _Accumulator] = {}
            member = next(iter(revision.member_counts))
            batch = []
            try:
                async for row in self._inputs.cotahist_rows(revision, member):
                    batch.append(row)
                    if len(batch) == 1000:
                        accumulate_quotes(batch, totals)
                        batch.clear()
                accumulate_quotes(batch, totals)
                values = {code: entry.freeze() for code, entry in totals.items()}
            except SourceMalformedError:
                raise
            except (KeyError, TypeError, ValueError, IndexError) as exc:
                raise SourceMalformedError(
                    f"malformed_mirrored_cotahist:{year}"
                ) from exc
            self._years[year] = values
            return values
