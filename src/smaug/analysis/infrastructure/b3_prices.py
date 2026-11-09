"""Legacy COTAHIST acquisition for explicit ingestion and reference tests.

Analysis composition uses MongoCotahistArchive and the pure quote_series module.
This adapter remains available to ingestion's targeted identity repair until
price acquisition moves into the dedicated ingestion flow in phase 7 (#354).
"""

from __future__ import annotations

import asyncio
import io
import json
import re
import zipfile
from collections.abc import Callable, Mapping, Sequence
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

import httpx

from smaug.analysis.infrastructure.quote_series import (
    _REDUCTION_VERSION,
    B3_SERIES_BASE_URL,
    B3BaseChanges,
    B3PriceHistory,
    B3PriceProvider,
    B3QuoteProvider,
    CodesResolver,
    IdentityState,
    QuoteArchive,
    RightsState,
    YearQuotes,
    reduce_records,
)
from smaug.shared.download import Sleeper, download_zip
from smaug.shared.errors import (
    CvmDownloadError,
    SourceMalformedError,
    SourceTimeoutError,
)
from smaug.shared.logging import get_logger

__all__ = [
    "B3BaseChanges",
    "B3PriceHistory",
    "B3PriceProvider",
    "B3QuoteProvider",
    "B3_SERIES_BASE_URL",
    "CodesResolver",
    "IdentityState",
    "QuoteArchive",
    "RightsState",
    "YearQuotes",
]

logger = get_logger(__name__)
_ARCHIVE_NAME = re.compile(r"^COTAHIST_A(?P<year>[0-9]{4})$", re.IGNORECASE)


def _quote_member(archive_path: Path, names: Sequence[str]) -> str:
    """Select exactly one official quote member for the archive's year."""
    match = _ARCHIVE_NAME.fullmatch(archive_path.stem)
    if match is None:
        raise ValueError(f"{archive_path.name} has an invalid COTAHIST filename")
    year = match.group("year")
    expected = {
        f"COTAHIST_A{year}.TXT",
        f"COTAHIST.A{year}",
        f"COTAHIST_A{year}",
    }
    members = [name for name in names if name.upper() in expected]
    if not members:
        raise ValueError(f"{archive_path.name} carries no supported COTAHIST member")
    if len(members) > 1:
        raise ValueError(
            f"{archive_path.name} carries ambiguous COTAHIST members: "
            f"{', '.join(sorted(members))}"
        )
    return members[0]


def _reduce(archive_path: Path) -> dict[str, YearQuotes]:
    with zipfile.ZipFile(archive_path) as archive:
        member = _quote_member(archive_path, archive.namelist())
        with archive.open(member) as raw:
            return reduce_records(io.TextIOWrapper(raw, encoding="latin-1", newline=""))


def _dump(reduction: Mapping[str, YearQuotes], built_on: date) -> str:
    return json.dumps(
        {
            "version": _REDUCTION_VERSION,
            "built_on": built_on.isoformat(),
            "codes": {
                code: [
                    quotes.sessions,
                    str(quotes.average),
                    quotes.last_session.isoformat(),
                    str(quotes.last_close),
                    quotes.closes,
                    quotes.rights,
                    quotes.name,
                    quotes.isin,
                    quotes.especi,
                    quotes.bdi,
                    quotes.identities,
                ]
                for code, quotes in reduction.items()
            },
        }
    )


def _load(text: str) -> tuple[dict[str, YearQuotes], date] | None:
    """Parse a cached reduction, or ``None`` when it is unusable/outdated."""
    try:
        payload = json.loads(text)
        if payload.get("version") != _REDUCTION_VERSION:
            return None
        built_on = date.fromisoformat(payload["built_on"])
        codes = {
            code: YearQuotes(
                sessions=int(row[0]),
                average=Decimal(row[1]),
                last_session=date.fromisoformat(row[2]),
                last_close=Decimal(row[3]),
                closes=row[4],
                rights=row[5],
                name=row[6],
                isin=row[7],
                especi=row[8],
                bdi=row[9],
                identities=row[10],
            )
            for code, row in payload["codes"].items()
        }
    except (ValueError, KeyError, TypeError, InvalidOperation):
        return None
    return codes, built_on


