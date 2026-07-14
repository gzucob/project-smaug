"""CVM raw data source — parses dados.cvm.gov.br into the ``RawDataSource`` port.

Unlike brapi (one HTTP call per ticker/module), CVM ships one yearly ZIP with
*every* company. So this source downloads that ZIP once, caches it, reads the
statement CSVs it contains, and serves each ticker/statement from the in-memory
index.

**The mirror stores everything and chooses nothing** (ADR 0016). It used to
decide, at ingestion time, which version superseded which, that the consolidated
statement beat the individual one, and that the comparative column was not worth
keeping — and each of those choices destroyed data that cannot be recovered
without re-ingesting. So one document is emitted per
``(reference_date, version, balance_type, ordem_exerc)``, and the *reader*
(``analysis/infrastructure/mongo_fundamentals.py``) makes the selection, where it
can be revised without another download.

The statement CSVs are read directly (ADR 0009), not through pycvm: pycvm's
reader crashed on the real DMPL, and — worse — its parallel batch reader
desynchronised on a duplicated head row and silently dropped whole consolidated
collections (#55), so we mirrored parent-only statements without noticing.

Three real-world quirks are handled here:
  * CVM is keyed by ``CD_CVM``, not by B3 ticker — hence the injected
    ticker -> code map (see ``portfolio.domain.cvm_codes``).
  * The **DMPL is a matrix**, not a list: its rows carry an extra ``COLUNA_DF``
    (which equity column the figure belongs to — capital, reserves, retained
    earnings, non-controlling interest). Two rows share a ``CD_CONTA`` and differ
    only by that column, so dropping it would silently collapse them.
  * The comparative column (``ORDEM_EXERC`` = PENÚLTIMO) describes the *prior*
    period, and carries its own ``DT_INI/FIM_EXERC`` — it is a period in its own
    right, not a duplicate of the current one.
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

# Every statement member the ZIP carries. BPA/BPP/DRE/DFC are what the indicators
# read today; DMPL/DVA/DRA are mirrored because the mirror is not the place to
# decide what will be useful later (ADR 0016) — the DMPL in particular carries the
# controllers/minority attribution the DRE sometimes files blank (#78).
_MODULES: tuple[str, ...] = ("BPA", "BPP", "DRE", "DFC", "DMPL", "DVA", "DRA")

# Substring in a member's file name -> the module it carries. The cash flow ships
# as two members (indirect / direct method); a filer uses one, both fold to DFC.
_MEMBER_MODULE: dict[str, str] = {
    "_BPA_": "BPA",  # balance sheet — assets
    "_BPP_": "BPP",  # balance sheet — liabilities + equity
    "_DRE_": "DRE",  # income statement
    "_DFC_MI_": "DFC",  # cash flow, indirect method
    "_DFC_MD_": "DFC",  # cash flow, direct method
    "_DMPL_": "DMPL",  # changes in equity (a matrix — see ``COLUNA_DF``)
    "_DVA_": "DVA",  # value added
    "_DRA_": "DRA",  # comprehensive income
}

# ESCALA_MOEDA -> the multiplier ``mongo_fundamentals`` scales figures by.
_CURRENCY_SIZE: dict[str, int] = {"MIL": 1000, "UNIDADE": 1}

# CVM open datasets are latin-1, semicolon-separated (like the FRE in cvm_capital).
_ENCODING = "latin-1"
_DELIMITER = ";"


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
    """One raw account line, mirrored as filed (Any: the untyped CVM payload).

    ``column`` is the DMPL's ``COLUNA_DF`` — which equity column the figure belongs
    to. Only the DMPL has it, and there it is load-bearing: its rows are a matrix,
    so two of them share a ``CD_CONTA`` and are told apart by this alone.
    """
    code = row.get("CD_CONTA", "")
    account: dict[str, Any] = {
        "code": code,
        "name": row.get("DS_CONTA", ""),
        "quantity": row.get("VL_CONTA", ""),
        "level": code.count(".") + 1 if code else None,
        "is_fixed": (row.get("ST_CONTA_FIXA") or "").strip().upper() == "S",
    }
    column = row.get("COLUNA_DF")
    if column:
        account["column"] = column
    return account


# One statement is identified by all five of these. Collapsing any of them is what
# ADR 0016 stopped doing: they are different filings, not duplicates.
_Key = tuple[str, str, int, str, str, str]  # code, ref, version, module, type, ordem


@dataclass
class _Statement:
    """One statement (module) for one filed period, version, balance and column."""

    module: str
    reference_date: str
    version: int
    balance_type: str
    ordem_exerc: str  # ULTIMO (the reported period) or PENULTIMO (the comparative)
    company_name: str
    currency: str | None
    currency_size: int | None
    period_start: str | None
    period_end: str | None
    accounts: list[dict[str, Any]] = field(default_factory=list)


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
        self._index: dict[str, list[_Statement]] | None = None
        self._lock = asyncio.Lock()

    @property
    def _zip_name(self) -> str:
        return f"{self._prefix}_{self._year}.zip"

    async def fetch(self, ticker: str, module: str) -> Sequence[RawFetchResult]:
        """Return every raw statement filed for ``ticker``/``module``.

        One result per ``(reference_date, version, balance_type, ordem_exerc)`` —
        the mirror keeps all of them (ADR 0016). So an ITR year yields Q1/Q2/Q3,
        each in both balance types, once per amendment, with its comparative
        alongside. 404 only when the filer has no such statement at all.
        """
        index = await self._ensure_loaded()

        code = self._ticker_to_code.get(ticker)
        if code is None:
            raise BrapiNotFoundError(f"no CVM code mapped for {ticker}")
        statements = index.get(code)
        if not statements:
            raise BrapiNotFoundError(
                f"no CVM {self._year} filing for {ticker} ({code})"
            )

        wanted = module.upper()
        results = [
            RawFetchResult(
                module=module,
                request={
                    "source": "cvm",
                    "file": self._zip_name,
                    "cvm_code": code,
                    "statement": module,
                    "balance_type": statement.balance_type,
                    "reference_date": statement.reference_date,
                    "version": statement.version,
                    "ordem_exerc": statement.ordem_exerc,
                },
                http_status=200,
                payload=self._to_payload(code, statement),
            )
            for statement in statements
            if statement.module == wanted and statement.accounts
        ]
        if not results:
            raise BrapiNotFoundError(f"no {module} for {ticker} ({code})")
        return results

    async def _ensure_loaded(self) -> dict[str, list[_Statement]]:
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

    def _build_index(self, archive_path: Path) -> dict[str, list[_Statement]]:
        """Index every statement filed by a wanted CVM code (sync; runs in a thread).

        Nothing is selected or collapsed here (ADR 0016) — every
        ``(reference_date, version, balance_type, ordem_exerc)`` the ZIP carries is
        kept, and the reader decides which of them it wants.
        """
        wanted = set(self._ticker_to_code.values())
        accumulated: dict[_Key, _Statement] = {}
        with zipfile.ZipFile(archive_path) as archive:
            for member in archive.namelist():
                classified = _classify(member)
                if classified is None:
                    continue
                module, balance_type = classified
                _read_member(archive, member, module, balance_type, wanted, accumulated)

        index: dict[str, list[_Statement]] = {}
        for (code, *_rest), statement in sorted(accumulated.items()):
            index.setdefault(code, []).append(statement)
        return index

    def _to_payload(self, cvm_code: str, statement: _Statement) -> dict[str, Any]:
        return {
            "cvm_code": cvm_code,
            "company_name": statement.company_name,
            "document_type": self._document,
            "reference_date": statement.reference_date,
            "statement": statement.module,
            "balance_type": statement.balance_type,
            "version": statement.version,
            "ordem_exerc": statement.ordem_exerc,
            "currency": statement.currency,
            "currency_size": statement.currency_size,
            "period_start_date": statement.period_start,
            "period_end_date": statement.period_end,
            "accounts": statement.accounts,
        }


def _read_member(
    archive: zipfile.ZipFile,
    member: str,
    module: str,
    balance_type: str,
    wanted: set[str],
    accumulated: dict[_Key, _Statement],
) -> None:
    """Read one statement CSV, accumulating every row a wanted company filed."""
    with archive.open(member) as raw:
        reader = csv.DictReader(
            io.TextIOWrapper(raw, encoding=_ENCODING), delimiter=_DELIMITER
        )
        for row in reader:
            code = row.get("CD_CVM", "").lstrip("0")
            if code not in wanted:
                continue
            try:
                version = int(row.get("VERSAO", ""))
            except ValueError:
                continue
            ordem = _fold(row.get("ORDEM_EXERC", "")).strip()
            reference_date = row.get("DT_REFER", "")
            key = (code, reference_date, version, module, balance_type, ordem)
            statement = accumulated.get(key)
            if statement is None:
                statement = _Statement(
                    module=module,
                    reference_date=reference_date,
                    version=version,
                    balance_type=balance_type,
                    ordem_exerc=ordem,
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
