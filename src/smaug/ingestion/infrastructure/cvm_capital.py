"""CVM capital composition (share counts) — the FRE side of the raw mirror.

The share count is the one number the statements never carry: BPA/BPP/DRE/DFC
say nothing about how many shares exist. CVM publishes it in the FRE
(*Formulário de Referência*), a yearly ZIP keyed by **CNPJ** rather than by the
``CD_CVM`` the statements use. Inside it, ``fre_cia_aberta_capital_social_*``
lists each company's capital, so this source mirrors the paid-in row as filed —
ordinary, preferred and total share counts, no arithmetic (that is Phase 2).

Two real-world quirks are handled here:
  * pycvm's ``FREFile`` rejects the modern files (``BadDocument: unknown
    document type 'FRE WEB'``), so the CSV member is read directly.
  * A company files the same reference date several times (``Versao``); the
    highest version is the amendment that supersedes the rest.
"""

from __future__ import annotations

import asyncio
import csv
import io
import zipfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import httpx

from smaug.ingestion.domain.ports import RawFetchResult
from smaug.ingestion.infrastructure.download import Sleeper, download_zip
from smaug.shared.errors import BrapiNotFoundError
from smaug.shared.logging import get_logger

logger = get_logger(__name__)

CVM_FRE_BASE_URL = "https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/FRE/DADOS"

# The module name this source answers to, alongside the statement modules.
CAPITAL_MODULE = "CAPITAL"

# Of the three capital rows a company files (issued / subscribed / paid-in),
# paid-in is the one that reflects shares actually in existence.
_PAID_IN_CAPITAL = "Capital Integralizado"

# The FRE CSVs are latin-1 and semicolon-separated, like every CVM open dataset.
_ENCODING = "latin-1"
_DELIMITER = ";"


def _int(value: str | None) -> int:
    return int(value) if value else 0


class CvmCapitalSource:
    """Fetch the capital composition for one ticker from CVM's yearly FRE file."""

    def __init__(
        self,
        http_client: httpx.AsyncClient,
        ticker_to_cnpj: Mapping[str, str],
        *,
        year: int,
        cache_dir: str,
        base_url: str | None = None,
        sleep: Sleeper = asyncio.sleep,
    ) -> None:
        self._http = http_client
        self._ticker_to_cnpj = dict(ticker_to_cnpj)
        self._year = year
        self._cache_dir = Path(cache_dir)
        self._base_url = (base_url or CVM_FRE_BASE_URL).rstrip("/")
        self._sleep = sleep
        self._index: dict[str, dict[str, Any]] | None = None
        self._lock = asyncio.Lock()

    @property
    def _zip_name(self) -> str:
        return f"fre_cia_aberta_{self._year}.zip"

    @property
    def _member_name(self) -> str:
        return f"fre_cia_aberta_capital_social_{self._year}.csv"

    async def fetch(self, ticker: str, module: str) -> Sequence[RawFetchResult]:
        """Return the paid-in capital row for ``ticker`` (one result, one year)."""
        index = await self._ensure_loaded()

        cnpj = self._ticker_to_cnpj.get(ticker)
        if cnpj is None:
            raise BrapiNotFoundError(f"no CNPJ mapped for {ticker}")
        row = index.get(cnpj)
        if row is None:
            raise BrapiNotFoundError(
                f"no CVM {self._year} FRE capital for {ticker} ({cnpj})"
            )

        return [
            RawFetchResult(
                module=module,
                request={
                    "source": "cvm",
                    "file": self._zip_name,
                    "cnpj": cnpj,
                    "statement": module,
                    "reference_date": row["reference_date"],
                },
                http_status=200,
                payload=row,
            )
        ]

    async def _ensure_loaded(self) -> dict[str, dict[str, Any]]:
        cached = self._index
        if cached is not None:
            return cached
        async with self._lock:
            cached = self._index
            if cached is not None:
                return cached
            self._cache_dir.mkdir(parents=True, exist_ok=True)
            raw = self._cache_dir / self._zip_name
            if not raw.exists():
                await self._download(raw)
            index = await asyncio.to_thread(self._build_index, raw)
            self._index = index
            logger.info(
                "Loaded CVM FRE %s: %d of %d portfolio companies found",
                self._year,
                len(index),
                len(set(self._ticker_to_cnpj.values())),
            )
            return index

    async def _download(self, dst: Path) -> None:
        # Same shared-file reasoning as the statements ZIP: retry + atomic
        # write, and a definitive failure is fatal for the run (#16).
        url = f"{self._base_url}/{self._zip_name}"
        logger.info("Downloading CVM FRE %s from %s", self._year, url)
        await download_zip(
            self._http, url, dst, follow_redirects=True, sleep=self._sleep
        )

    def _build_index(self, archive: Path) -> dict[str, dict[str, Any]]:
        """Index the paid-in capital row per wanted CNPJ (sync; runs in a thread).

        When a CNPJ files the same year more than once, the highest ``Versao``
        wins — later versions are amendments to the earlier ones.
        """
        wanted = set(self._ticker_to_cnpj.values())
        best: dict[str, tuple[int, dict[str, Any]]] = {}
        with zipfile.ZipFile(archive) as archive_file:
            with archive_file.open(self._member_name) as member:
                reader = csv.DictReader(
                    io.TextIOWrapper(member, encoding=_ENCODING),
                    delimiter=_DELIMITER,
                )
                for row in reader:
                    cnpj = row["CNPJ_Companhia"]
                    if cnpj not in wanted or row["Tipo_Capital"] != _PAID_IN_CAPITAL:
                        continue
                    version = _int(row["Versao"])
                    current = best.get(cnpj)
                    if current is None or version > current[0]:
                        best[cnpj] = (version, _to_payload(row))
        return {cnpj: payload for cnpj, (_, payload) in best.items()}


def _to_payload(row: Mapping[str, str]) -> dict[str, Any]:
    """Mirror the filed row — share counts as filed, no derivation."""
    return {
        "cnpj": row["CNPJ_Companhia"],
        "company_name": row["Nome_Companhia"],
        "reference_date": row["Data_Referencia"],
        "version": _int(row["Versao"]),
        "capital_type": row["Tipo_Capital"],
        "common_shares": _int(row["Quantidade_Acoes_Ordinarias"]),
        "preferred_shares": _int(row["Quantidade_Acoes_Preferenciais"]),
        "total_shares": _int(row["Quantidade_Total_Acoes"]),
    }
