"""CVM raw data source — parses dados.cvm.gov.br into the ``RawDataSource`` port.

Unlike brapi (one HTTP call per ticker/module), CVM ships one yearly ZIP with
*every* company. So this source downloads that ZIP once, caches it, reads the
statement CSVs it contains, and serves each ticker/statement from the in-memory
index. It stays a faithful mirror: it stores the raw statement accounts (code,
name, value) exactly as filed — no indicators, no math (that is Phase 2).

The statement CSVs are read directly (ADR 0009), not through pycvm: pycvm's
reader crashed on the real DMPL, and — worse — its parallel batch reader
desynchronised on a duplicated head row and silently dropped whole consolidated
collections (#55), so we mirrored parent-only statements without noticing.
Reading each ``{statement}_{con|ind}`` member on its own removes both failure
modes and lets us pick the amendment (highest ``VERSAO``) deterministically.

Three real-world quirks are handled here:
  * CVM is keyed by ``CD_CVM``, not by B3 ticker — hence the injected
    ticker -> code map (see ``portfolio.domain.cvm_codes``).
  * A company files the same reference date several times (``VERSAO``); the
    highest version is the amendment that supersedes the rest.
  * Consolidated and individual (parent-only) statements live in separate
    members; the consolidated is preferred, the individual is the fallback for a
    filer that reports no consolidated (e.g. SAPR11).
"""

from __future__ import annotations

import asyncio
import csv
import io
import unicodedata
import zipfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import httpx

from smaug.ingestion.domain.ports import RawFetchResult
from smaug.ingestion.infrastructure.download import Sleeper, download_zip
from smaug.shared.errors import BrapiNotFoundError
from smaug.shared.logging import get_logger

logger = get_logger(__name__)

CVM_ITR_BASE_URL = "https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/ITR/DADOS"
CVM_DFP_BASE_URL = "https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/DFP/DADOS"

# document kind -> (base URL, file-name prefix). ITR = quarterly (YTD periods),
# DFP = annual closed year (single 12-month period). Same CSV layout; only the
# URL and file name differ.
CvmDocument = Literal["ITR", "DFP"]
_DOCUMENT_BASE_URL: dict[str, str] = {
    "ITR": CVM_ITR_BASE_URL,
    "DFP": CVM_DFP_BASE_URL,
}
_DOCUMENT_PREFIX: dict[str, str] = {
    "ITR": "itr_cia_aberta",
    "DFP": "dfp_cia_aberta",
}

# The modules we mirror, in balance-sheet-then-statement order.
_MODULES: tuple[str, ...] = ("BPA", "BPP", "DRE", "DFC")

# Substring in a member's file name -> the module it carries. The cash flow ships
# as two members (indirect / direct method); a filer uses one, both fold to DFC.
_MEMBER_MODULE: dict[str, str] = {
    "_BPA_": "BPA",  # balance sheet — assets
    "_BPP_": "BPP",  # balance sheet — liabilities + equity
    "_DRE_": "DRE",  # income statement
    "_DFC_MI_": "DFC",  # cash flow, indirect method
    "_DFC_MD_": "DFC",  # cash flow, direct method
}

# ESCALA_MOEDA -> the multiplier ``mongo_fundamentals`` scales figures by.
_CURRENCY_SIZE: dict[str, int] = {"MIL": 1000, "UNIDADE": 1}

# CVM open datasets are latin-1, semicolon-separated (like the FRE in cvm_capital).
_ENCODING = "latin-1"
_DELIMITER = ";"

# ORDEM_EXERC of the period being reported (vs. PENÚLTIMO, the comparative), folded.
_LAST_PERIOD = "ULTIMO"


def _fold(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).upper()


def _currency(moeda: str | None) -> str | None:
    if not moeda:
        return None
    return "BRL" if moeda.strip().upper() == "REAL" else moeda.strip()


def _classify(member: str) -> tuple[str, str] | None:
    """``(module, balance_type)`` for a statement CSV, or ``None`` to skip it."""
    if "_con_" in member:
        balance_type = "consolidated"
    elif "_ind_" in member:
        balance_type = "individual"
    else:
        return None
    for token, module in _MEMBER_MODULE.items():
        if token in member:
            return module, balance_type
    return None


