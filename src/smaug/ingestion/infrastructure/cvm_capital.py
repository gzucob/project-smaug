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
from smaug.ingestion.infrastructure.cvm_source import (
    _DOCUMENT_BASE_URL,
    _DOCUMENT_PREFIX,
    CvmDocument,
)
from smaug.ingestion.infrastructure.download import Sleeper, download_zip
from smaug.shared.errors import BrapiNotFoundError
from smaug.shared.logging import get_logger

logger = get_logger(__name__)

CVM_FRE_BASE_URL = "https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/FRE/DADOS"

# The module names these sources answer to, alongside the statement modules.
# CAPITAL is the FRE's share count (the primary one, ADR 0004); CAPITAL_DFP is the
# statements ZIP's own composition, which is what carries treasury shares.
CAPITAL_MODULE = "CAPITAL"
TREASURY_MODULE = "CAPITAL_DFP"

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
        self._index: dict[str, list[dict[str, Any]]] | None = None
        self._lock = asyncio.Lock()

    @property
    def _zip_name(self) -> str:
        return f"fre_cia_aberta_{self._year}.zip"

    @property
    def _member_name(self) -> str:
        return f"fre_cia_aberta_capital_social_{self._year}.csv"

    async def fetch(self, ticker: str, module: str) -> Sequence[RawFetchResult]:
        """Return every paid-in capital row ``ticker`` filed — one per amendment.

        The mirror keeps all of them and picks none (ADR 0016); the reader takes
        the highest ``version`` for the year. The FRE is heavily amended (BBDC4 is
        on v30), so which one supersedes which is exactly the kind of judgement
        that does not belong in an append-only mirror.
        """
        index = await self._ensure_loaded()

        cnpj = self._ticker_to_cnpj.get(ticker)
        if cnpj is None:
            raise BrapiNotFoundError(f"no CNPJ mapped for {ticker}")
        rows = index.get(cnpj)
        if not rows:
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
                    "version": row["version"],
                },
                http_status=200,
                payload=row,
            )
            for row in rows
        ]

    async def _ensure_loaded(self) -> dict[str, list[dict[str, Any]]]:
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

    def _build_index(self, archive: Path) -> dict[str, list[dict[str, Any]]]:
        """Index every paid-in capital row per wanted CNPJ (sync; runs in a thread).

        Every amendment is kept, not just the latest (ADR 0016) — the reader picks.
        """
        wanted = set(self._ticker_to_cnpj.values())
        index: dict[str, list[dict[str, Any]]] = {}
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
                    index.setdefault(cnpj, []).append(_to_payload(row))
        return index


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


class CvmTreasurySource:
    """Fetch the DFP/ITR's own capital composition — the one that names treasury.

    The statements ZIP carries a ``composicao_capital`` member the FRE has no
    equivalent of: it reports **shares held in treasury**, which are issued but not
    outstanding, and which the market cap (ADR 0014) arguably should not count.

    It does **not** replace the FRE as the share-count source (ADR 0004 stands).
    Its counts are filed at an inconsistent scale — TAEE11, VALE3 and CXSE3 file
    thousands while PETR4, BBAS3 and WEGE3 file units, and the member has **no
    scale column** to tell them apart. So it is mirrored exactly as filed, scale
    problem and all, and resolving that is the reader's problem, not the mirror's.
    """

    def __init__(
        self,
        http_client: httpx.AsyncClient,
        ticker_to_cnpj: Mapping[str, str],
        *,
        year: int,
        cache_dir: str,
        document: CvmDocument = "DFP",
        base_url: str | None = None,
        sleep: Sleeper = asyncio.sleep,
    ) -> None:
        self._http = http_client
        self._ticker_to_cnpj = dict(ticker_to_cnpj)
        self._year = year
        self._cache_dir = Path(cache_dir)
        self._document = document
        self._prefix = _DOCUMENT_PREFIX[document]
        self._base_url = (base_url or _DOCUMENT_BASE_URL[document]).rstrip("/")
        self._sleep = sleep
        self._index: dict[str, list[dict[str, Any]]] | None = None
        self._lock = asyncio.Lock()

    @property
    def _zip_name(self) -> str:
        return f"{self._prefix}_{self._year}.zip"

    @property
    def _member_name(self) -> str:
        return f"{self._prefix}_composicao_capital_{self._year}.csv"

    async def fetch(self, ticker: str, module: str) -> Sequence[RawFetchResult]:
        """Return every capital-composition row ``ticker`` filed — one per version."""
        index = await self._ensure_loaded()

        cnpj = self._ticker_to_cnpj.get(ticker)
        if cnpj is None:
            raise BrapiNotFoundError(f"no CNPJ mapped for {ticker}")
        rows = index.get(cnpj)
        if not rows:
            raise BrapiNotFoundError(
                f"no CVM {self._year} {self._document} capital for {ticker} ({cnpj})"
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
                    "version": row["version"],
                },
                http_status=200,
                payload=row,
            )
            for row in rows
        ]

    async def _ensure_loaded(self) -> dict[str, list[dict[str, Any]]]:
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
                url = f"{self._base_url}/{self._zip_name}"
                logger.info("Downloading CVM %s %s", self._document, self._year)
                await download_zip(self._http, url, raw, sleep=self._sleep)
            index = await asyncio.to_thread(self._build_index, raw)
            self._index = index
            return index

    def _build_index(self, archive: Path) -> dict[str, list[dict[str, Any]]]:
        wanted = set(self._ticker_to_cnpj.values())
        index: dict[str, list[dict[str, Any]]] = {}
        with zipfile.ZipFile(archive) as archive_file:
            with archive_file.open(self._member_name) as member:
                reader = csv.DictReader(
                    io.TextIOWrapper(member, encoding=_ENCODING),
                    delimiter=_DELIMITER,
                )
                for row in reader:
                    cnpj = row["CNPJ_CIA"]
                    if cnpj in wanted:
                        index.setdefault(cnpj, []).append(_to_treasury_payload(row))
        return index


def _to_treasury_payload(row: Mapping[str, str]) -> dict[str, Any]:
    """Mirror the filed row. The counts carry the filer's own scale — see the class."""
    return {
        "cnpj": row["CNPJ_CIA"],
        "company_name": row["DENOM_CIA"],
        "reference_date": row["DT_REFER"],
        "version": _int(row["VERSAO"]),
        "common_shares": _int(row["QT_ACAO_ORDIN_CAP_INTEGR"]),
        "preferred_shares": _int(row["QT_ACAO_PREF_CAP_INTEGR"]),
        "total_shares": _int(row["QT_ACAO_TOTAL_CAP_INTEGR"]),
        "treasury_common_shares": _int(row["QT_ACAO_ORDIN_TESOURO"]),
        "treasury_preferred_shares": _int(row["QT_ACAO_PREF_TESOURO"]),
        "treasury_total_shares": _int(row["QT_ACAO_TOTAL_TESOURO"]),
    }
