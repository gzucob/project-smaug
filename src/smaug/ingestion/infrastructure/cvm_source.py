"""CVM raw data source — parses dados.cvm.gov.br into the ``RawDataSource`` port.

Unlike brapi (one HTTP call per ticker/module), CVM ships one yearly ZIP with
*every* company. So this source downloads that ZIP once, caches it, parses it
with pycvm, and then serves each ticker/statement from the in-memory index.
It stays a faithful mirror: it stores the raw statement accounts (code, name,
value) exactly as filed — no indicators, no math (that is Phase 2).

Two real-world quirks are handled here:
  * CVM is keyed by ``CD_CVM``, not by B3 ticker — hence the injected
    ticker -> code map (see ``portfolio.domain.cvm_codes``).
  * pycvm's DMPL parser crashes on the real 2024 ITR
    (``KeyError: 'Patrimônio Líquido'``). We do not need DMPL, but the parser
    walks the whole file, so we neutralise the DMPL members (header only)
    before parsing — ``_sanitize_dmpl``.
"""

from __future__ import annotations

import asyncio
import zipfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

import httpx

from smaug.ingestion.domain.ports import RawFetchResult
from smaug.shared.errors import BrapiNotFoundError
from smaug.shared.logging import get_logger

logger = get_logger(__name__)

CVM_ITR_BASE_URL = "https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/ITR/DADOS"
CVM_DFP_BASE_URL = "https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/DFP/DADOS"

# document kind -> (base URL, file-name prefix). ITR = quarterly (YTD periods),
# DFP = annual closed year (single 12-month period). pycvm's DFPITRFile parses
# both; only the URL and file name differ.
CvmDocument = Literal["ITR", "DFP"]
_DOCUMENT_BASE_URL: dict[str, str] = {
    "ITR": CVM_ITR_BASE_URL,
    "DFP": CVM_DFP_BASE_URL,
}
_DOCUMENT_PREFIX: dict[str, str] = {
    "ITR": "itr_cia_aberta",
    "DFP": "dfp_cia_aberta",
}

# module (config) -> attribute on pycvm's StatementCollection.
_MODULE_TO_ATTR: dict[str, str] = {
    "BPA": "bpa",  # balance sheet — assets
    "BPP": "bpp",  # balance sheet — liabilities + equity
    "DRE": "dre",  # income statement
    "DFC": "dfc",  # cash flow
    "DMPL": "dmpl",
    "DVA": "dva",
}


