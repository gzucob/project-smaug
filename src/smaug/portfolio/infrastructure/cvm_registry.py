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

from smaug.portfolio.domain.company import CompanyIdentity, InstrumentKind
from smaug.portfolio.domain.share_classes import (
    PerShareClass,
    ShareClass,
    ShareKind,
    UnitComponent,
    per_share_class_from_symbol,
)
from smaug.portfolio.domain.universe import ListedCompany, listed_companies
from smaug.shared.artifacts import SourceArtifact, SourceArtifactStore
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
    instrument_kind: InstrumentKind
    instrument_type: str
    trading_ended: date | None = None
    listed_since: date | None = None
    # Underlying shares this ticker's own row bundles, when the row is a unit
    # ("Units ..."); ``None`` for a plain ON/PN class.
    unit_shares: int | None = None
    unit_components: tuple[UnitComponent, ...] = ()


@dataclass
class _ClassAccumulator:
    """The ON/PN/PNA/PNB trading symbols a company lists, gathered per class."""

    symbols: dict[PerShareClass, set[str]] = field(default_factory=dict)

    def add(self, kind: ShareKind, symbol: str) -> None:
        per_share_class = per_share_class_from_symbol(symbol, kind)
        self.symbols.setdefault(per_share_class, set()).add(symbol)


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


def _instrument_kind(valor_mobiliario: str) -> InstrumentKind:
    """Classify the FCA's security label without consulting the ticker suffix."""
    share_kind = _share_kind(valor_mobiliario)
    if share_kind is ShareKind.COMMON:
        return InstrumentKind.COMMON_SHARE
    if share_kind is ShareKind.PREFERRED:
        return InstrumentKind.PREFERRED_SHARE
    label = _fold(valor_mobiliario).strip()
    if label.startswith("units"):
        return InstrumentKind.UNIT
    if label.startswith("bonus de subscricao"):
        return InstrumentKind.SUBSCRIPTION_WARRANT
    if label.startswith("recibos de subscricao"):
        return InstrumentKind.SUBSCRIPTION_RECEIPT
    if label.startswith("bdr") or label.startswith("certificados de deposito"):
        return InstrumentKind.DEPOSITARY_RECEIPT
    return InstrumentKind.OTHER


_UNIT_SEPARATOR_RE = re.compile(r"\s*(?:\+|/|\be\b)\s*")
_SYMBOL_COMPONENT_RE = re.compile(r"(?P<qty>\d+)\s*(?P<symbol>[a-z0-9]{4}\d{1,2})")
_TEXT_COMPONENT_RE = re.compile(
    r"(?P<qty>\d+)\s*(?:(?:acao|acoes)\s+)?"
    r"(?P<label>ons?|pn[a-z]*s?|ordinari[ao]s?|preferencia(?:l|is))"
)


def _kind_from_suffix(symbol: str) -> ShareKind | None:
    """ON/PN from a B3 ticker suffix (3 = ON, 4/5/6 = PN); ``None`` for a unit."""
    suffix = symbol[4:]  # B3 roots are four letters; the rest is the class number
    if suffix == "3":
        return ShareKind.COMMON
    if suffix in ("4", "5", "6"):
        return ShareKind.PREFERRED
    return None


def _unit_composition(composition: str) -> list[UnitComponent]:
    """Quantity + underlying ON/PN classes named in a unit's ``Composicao_BDR_Unit``.

    Some companies file only the unit on the FCA (Klabin lists KLBN11, never
    KLBN3/KLBN4), but the unit row spells its bundle out — e.g. "1 KLBN3 +
    4 KLBN4". Parse the leading quantity alongside each class ticker, keyed by
    suffix: the quantities summed are how many underlying shares one unit is
    worth (#212), which the FRE itself never publishes.
    """
    parts = [part for part in _UNIT_SEPARATOR_RE.split(_fold(composition)) if part]
    resolved: list[UnitComponent] = []
    for part in parts:
        symbol_match = _SYMBOL_COMPONENT_RE.fullmatch(part)
        if symbol_match is not None:
            symbol = symbol_match.group("symbol").upper()
            kind = _kind_from_suffix(symbol)
            if kind is None:
                return []
            resolved.append(
                UnitComponent(
                    int(symbol_match.group("qty")),
                    per_share_class_from_symbol(symbol, kind),
                    symbol,
                )
            )
            continue
        text_match = _TEXT_COMPONENT_RE.fullmatch(part)
        if text_match is None:
            # Refuse a partial reading such as "1 PN + 3 subscription receipts".
            return []
        label = text_match.group("label")
        kind = (
            ShareKind.COMMON
            if label.startswith("on") or label.startswith("ordinari")
            else ShareKind.PREFERRED
        )
        preferred_label = label.removesuffix("s").upper()
        per_share_class = (
            PerShareClass.ORDINARY
            if kind is ShareKind.COMMON
            else {
                "PNA": PerShareClass.PREFERRED_A,
                "PNB": PerShareClass.PREFERRED_B,
            }.get(preferred_label, PerShareClass.PREFERRED)
        )
        resolved.append(UnitComponent(int(text_match.group("qty")), per_share_class))
    return resolved


