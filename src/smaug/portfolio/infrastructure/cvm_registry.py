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
the full identity — resolved this way for every ticker, no hand-picked shortcut
(#212), and it scales to the whole exchange (the batch-ingestion slice of M2
reuses the same index).

Follows the same download-once / cache / read-in-a-thread shape as
``CvmDataSource``; the FCA CSVs are latin-1, semicolon-separated like every CVM
open dataset.
"""

from __future__ import annotations

import asyncio
import csv
import io
import re
import unicodedata
import zipfile
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import httpx

from smaug.portfolio.domain.company import CompanyIdentity
from smaug.portfolio.domain.share_classes import ShareClass, ShareKind
from smaug.portfolio.domain.universe import ListedCompany, listed_companies
from smaug.shared.download import Sleeper, download_zip
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
    listed_since: date | None = None
    # Underlying shares this ticker's own row bundles, when the row is a unit
    # ("Units ..."); ``None`` for a plain ON/PN class.
    unit_shares: int | None = None


@dataclass
class _ClassAccumulator:
    """The ON/PN trading symbols a company lists, gathered per kind."""

    common: set[str] = field(default_factory=set)
    preferred: set[str] = field(default_factory=set)

    def add(self, kind: ShareKind, symbol: str) -> None:
        (self.common if kind is ShareKind.COMMON else self.preferred).add(symbol)


def _fold(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).lower()


def _share_kind(valor_mobiliario: str) -> ShareKind | None:
    """ON/PN from an FCA ``Valor_Mobiliario`` label; ``None`` for units, BDRs, etc."""
    label = _fold(valor_mobiliario).strip()
    if label.startswith("acoes ordinarias"):
        return ShareKind.COMMON
    if label.startswith("acoes preferenciais"):
        return ShareKind.PREFERRED
    return None


def _is_unit(valor_mobiliario: str) -> bool:
    return _fold(valor_mobiliario).strip().startswith("units")


_UNIT_COMPONENT_RE = re.compile(r"(\d+)\s*([A-Z]{4}\d{1,2})")


def _kind_from_suffix(symbol: str) -> ShareKind | None:
    """ON/PN from a B3 ticker suffix (3 = ON, 4/5/6 = PN); ``None`` for a unit."""
    suffix = symbol[4:]  # B3 roots are four letters; the rest is the class number
    if suffix == "3":
        return ShareKind.COMMON
    if suffix in ("4", "5", "6"):
        return ShareKind.PREFERRED
    return None


def _unit_composition(composition: str) -> list[tuple[int, str, ShareKind]]:
    """Quantity + underlying ON/PN classes named in a unit's ``Composicao_BDR_Unit``.

    Some companies file only the unit on the FCA (Klabin lists KLBN11, never
    KLBN3/KLBN4), but the unit row spells its bundle out — e.g. "1 KLBN3 +
    4 KLBN4". Parse the leading quantity alongside each class ticker, keyed by
    suffix: the quantities summed are how many underlying shares one unit is
    worth (#212), which the FRE itself never publishes.
    """
    resolved: list[tuple[int, str, ShareKind]] = []
    for qty, symbol in _UNIT_COMPONENT_RE.findall(composition.upper()):
        kind = _kind_from_suffix(symbol)
        if kind is not None:
            resolved.append((int(qty), symbol, kind))
    return resolved


def _resolve_classes(
    accumulator: _ClassAccumulator,
) -> tuple[ShareClass, ...]:
    """The company's ON/PN classes, ordered ON→PN.

    Only when there is at most one class per kind: the market cap sums each class
    at its own price times the **per-kind** filed count (ADR 0014), so a second
    class of the same kind would multiply that whole count twice. Rather than a
    wrong cap, an ambiguous company yields no classes (the cap stays a named null).
    """
    if len(accumulator.common) > 1 or len(accumulator.preferred) > 1:
        return ()
    classes: list[ShareClass] = []
    for symbol in accumulator.common:
        classes.append(ShareClass(symbol=symbol, kind=ShareKind.COMMON))
    for symbol in accumulator.preferred:
        classes.append(ShareClass(symbol=symbol, kind=ShareKind.PREFERRED))
    return tuple(classes)


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

    async def companies(self) -> tuple[ListedCompany, ...]:
        """Every listed company in the archive, grouped from its trading codes.

        The index is keyed by ticker because that is what a caller resolves; a
        batch wants the other direction, and the grouping is where the archive's
        non-tickers are dropped (see ``universe``).
        """
        index = await self._ensure_loaded()
        return listed_companies(index.values())

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
            securities, class_accumulators = self._read_securities(archive)

        classes = {
            cnpj: _resolve_classes(acc) for cnpj, acc in class_accumulators.items()
        }
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
                listed_since=security.listed_since,
                share_classes=classes.get(security.cnpj, ()),
                shares_per_unit=security.unit_shares,
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

    def _read_securities(
        self, archive: zipfile.ZipFile
    ) -> tuple[dict[str, _Security], dict[str, _ClassAccumulator]]:
        """Ticker -> CNPJ (best listing), plus the ON/PN classes gathered per CNPJ.

        A single pass: the ticker map keys off ``Codigo_Negociacao``; the class
        accumulator gathers the company's trading ON/PN symbols (units and BDRs
        are skipped by ``_share_kind``) so the cap knows what to price (ADR 0014).
        A unit row also carries its own bundle ratio (``unit_shares``), parsed
        from the same ``Composicao_BDR_Unit`` text (#212).
        """
        securities: dict[str, _Security] = {}
        classes: dict[str, _ClassAccumulator] = {}
        with archive.open(self._securities_member) as member:
            reader = csv.DictReader(
                io.TextIOWrapper(member, encoding=_ENCODING), delimiter=_DELIMITER
            )
            for row in reader:
                ticker = (row.get("Codigo_Negociacao") or "").strip().upper()
                cnpj = (row.get("CNPJ_Companhia") or "").strip()
                if not ticker or not cnpj:
                    continue
                trading = not (row.get("Data_Fim_Negociacao") or "").strip()
                valor = row.get("Valor_Mobiliario") or ""
                kind = _share_kind(valor)
                composition = (
                    _unit_composition(row.get("Composicao_BDR_Unit") or "")
                    if kind is None and _is_unit(valor)
                    else ()
                )
                unit_shares = sum(qty for qty, _, _ in composition) or None
                candidate = _Security(
                    cnpj=cnpj,
                    trading=trading,
                    version=_int(row.get("Versao")),
                    listed_since=_iso_date(row.get("Data_Inicio_Listagem")),
                    unit_shares=unit_shares,
                )
                current = securities.get(ticker)
                if current is None or _prefer(candidate, current):
                    securities[ticker] = candidate

                if not trading:
                    continue
                if kind is not None:
                    classes.setdefault(cnpj, _ClassAccumulator()).add(kind, ticker)
                elif composition:
                    # A unit-only filer (Klabin) names its classes in the bundle.
                    accumulator = classes.setdefault(cnpj, _ClassAccumulator())
                    for _qty, symbol, unit_kind in composition:
                        accumulator.add(unit_kind, symbol)
        return securities, classes


def _int(value: str | None) -> int:
    try:
        return int(value) if value else 0
    except ValueError:
        return 0


def _iso_date(value: str | None) -> date | None:
    """An FCA date column (``YYYY-MM-DD``), or ``None`` when blank or malformed."""
    text = (value or "").strip()
    try:
        return date.fromisoformat(text) if text else None
    except ValueError:
        return None


def _prefer(candidate: _Security, current: _Security) -> bool:
    """A still-trading listing wins; then the higher document version."""
    if candidate.trading != current.trading:
        return candidate.trading
    return candidate.version > current.version
