"""Ticker -> CVM registrant resolution from CVM's FCA archive.

The statements (``CD_CVM``) and the FRE (``CNPJ``) are both keyed by registrant,
never by the B3 ticker — so before either source can run for an arbitrary ticker,
the ticker has to be resolved to those keys. That link lives in the CVM's
*Formulário Cadastral* (FCA), one yearly ZIP with two members that join on CNPJ:

  * ``fca_cia_aberta_valor_mobiliario`` — the securities each company has listed,
    carrying ``Codigo_Negociacao`` (the B3 ticker) and ``CNPJ_Companhia``.
  * ``fca_cia_aberta_geral`` — the general cadastre, carrying ``Codigo_CVM``,
    ``Setor_Atividade`` and ``Situacao_Registro_CVM``, also keyed by CNPJ.

So ``ticker -> CNPJ`` (securities) joined with ``CNPJ -> CD_CVM`` (general) gives
the full identity. This replaces the hand-curated ``cvm_codes.py`` maps for any
ticker outside the nine, and scales to the whole exchange (the batch-ingestion
slice of M2 reuses the same index).

Follows the same download-once / cache / read-in-a-thread shape as
``CvmDataSource``; the FCA CSVs are latin-1, semicolon-separated like every CVM
open dataset.
"""

from __future__ import annotations

import asyncio
import csv
import io
import zipfile
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import httpx

from smaug.ingestion.infrastructure.download import Sleeper, download_zip
from smaug.portfolio.domain.company import CompanyIdentity
from smaug.shared.logging import get_logger

logger = get_logger(__name__)

CVM_FCA_BASE_URL = "https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/FCA/DADOS"

_ENCODING = "latin-1"
_DELIMITER = ";"


@dataclass
class _Cadastre:
    """The general-cadastre facts for one company, keyed by CNPJ."""

    cd_cvm: str
    denom: str
    cvm_sector: str
    situation: str
    version: int


@dataclass
class _Security:
    """A listed security's CNPJ + whether it is still trading, for one ticker."""

    cnpj: str
    trading: bool
    version: int


class CvmCompanyRegistry:
    """Resolve B3 tickers to CVM identities from the yearly FCA archive."""

    def __init__(
        self,
        http_client: httpx.AsyncClient,
        *,
        year: int,
        cache_dir: str,
        base_url: str | None = None,
        sleep: Sleeper = asyncio.sleep,
    ) -> None:
        self._http = http_client
        self._year = year
        self._cache_dir = Path(cache_dir)
        self._base_url = (base_url or CVM_FCA_BASE_URL).rstrip("/")
        self._sleep = sleep
        self._index: dict[str, CompanyIdentity] | None = None
        self._lock = asyncio.Lock()

    @property
    def _zip_name(self) -> str:
        return f"fca_cia_aberta_{self._year}.zip"

    @property
    def _geral_member(self) -> str:
        return f"fca_cia_aberta_geral_{self._year}.csv"

    @property
    def _securities_member(self) -> str:
        return f"fca_cia_aberta_valor_mobiliario_{self._year}.csv"

    async def resolve(self, ticker: str) -> CompanyIdentity | None:
        index = await self._ensure_loaded()
        return index.get(ticker.upper().strip())

    async def resolve_all(self, tickers: Iterable[str]) -> dict[str, CompanyIdentity]:
        index = await self._ensure_loaded()
        resolved: dict[str, CompanyIdentity] = {}
        for ticker in tickers:
            identity = index.get(ticker.upper().strip())
            if identity is not None:
                resolved[ticker] = identity
        return resolved

    async def _ensure_loaded(self) -> dict[str, CompanyIdentity]:
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
                "Loaded CVM FCA %s registry: %d tradable tickers",
                self._year,
                len(index),
            )
            return index

    async def _download(self, dst: Path) -> None:
        url = f"{self._base_url}/{self._zip_name}"
        logger.info("Downloading CVM FCA %s from %s", self._year, url)
        await download_zip(
            self._http, url, dst, follow_redirects=True, sleep=self._sleep
        )

    def _build_index(self, archive_path: Path) -> dict[str, CompanyIdentity]:
        """Join securities (ticker->CNPJ) with the cadastre (CNPJ->CD_CVM)."""
        with zipfile.ZipFile(archive_path) as archive:
            cadastre = self._read_cadastre(archive)
            securities = self._read_securities(archive)

        index: dict[str, CompanyIdentity] = {}
        for ticker, security in securities.items():
            company = cadastre.get(security.cnpj)
            if company is None:  # a ticker whose company has no cadastre row
                continue
            index[ticker] = CompanyIdentity(
                ticker=ticker,
                cd_cvm=company.cd_cvm,
                cnpj=security.cnpj,
                denom=company.denom,
                cvm_sector=company.cvm_sector,
                situation=company.situation,
            )
        return index

    def _read_cadastre(self, archive: zipfile.ZipFile) -> dict[str, _Cadastre]:
        """CNPJ -> cadastre facts, keeping the highest-version row per company."""
        cadastre: dict[str, _Cadastre] = {}
        with archive.open(self._geral_member) as member:
            reader = csv.DictReader(
                io.TextIOWrapper(member, encoding=_ENCODING), delimiter=_DELIMITER
            )
            for row in reader:
                cnpj = (row.get("CNPJ_Companhia") or "").strip()
                cd_cvm = (row.get("Codigo_CVM") or "").strip().lstrip("0")
                if not cnpj or not cd_cvm:
                    continue
                version = _int(row.get("Versao"))
                current = cadastre.get(cnpj)
                if current is not None and current.version >= version:
                    continue
                cadastre[cnpj] = _Cadastre(
                    cd_cvm=cd_cvm,
                    denom=(row.get("Nome_Empresarial") or "").strip(),
                    cvm_sector=(row.get("Setor_Atividade") or "").strip(),
                    situation=(row.get("Situacao_Registro_CVM") or "").strip(),
                    version=version,
                )
        return cadastre

    def _read_securities(self, archive: zipfile.ZipFile) -> dict[str, _Security]:
        """Ticker -> CNPJ, preferring a still-trading, highest-version listing."""
        securities: dict[str, _Security] = {}
        with archive.open(self._securities_member) as member:
            reader = csv.DictReader(
                io.TextIOWrapper(member, encoding=_ENCODING), delimiter=_DELIMITER
            )
            for row in reader:
                ticker = (row.get("Codigo_Negociacao") or "").strip().upper()
                cnpj = (row.get("CNPJ_Companhia") or "").strip()
                if not ticker or not cnpj:
                    continue
                candidate = _Security(
                    cnpj=cnpj,
                    trading=not (row.get("Data_Fim_Negociacao") or "").strip(),
                    version=_int(row.get("Versao")),
                )
                current = securities.get(ticker)
                if current is None or _prefer(candidate, current):
                    securities[ticker] = candidate
        return securities


def _int(value: str | None) -> int:
    try:
        return int(value) if value else 0
    except ValueError:
        return 0


def _prefer(candidate: _Security, current: _Security) -> bool:
    """A still-trading listing wins; then the higher document version."""
    if candidate.trading != current.trading:
        return candidate.trading
    return candidate.version > current.version