def _resolve_classes(
    accumulator: _ClassAccumulator,
) -> tuple[ShareClass, ...]:
    """The company's ON/PN/PNA/PNB classes, ordered by economic class.

    Only when there is at most one symbol per economic class. Two codes for the
    same class cannot both multiply its filed count; rather than a wrong cap, an
    ambiguous company yields no classes (the cap stays a named null).
    """
    if any(len(symbols) > 1 for symbols in accumulator.symbols.values()):
        return ()
    classes: list[ShareClass] = []
    for per_share_class in PerShareClass:
        kind = (
            ShareKind.COMMON
            if per_share_class is PerShareClass.ORDINARY
            else ShareKind.PREFERRED
        )
        for symbol in sorted(accumulator.symbols.get(per_share_class, ())):
            classes.append(ShareClass(symbol=symbol, kind=kind))
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
        artifact_store: SourceArtifactStore | None = None,
        artifact_id: str | None = None,
    ) -> None:
        self._http = http_client
        self._year = year
        self._cache_dir = Path(cache_dir)
        self._base_url = (base_url or CVM_FCA_BASE_URL).rstrip("/")
        self._sleep = sleep
        self._artifact_store = artifact_store
        self._replay_artifact_id = artifact_id
        self._artifact: SourceArtifact | None = None
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

    async def artifact(self) -> SourceArtifact | None:
        """Acquire the FCA identity without parsing its members."""
        return await self._ensure_artifact()

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
            raw = await self._archive_path()
            index = await asyncio.to_thread(self._build_index, raw)
            self._index = index
            logger.info(
                "Loaded CVM FCA %s registry: %d coded securities",
                self._year,
                len(index),
            )
            return index

    async def _archive_path(self) -> Path:
        artifact = await self._ensure_artifact()
        if artifact is not None:
            return artifact.path
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        raw = self._cache_dir / self._zip_name
        if not raw.exists():
            await self._download(raw)
        return raw

    async def _ensure_artifact(self) -> SourceArtifact | None:
        if self._artifact_store is None:
            return None
        if self._artifact is None:
            if self._replay_artifact_id is not None:
                self._artifact = await self._artifact_store.open(
                    self._replay_artifact_id
                )
            else:
                self._artifact = await self._artifact_store.acquire(
                    f"{self._base_url}/{self._zip_name}", follow_redirects=True
                )
        return self._artifact

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
                instrument_kind=security.instrument_kind,
                instrument_type=security.instrument_type,
                trading_ended=security.trading_ended,
                listed_since=security.listed_since,
                share_classes=classes.get(security.cnpj, ()),
                shares_per_unit=security.unit_shares,
                unit_components=security.unit_components,
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
        from explicit component tickers or textual ON/PN quantities in the same
        ``Composicao_BDR_Unit`` field (ADR 0053).
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
                trading_ended = _iso_date(row.get("Data_Fim_Negociacao"))
                valor = (row.get("Valor_Mobiliario") or "").strip()
                kind = _share_kind(valor)
                instrument_kind = _instrument_kind(valor)
                composition = (
                    _unit_composition(row.get("Composicao_BDR_Unit") or "")
                    if instrument_kind is InstrumentKind.UNIT
                    else ()
                )
                unit_shares = sum(item.quantity for item in composition) or None
                candidate = _Security(
                    cnpj=cnpj,
                    trading=trading,
                    version=_int(row.get("Versao")),
                    instrument_kind=instrument_kind,
                    instrument_type=valor,
                    trading_ended=trading_ended,
                    listed_since=_iso_date(row.get("Data_Inicio_Listagem")),
                    unit_shares=unit_shares,
                    unit_components=tuple(composition),
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
                    for component in composition:
                        if component.symbol is not None:
                            kind = (
                                ShareKind.COMMON
                                if component.per_share_class is PerShareClass.ORDINARY
                                else ShareKind.PREFERRED
                            )
                            accumulator.add(kind, component.symbol)
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
