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
import unicodedata
import zipfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

import httpx

from smaug.portfolio.domain.securities import (
    FIRST_FCA_YEAR,
    RegistrantNamesResolver,
    SiblingCodesResolver,
    name_key,
    share_class_suffix,
)
from smaug.portfolio.domain.share_classes import TickerCodeEvidence
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
        since: int = FIRST_FCA_YEAR,
        base_url: str | None = None,
        sleep: Sleeper = asyncio.sleep,
    ) -> None:
        self._http = http_client
        self._years = range(since, through + 1)
        self._cache_dir = Path(cache_dir)
        self._base_url = (base_url or CVM_FCA_BASE_URL).rstrip("/")
        self._sleep = sleep
        self._index: _Index | None = None
        self._lock = asyncio.Lock()

    async def resolver(self) -> SiblingCodesResolver:
        """Load the archive once and hand the composition root a plain callable."""
        index = await self._ensure_loaded()

        def siblings(ticker: str) -> tuple[str, ...]:
            return index.siblings.get(ticker.strip().upper(), ())

        return siblings

    async def names(self) -> RegistrantNamesResolver:
        """Every name a code's registrant has filed, folded for comparison.

        The FCA is a snapshot as of each year's own filing, so a company that
        renamed is only ever named by the *earlier* years — which is exactly what
        makes this useful: it is the record of what a registrant used to be
        called, and the tape is the record of what traded under that name (#198).
        """
        index = await self._ensure_loaded()

        def names_of(ticker: str) -> frozenset[str]:
            cnpj = index.registrant.get(ticker.strip().upper())
            return index.names.get(cnpj, frozenset()) if cnpj else frozenset()

        return names_of

    async def historical_codes(
        self,
    ) -> Callable[[str], tuple[TickerCodeEvidence, ...]]:
        """Return every FCA code witness for the ticker's economic class."""
        index = await self._ensure_loaded()

        def codes_of(ticker: str) -> tuple[TickerCodeEvidence, ...]:
            return index.codes_by_ticker.get(ticker.strip().upper(), ())

        return codes_of

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
                "Loaded CVM FCA %d-%d: %d codes share a class with another, "
                "%d registrants named",
                self._years.start,
                self._years.stop - 1,
                len(index.siblings),
                len(index.names),
            )
            return index


@dataclass(frozen=True, slots=True)
class _Index:
    """What one pass over the archive yields, keyed for the two questions asked."""

    siblings: dict[str, tuple[str, ...]]
    registrant: dict[str, str]  # code -> CNPJ
    names: dict[str, frozenset[str]]  # CNPJ -> every name it has filed
    codes_by_ticker: dict[str, tuple[TickerCodeEvidence, ...]]


def _build_index(archives: Sequence[tuple[int, Path]]) -> _Index:
    """Group every filed equity code by (registrant, class), then invert."""
    grouped: dict[tuple[str, str], set[str]] = {}
    observed: dict[tuple[str, str], dict[str, set[int]]] = {}
    registrant: dict[str, str] = {}
    names: dict[str, set[str]] = {}
    for year, path in archives:
        for cnpj, code in _codes(year, path):
            suffix = share_class_suffix(code)
            if suffix is None:
                continue
            grouped.setdefault((cnpj, suffix), set()).add(code)
            observed.setdefault((cnpj, suffix), {}).setdefault(code, set()).add(year)
            registrant.setdefault(code, cnpj)
        for cnpj, name in _names(year, path):
            key = name_key(name)
            if key:
                names.setdefault(cnpj, set()).add(key)
    siblings: dict[str, tuple[str, ...]] = {}
    codes_by_ticker: dict[str, tuple[TickerCodeEvidence, ...]] = {}
    for group, codes in grouped.items():
        evidence = tuple(
            TickerCodeEvidence(
                symbol=code,
                filed_years=tuple(sorted(observed[group].get(code, ()))),
            )
            for code in sorted(codes)
        )
        for code in codes:
            codes_by_ticker[code] = evidence
        if len(codes) < 2:
            continue
        for code in codes:
            siblings[code] = tuple(sorted(codes - {code}))
    return _Index(
        siblings=siblings,
        registrant=registrant,
        names={cnpj: frozenset(filed) for cnpj, filed in names.items()},
        codes_by_ticker=codes_by_ticker,
    )


def _names(year: int, path: Path) -> list[tuple[str, str]]:
    """``(CNPJ, Nome_Empresarial)`` from the year's general cadastre."""
    return [
        (cnpj, name)
        for row in _member(year, path, "geral")
        if (cnpj := (row.get("CNPJ_Companhia") or "").strip())
        and (name := (row.get("Nome_Empresarial") or "").strip())
    ]


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


def _codes(year: int, path: Path) -> list[tuple[str, str]]:
    """``(CNPJ, code)`` for every equity security the year's FCA names."""
    rows: list[tuple[str, str]] = []
    for row in _member(year, path, "valor_mobiliario"):
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
