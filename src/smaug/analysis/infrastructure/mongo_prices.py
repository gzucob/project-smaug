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
        self._lock = asyncio.Lock()

    async def year(self, year: int) -> Mapping[str, YearQuotes]:
        async with self._lock:
            if year in self._years:
                return self._years[year]
            revision = await self._inputs.revision("COTAHIST", year)
            if len(revision.member_counts) != 1:
                raise SourceMalformedError(f"ambiguous_mirrored_cotahist:{year}")
            # Rows stream from MongoDB. Keep only per-code accumulators in memory.
            # The reducer accepts one shared accumulator map across cursor batches.
            totals: dict[str, _Accumulator] = {}
            member = next(iter(revision.member_counts))
            batch = []
            try:
                async for row in self._inputs.rows(revision, member):
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