class CotahistArchive:
    """B3's yearly quote series: downloaded once, reduced once, served per code.

    One instance is shared by both providers below and by every ticker of a run,
    so a whole-exchange analysis streams each year's archive at most once. The
    reduction is memoized in memory and persisted beside the ZIP, which is what
    keeps a second run from re-reading gigabytes it has already summarized.
    """

    def __init__(
        self,
        http_client: httpx.AsyncClient,
        *,
        cache_dir: str,
        base_url: str = B3_SERIES_BASE_URL,
        sleep: Sleeper = asyncio.sleep,
        today: Callable[[], date] = date.today,
        refresh_current_year: bool = False,
    ) -> None:
        self._refresh_current_year = refresh_current_year
        self._http = http_client
        self._cache_dir = Path(cache_dir)
        self._base_url = base_url.rstrip("/")
        self._sleep = sleep
        self._today = today
        self._years: dict[int, Mapping[str, YearQuotes]] = {}
        self._lock = asyncio.Lock()

    def _zip_name(self, year: int) -> str:
        return f"COTAHIST_A{year}.ZIP"

    def _reduction_path(self, year: int) -> Path:
        return self._cache_dir / f"cotahist_{year}.json"

    async def year(self, year: int) -> Mapping[str, YearQuotes]:
        """Every spot-market code quoted in ``year``, reduced."""
        cached = self._years.get(year)
        if cached is not None:
            return cached
        async with self._lock:
            cached = self._years.get(year)
            if cached is not None:
                return cached
            reduction = await self._resolve(year)
            self._years[year] = reduction
            return reduction

    async def _resolve(self, year: int) -> Mapping[str, YearQuotes]:
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        today = self._today()
        reduction_path = self._reduction_path(year)
        if reduction_path.exists():
            loaded = await asyncio.to_thread(
                lambda: _load(reduction_path.read_text(encoding="utf-8"))
            )
            if loaded is not None and self._is_fresh(loaded[1], year, today):
                logger.info(
                    "B3 %d: %d codes from the cached reduction", year, len(loaded[0])
                )
                return loaded[0]

        archive = self._cache_dir / self._zip_name(year)
        if not archive.exists() or not self._is_fresh(
            date.fromtimestamp(archive.stat().st_mtime), year, today
        ):
            url = f"{self._base_url}/{self._zip_name(year)}"
            logger.info("Downloading B3 quote series %d from %s", year, url)
            try:
                await download_zip(self._http, url, archive, sleep=self._sleep)
            except CvmDownloadError as exc:
                # ``download_zip`` retains the transport exception as its cause;
                # keep a timeout distinct from an unavailable archive for the
                # persisted indicator null reason.
                if isinstance(exc.__cause__, httpx.TimeoutException):
                    raise SourceTimeoutError(
                        f"timed out downloading B3 {self._zip_name(year)}"
                    ) from exc
                raise

        try:
            reduction = await asyncio.to_thread(_reduce, archive)
        except (IndexError, UnicodeError, ValueError, zipfile.BadZipFile) as exc:
            raise SourceMalformedError(
                f"malformed B3 quote archive {archive.name}"
            ) from exc
        await asyncio.to_thread(
            lambda: reduction_path.write_text(_dump(reduction, today), encoding="utf-8")
        )
        logger.info(
            "B3 %d: reduced %s to %d spot-market codes",
            year,
            self._zip_name(year),
            len(reduction),
        )
        return reduction

    def _is_fresh(self, built_on: date, year: int, today: date) -> bool:
        """Whether a cached artifact for ``year`` can still be trusted.

        A closed year is immutable only if downloaded after it ended. The
        running year is republished every session, so a copy built before today
        is missing sessions and gets replaced.
        """
        if year < today.year:
            return built_on.year > year
        return not self._refresh_current_year and built_on >= today