def _sanitize_dmpl(src: Path, dst: Path) -> None:
    """Copy ``src`` to ``dst`` with every DMPL member reduced to its header.

    pycvm insists on parsing DMPL and blows up on the real file; emptying the
    DMPL CSVs (keeping a valid header row) lets the whole file parse.
    """
    with (
        zipfile.ZipFile(src) as zin,
        zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zout,
    ):
        for info in zin.infolist():
            data = zin.read(info.filename)
            if "_DMPL_" in info.filename.upper():
                header = data.split(b"\n", 1)[0]
                data = header + b"\n"
            zout.writestr(info, data)


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
    ) -> None:
        self._http = http_client
        self._ticker_to_code = dict(ticker_to_code)
        self._year = year
        self._cache_dir = Path(cache_dir)
        self._document = document
        self._prefix = _DOCUMENT_PREFIX[document]
        self._base_url = (base_url or _DOCUMENT_BASE_URL[document]).rstrip("/")
        self._index: dict[str, Any] | None = None
        self._lock = asyncio.Lock()

    @property
    def _zip_name(self) -> str:
        return f"{self._prefix}_{self._year}.zip"

    async def fetch(self, ticker: str, module: str) -> RawFetchResult:
        """Return the raw statement for ``ticker``/``module`` (BPA/BPP/DRE/DFC)."""
        index = await self._ensure_loaded()

        code = self._ticker_to_code.get(ticker)
        if code is None:
            raise BrapiNotFoundError(f"no CVM code mapped for {ticker}")
        doc = index.get(code)
        if doc is None:
            raise BrapiNotFoundError(
                f"no CVM {self._year} filing for {ticker} ({code})"
            )

        balance_type, collection = self._pick_collection(doc)
        if collection is None:
            raise BrapiNotFoundError(f"no statements for {ticker} ({code})")
        statement = self._statement_for(collection, module)
        if statement is None or not getattr(statement, "accounts", None):
            raise BrapiNotFoundError(f"no {module} for {ticker} ({code})")

        payload = self._to_payload(doc, module, balance_type, statement)
        return RawFetchResult(
            module=module,
            request={
                "source": "cvm",
                "file": self._zip_name,
                "cvm_code": code,
                "statement": module,
                "balance_type": balance_type,
            },
            http_status=200,
            payload=payload,
        )

    async def _ensure_loaded(self) -> dict[str, Any]:
        cached = self._index
        if cached is not None:
            return cached
        async with self._lock:
            cached = self._index
            if cached is not None:
                return cached
            self._cache_dir.mkdir(parents=True, exist_ok=True)
            raw = self._cache_dir / self._zip_name
            sanitized = self._cache_dir / f"{self._prefix}_{self._year}.sanitized.zip"
            if not raw.exists():
                await self._download(raw)
            if not sanitized.exists():
                await asyncio.to_thread(_sanitize_dmpl, raw, sanitized)
            index = await asyncio.to_thread(self._build_index, sanitized)
            self._index = index
            logger.info(
                "Loaded CVM %s %s: %d of %d portfolio companies found",
                self._document,
                self._year,
                len(index),
                len(set(self._ticker_to_code.values())),
            )
            return index

    async def _download(self, dst: Path) -> None:
        url = f"{self._base_url}/{self._zip_name}"
        logger.info("Downloading CVM %s %s from %s", self._document, self._year, url)
        response = await self._http.get(url, timeout=180.0)
        response.raise_for_status()
        dst.write_bytes(response.content)

    def _build_index(self, sanitized: Path) -> dict[str, Any]:
        """Index the latest filing per wanted CVM code (sync; runs in a thread)."""
        from cvm import DFPITRFile

        wanted = set(self._ticker_to_code.values())
        index: dict[str, Any] = {}
        for doc in DFPITRFile(str(sanitized)):
            if doc.cvm_code not in wanted:
                continue
            current = index.get(doc.cvm_code)
            if current is None or doc.reference_date > current.reference_date:
                index[doc.cvm_code] = doc
        return index

    @staticmethod
    def _pick_collection(doc: Any) -> tuple[str, Any]:
        """Prefer the consolidated statements, fall back to individual."""
        if doc.consolidated is not None:
            return "consolidated", doc.consolidated.last
        if doc.individual is not None:
            return "individual", doc.individual.last
        return "none", None

    @staticmethod
    def _statement_for(collection: Any, module: str) -> Any:
        attr = _MODULE_TO_ATTR.get(module.upper())
        if attr is None:
            return None
        return getattr(collection, attr, None)

    @staticmethod
    def _to_payload(
        doc: Any, module: str, balance_type: str, statement: Any
    ) -> dict[str, Any]:
        period_start = getattr(statement, "period_start_date", None)
        period_end = getattr(statement, "period_end_date", None)
        currency = getattr(statement, "currency", None)
        return {
            "cvm_code": str(doc.cvm_code),
            "company_name": str(doc.company_name),
            "document_type": getattr(doc.type, "name", str(doc.type)),
            "reference_date": doc.reference_date.isoformat(),
            "statement": module,
            "balance_type": balance_type,
            "currency": None if currency is None else str(currency),
            "currency_size": getattr(statement, "currency_size", None),
            "period_start_date": (
                None if period_start is None else period_start.isoformat()
            ),
            "period_end_date": None if period_end is None else period_end.isoformat(),
            "accounts": [
                {
                    "code": str(a.code),
                    "name": str(a.name),
                    "quantity": str(a.quantity),
                    "level": getattr(a, "level", None),
                    "is_fixed": getattr(a, "is_fixed", None),
                }
                for a in statement.accounts
            ],
        }
