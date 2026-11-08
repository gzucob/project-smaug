"""Who a security is, gathered across every year of the FCA.

``CvmCompanyRegistry`` reads one year and answers "what is this ticker today".
This reads every year and answers the two questions that need a history: what
else this share class has been **coded**, and what its registrant has been
**called**. One pass per year, one small ZIP per year (~400 KB), cached beside
the registry's own.

The two answers come from different halves of the archive, and deliberately from
different year ranges. The trading code exists only from 2018
(``FIRST_YEAR_WITH_TRADING_CODES``); the names go back to 2010, and it is the
early years that carry weight — the FCA is a snapshot as of each filing, so a
company that renamed is named by the years *before* it did, and nowhere else.

The join is the registrant's ``CNPJ`` plus the code's class digit — never the
company name, and never the code's root. A rename usually changes the name too
(``AREZZO CO`` → ``AZZAS 2154``), and B3 hands a retired root to whoever asks
next, so matching on either recovers a valid history belonging to somebody else
(the trap of #190).
"""

from __future__ import annotations

import asyncio
import csv
import io
import zipfile
from pathlib import Path

import httpx

from smaug.portfolio.domain.securities import (
    FIRST_FCA_YEAR,
)
from smaug.portfolio.infrastructure.cvm_registry import CVM_FCA_BASE_URL
from smaug.portfolio.infrastructure.fca_securities import (
    FcaSecurityHistory,
    _build_index,
    _Index,
)
from smaug.shared.artifacts import SourceArtifact, SourceArtifactStore
from smaug.shared.download import Sleeper, download_zip
from smaug.shared.errors import CvmDownloadError
from smaug.shared.logging import get_logger

logger = get_logger(__name__)

_ENCODING = "latin-1"
_DELIMITER = ";"


class CvmSecurityHistory(FcaSecurityHistory):
    """The codes each equity class has been filed under, gathered over FCA years."""

    def __init__(
        self,
        http_client: httpx.AsyncClient,
        *,
        through: int,
        cache_dir: str,
        since: int = FIRST_FCA_YEAR,
        base_url: str | None = None,
        sleep: Sleeper = asyncio.sleep,
        artifact_store: SourceArtifactStore | None = None,
        snapshot_year: int | None = None,
        snapshot_artifact_id: str | None = None,
    ) -> None:
        self._http = http_client
        self._years = range(since, through + 1)
        self._cache_dir = Path(cache_dir)
        self._base_url = (base_url or CVM_FCA_BASE_URL).rstrip("/")
        self._sleep = sleep
        self._artifact_store = artifact_store
        self._snapshot_year = snapshot_year
        self._snapshot_artifact_id = snapshot_artifact_id
        self._index: _Index | None = None
        self._lock = asyncio.Lock()

    async def _ensure_loaded(self) -> _Index:
        cached = self._index
        if cached is not None:
            return cached
        async with self._lock:
            cached = self._index
            if cached is not None:
                return cached
            self._cache_dir.mkdir(parents=True, exist_ok=True)
            archives: list[tuple[int, Path]] = []
            for year in self._years:
                artifact = await self._snapshot_artifact(year)
                if artifact is not None:
                    archives.append((year, artifact.path))
                    continue
                path = self._cache_dir / f"fca_cia_aberta_{year}.zip"
                if not path.exists():
                    url = f"{self._base_url}/{path.name}"
                    logger.info("Downloading CVM FCA %d from %s", year, url)
                    try:
                        await download_zip(
                            self._http,
                            url,
                            path,
                            follow_redirects=True,
                            sleep=self._sleep,
                        )
                    except CvmDownloadError:
                        # The running year is filed during it, so the archive may
                        # not exist yet. Read through today's year regardless: a
                        # code renamed this year is named by no earlier one, and
                        # EMBR3 -> EMBJ3 (November 2025) is exactly that case.
                        logger.info("CVM has not published the FCA for %d yet", year)
                        continue
                archives.append((year, path))
            index = await asyncio.to_thread(
                _build_index,
                [
                    (
                        year,
                        {
                            f"fca_cia_aberta_{member}_{year}.csv": _member(
                                year, path, member
                            )
                            for member in ("geral", "valor_mobiliario")
                        },
                    )
                    for year, path in archives
                ],
            )
            self._index = index
            logger.info(
                "Loaded CVM FCA %d-%d: %d codes share a class with another, "
                "%d registrants named",
                self._years.start,
                self._years.stop - 1,
                len(index.siblings),
                len(index.names),
            )
            return index

    async def _snapshot_artifact(self, year: int) -> SourceArtifact | None:
        """Reuse the current registry snapshot already stored in Bronze.

        The current FCA archive is also one of the historical inputs.  Opening
        its content-addressed artifact avoids a second HTTP acquisition (and
        avoids creating a second cache copy) while leaving older years on the
        history reader's normal cache/download path.
        """
        if (
            self._artifact_store is None
            or self._snapshot_artifact_id is None
            or self._snapshot_year != year
        ):
            return None
        try:
            return await self._artifact_store.open(self._snapshot_artifact_id)
        except FileNotFoundError:
            # A replay reference may outlive its local Bronze store.  The
            # history reader remains usable by falling back to its year cache.
            logger.info(
                "FCA Bronze artifact %s is unavailable; reading %d from cache",
                self._snapshot_artifact_id,
                year,
            )
            return None


def _member(year: int, path: Path, member: str) -> list[dict[str, str]]:
    """One CSV of the year's FCA archive, or nothing when it cannot be read."""
    name = f"fca_cia_aberta_{member}_{year}.csv"
    try:
        with zipfile.ZipFile(path) as archive:
            if name not in archive.namelist():
                return []
            raw = archive.read(name).decode(_ENCODING)
    except (OSError, zipfile.BadZipFile):
        logger.warning("CVM FCA %d is unreadable; its %s is skipped", year, member)
        return []
    return list(csv.DictReader(io.StringIO(raw), delimiter=_DELIMITER))