def _account(row: Mapping[str, str]) -> dict[str, Any]:
    """One raw account line, mirrored as filed (Any: the untyped CVM payload)."""
    code = row.get("CD_CONTA", "")
    return {
        "code": code,
        "name": row.get("DS_CONTA", ""),
        "quantity": row.get("VL_CONTA", ""),
        "level": code.count(".") + 1 if code else None,
        "is_fixed": (row.get("ST_CONTA_FIXA") or "").strip().upper() == "S",
    }


@dataclass
class _Statement:
    """One statement (module) for one filed period, as read from its CSV member."""

    balance_type: str
    company_name: str
    currency: str | None
    currency_size: int | None
    period_start: str | None
    period_end: str | None
    accounts: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class _Document:
    """The statements kept for one ``(cvm_code, reference_date)`` filing period."""

    cvm_code: str
    reference_date: str
    statements: dict[str, _Statement]


class CvmDataSource:
    """Fetch one statement (module) for one ticker from CVM open data."""

    def __init__(
        self,
        http_client: httpx.AsyncClient,
        ticker_to_code: Mapping[str, str],
        *,
        year: int,
        cache_dir: str,
        document: CvmDocument = "ITR",
        base_url: str | None = None,
        sleep: Sleeper = asyncio.sleep,
    ) -> None:
        self._http = http_client
        self._ticker_to_code = dict(ticker_to_code)
        self._year = year
        self._cache_dir = Path(cache_dir)
        self._document = document
        self._prefix = _DOCUMENT_PREFIX[document]
        self._base_url = (base_url or _DOCUMENT_BASE_URL[document]).rstrip("/")
        self._sleep = sleep
        self._index: dict[str, list[_Document]] | None = None
        self._lock = asyncio.Lock()

    @property
    def _zip_name(self) -> str:
        return f"{self._prefix}_{self._year}.zip"

    async def fetch(self, ticker: str, module: str) -> Sequence[RawFetchResult]:
        """Return the raw statement for ``ticker``/``module`` (BPA/BPP/DRE/DFC).

        One result per filed period: the ITR file carries Q1/Q2/Q3, so a normal
        year yields three results here (the DFP file yields one). Periods where
        this module is absent are skipped; if none carry it, we raise 404.
        """
        index = await self._ensure_loaded()

        code = self._ticker_to_code.get(ticker)
        if code is None:
            raise BrapiNotFoundError(f"no CVM code mapped for {ticker}")
        docs = index.get(code)
        if not docs:
            raise BrapiNotFoundError(
                f"no CVM {self._year} filing for {ticker} ({code})"
            )

        results: list[RawFetchResult] = []
        for doc in docs:
            statement = doc.statements.get(module.upper())
            if statement is None or not statement.accounts:
                continue
            results.append(
                RawFetchResult(
                    module=module,
                    request={
                        "source": "cvm",
                        "file": self._zip_name,
                        "cvm_code": code,
                        "statement": module,
                        "balance_type": statement.balance_type,
                        "reference_date": doc.reference_date,
                    },
                    http_status=200,
                    payload=self._to_payload(doc, module, statement),
                )
            )
        if not results:
            raise BrapiNotFoundError(f"no {module} for {ticker} ({code})")
        return results

    async def _ensure_loaded(self) -> dict[str, list[_Document]]:
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
                "Loaded CVM %s %s: %d of %d portfolio companies found (%d periods)",
                self._document,
                self._year,
                len(index),
                len(set(self._ticker_to_code.values())),
                sum(len(docs) for docs in index.values()),
            )
            return index

    async def _download(self, dst: Path) -> None:
        """Fetch the yearly ZIP with retry + atomic write (see ``download_zip``).

        A definitive failure raises ``CvmDownloadError``; the use case treats
        it as fatal for the run, since every ticker of the year shares this
        file.
        """
        url = f"{self._base_url}/{self._zip_name}"
        logger.info("Downloading CVM %s %s from %s", self._document, self._year, url)
        await download_zip(self._http, url, dst, sleep=self._sleep)

    def _build_index(self, archive_path: Path) -> dict[str, list[_Document]]:
        """Index every filed period per wanted CVM code (sync; runs in a thread).

        Reads each statement member directly, keeping the current period's rows
        (``ORDEM_EXERC`` = ÚLTIMO) per ``(code, reference_date, version, module,
        balance_type)``, then reduces to one document per period — see ``_reduce``.
        """
        wanted = set(self._ticker_to_code.values())
        accumulated: dict[tuple[str, str, int, str, str], _Statement] = {}
        with zipfile.ZipFile(archive_path) as archive:
            for member in archive.namelist():
                classified = _classify(member)
                if classified is None:
                    continue
                module, balance_type = classified
                self._read_member(
                    archive, member, module, balance_type, wanted, accumulated
                )
        return _reduce(accumulated)

    @staticmethod
    def _read_member(
        archive: zipfile.ZipFile,
        member: str,
        module: str,
        balance_type: str,
        wanted: set[str],
        accumulated: dict[tuple[str, str, int, str, str], _Statement],
    ) -> None:
        with archive.open(member) as raw:
            reader = csv.DictReader(
                io.TextIOWrapper(raw, encoding=_ENCODING), delimiter=_DELIMITER
            )
            for row in reader:
                code = row.get("CD_CVM", "").lstrip("0")
                if code not in wanted:
                    continue
                if _fold(row.get("ORDEM_EXERC", "")).strip() != _LAST_PERIOD:
                    continue
                try:
                    version = int(row.get("VERSAO", ""))
                except ValueError:
                    continue
                key = (code, row.get("DT_REFER", ""), version, module, balance_type)
                statement = accumulated.get(key)
                if statement is None:
                    statement = _Statement(
                        balance_type=balance_type,
                        company_name=row.get("DENOM_CIA", ""),
                        currency=_currency(row.get("MOEDA")),
                        currency_size=_CURRENCY_SIZE.get(
                            (row.get("ESCALA_MOEDA") or "").strip().upper()
                        ),
                        period_start=row.get("DT_INI_EXERC") or None,
                        period_end=row.get("DT_FIM_EXERC") or None,
                    )
                    accumulated[key] = statement
                statement.accounts.append(_account(row))

    def _to_payload(
        self, doc: _Document, module: str, statement: _Statement
    ) -> dict[str, Any]:
        return {
            "cvm_code": doc.cvm_code,
            "company_name": statement.company_name,
            "document_type": self._document,
            "reference_date": doc.reference_date,
            "statement": module,
            "balance_type": statement.balance_type,
            "currency": statement.currency,
            "currency_size": statement.currency_size,
            "period_start_date": statement.period_start,
            "period_end_date": statement.period_end,
            "accounts": statement.accounts,
        }


def _reduce(
    accumulated: dict[tuple[str, str, int, str, str], _Statement],
) -> dict[str, list[_Document]]:
    """Collapse the accumulated rows to one document per ``(code, reference_date)``.

    Keeps the amendment (highest ``VERSAO``) and, within it, prefers each module's
    consolidated statement over its individual one. A period with no statement at
    the chosen version is dropped.
    """
    periods = {(code, ref) for (code, ref, *_rest) in accumulated}
    max_version: dict[tuple[str, str], int] = {}
    for code, ref, version, *_rest in accumulated:
        key = (code, ref)
        if version > max_version.get(key, -1):
            max_version[key] = version

    index: dict[str, list[_Document]] = {}
    for code, ref in sorted(periods):
        version = max_version[(code, ref)]
        statements: dict[str, _Statement] = {}
        for module in _MODULES:
            statement = accumulated.get(
                (code, ref, version, module, "consolidated")
            ) or accumulated.get((code, ref, version, module, "individual"))
            if statement is not None:
                statements[module] = statement
        if statements:
            index.setdefault(code, []).append(_Document(code, ref, statements))
    return index
