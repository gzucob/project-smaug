"""Every trading code a registrant has filed for a share class, across FCA years.

``CvmCompanyRegistry`` reads one year of the FCA and answers "what is this ticker
today". This reads the same securities member across every year that carries the
column and answers the other question: "what else has this share class been
called". One pass per year, one small ZIP per year (~400 KB), cached beside the
registry's own.

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
import unicodedata
import zipfile
from collections.abc import Sequence
from pathlib import Path

import httpx

from smaug.portfolio.domain.securities import (
    FIRST_YEAR_WITH_TRADING_CODES,
    SiblingCodesResolver,
    share_class_suffix,
)
from smaug.portfolio.infrastructure.cvm_registry import CVM_FCA_BASE_URL
from smaug.shared.download import Sleeper, download_zip
from smaug.shared.errors import CvmDownloadError
from smaug.shared.logging import get_logger

logger = get_logger(__name__)

_ENCODING = "latin-1"
_DELIMITER = ";"


class CvmSecurityHistory:
    """The codes each equity class has been filed under, gathered over FCA years."""

    def __init__(
        self,
        http_client: httpx.AsyncClient,
        *,
        through: int,
        cache_dir: str,
        since: int = FIRST_YEAR_WITH_TRADING_CODES,
        base_url: str | None = None,
        sleep: Sleeper = asyncio.sleep,
    ) -> None:
        self._http = http_client
        self._years = range(since, through + 1)
        self._cache_dir = Path(cache_dir)
        self._base_url = (base_url or CVM_FCA_BASE_URL).rstrip("/")
        self._sleep = sleep
        self._index: dict[str, tuple[str, ...]] | None = None
        self._lock = asyncio.Lock()

    async def resolver(self) -> SiblingCodesResolver:
        """Load the archive once and hand the composition root a plain callable."""
        index = await self._ensure_loaded()

        def siblings(ticker: str) -> tuple[str, ...]:
            return index.get(ticker.strip().upper(), ())

        return siblings

    async def _ensure_loaded(self) -> dict[str, tuple[str, ...]]:
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
            index = await asyncio.to_thread(_build_index, archives)
            self._index = index
            logger.info(
                "Loaded CVM FCA %d-%d: %d codes share a class with another",
                self._years.start,
                self._years.stop - 1,
                len(index),
            )
            return index


def _build_index(archives: Sequence[tuple[int, Path]]) -> dict[str, tuple[str, ...]]:
    """Group every filed equity code by (registrant, class), then invert."""
    grouped: dict[tuple[str, str], set[str]] = {}
    for year, path in archives:
        for cnpj, code in _codes(year, path):
            suffix = share_class_suffix(code)
            if suffix is None:
                continue
            grouped.setdefault((cnpj, suffix), set()).add(code)
    index: dict[str, tuple[str, ...]] = {}
    for codes in grouped.values():
        if len(codes) < 2:
            continue
        for code in codes:
            index[code] = tuple(sorted(codes - {code}))
    return index


def _codes(year: int, path: Path) -> list[tuple[str, str]]:
    """``(CNPJ, code)`` for every equity security the year's FCA names."""
    member = f"fca_cia_aberta_valor_mobiliario_{year}.csv"
    try:
        with zipfile.ZipFile(path) as archive:
            if member not in archive.namelist():
                return []
            raw = archive.read(member).decode(_ENCODING)
    except (OSError, zipfile.BadZipFile):
        logger.warning("CVM FCA %d is unreadable; its codes are skipped", year)
        return []
    rows: list[tuple[str, str]] = []
    for row in csv.DictReader(io.StringIO(raw), delimiter=_DELIMITER):
        code = (row.get("Codigo_Negociacao") or "").strip().upper()
        cnpj = (row.get("CNPJ_Companhia") or "").strip()
        # Only shares: a debenture or a BDR line carries a code too, and a unit
        # is a different share base (see ``share_class_suffix``).
        if code and cnpj and _is_share(row.get("Valor_Mobiliario") or ""):
            rows.append((cnpj, code))
    return rows


def _is_share(valor_mobiliario: str) -> bool:
    """Whether an FCA ``Valor_Mobiliario`` label names plain ON/PN equity."""
    decomposed = unicodedata.normalize("NFKD", valor_mobiliario)
    folded = "".join(c for c in decomposed if not unicodedata.combining(c))
    return folded.strip().lower().startswith("acoes")
