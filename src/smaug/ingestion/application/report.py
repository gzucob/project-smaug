"""Completeness report use case (plan §6).

Reads the raw mirror and answers, per ticker: which modules arrived, how deep
they go, and whether the sector-critical signals are present. It only *reads*
and *counts* — it never derives indicators (that is Phase 2).

The check is source-aware (a ``ReportProfile``): brapi payloads are keyed by
field name and carry quarterly history, whereas CVM payloads are lists of raw
accounts (code/name/value) for one period. A missing signal is a Phase 1
*discovery*, not a bug.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from smaug.ingestion.domain.repositories import RawIngestionRepository
from smaug.portfolio.domain.sectors import Sector, sector_of

# --- brapi sector-directed field expectations (plan §6.1) ------------------
_NON_FINANCIAL_FIELDS: tuple[str, ...] = (
    "totalRevenue",
    "netIncome",
    "ebitda",
    "totalDebt",
    "grossProfit",
)
_BANK_FIELDS: tuple[str, ...] = (
    "totalStockholderEquity",
    "netIncome",
    "returnOnEquity",
)
_INSURER_FIELDS: tuple[str, ...] = (
    "totalStockholderEquity",
    "netIncome",
    "totalRevenue",
)
_SECTOR_FIELDS: dict[Sector, tuple[str, ...]] = {
    Sector.BANK: _BANK_FIELDS,
    Sector.INSURER: _INSURER_FIELDS,
    Sector.UTILITY: _NON_FINANCIAL_FIELDS,
    Sector.COMMODITY: _NON_FINANCIAL_FIELDS,
    Sector.INDUSTRY: _NON_FINANCIAL_FIELDS,
}


# --- CVM sector-directed account anchors -----------------------------------
@dataclass(frozen=True)
class _Anchor:
    """One expected account, matched by exact ``code`` or by a name substring.

    Codes/names verified against the real 2024 ITR: equity code differs by
    sector (2.03 vs bank's 2.07) so it is matched by name; the DRE revenue line
    keeps code 3.01 across sectors but changes name, hence the sector split.
    """

    label: str
    by: str  # "code" or "name"
    needle: str


# Present across every sector (name-matched to absorb layout differences).
_CVM_COMMON: tuple[_Anchor, ...] = (
    _Anchor("Ativo Total", "code", "1"),
    _Anchor("Patrimônio Líquido", "name", "patrimonio liquido"),
    _Anchor("Resultado do período", "name", "operacoes continuadas"),
)
_CVM_NON_FINANCIAL: tuple[_Anchor, ...] = (
    *_CVM_COMMON,
    _Anchor("Receita de venda", "code", "3.01"),
    _Anchor("Resultado Bruto", "name", "resultado bruto"),
)
_CVM_BANK: tuple[_Anchor, ...] = (
    *_CVM_COMMON,
    _Anchor("Receita de intermediação", "name", "intermediacao financeira"),
)
_CVM_INSURER: tuple[_Anchor, ...] = (
    *_CVM_COMMON,
    _Anchor("Receita de seguros", "name", "seguradoras"),
)
_CVM_SECTOR_ANCHORS: dict[Sector, tuple[_Anchor, ...]] = {
    Sector.BANK: _CVM_BANK,
    Sector.INSURER: _CVM_INSURER,
    Sector.UTILITY: _CVM_NON_FINANCIAL,
    Sector.COMMODITY: _CVM_NON_FINANCIAL,
    Sector.INDUSTRY: _CVM_NON_FINANCIAL,
}


@dataclass(frozen=True)
class ModulePresence:
    """Whether a module's snapshot exists and how deep it goes."""

    module: str
    present: bool
    http_status: int | None
    quarters: int  # brapi: quarters of history; cvm: number of accounts
    fetched_at: datetime | None


@dataclass(frozen=True)
class SectorCheck:
    """Sector-directed presence check over the collected payloads."""

    sector: Sector
    present_fields: tuple[str, ...]
    missing_fields: tuple[str, ...]


@dataclass(frozen=True)
class TickerReport:
    """Completeness of one ticker across all configured modules."""

    ticker: str
    sector: Sector
    modules: tuple[ModulePresence, ...]
    sector_check: SectorCheck
    max_quarters: int
    last_collected_at: datetime | None


@dataclass(frozen=True)
class CompletenessReport:
    """The full report: one entry per ticker, plus how depth is labelled."""

    tickers: tuple[TickerReport, ...]
    depth_label: str = "quarters"


class ReportProfile(Protocol):
    """Source-specific rules for depth counting and the sector check."""

    depth_label: str

    def count_depth(self, payload: Any) -> int:
        """How deep a single module payload goes (quarters / accounts)."""
        ...

    def sector_check(
        self, sector: Sector, payloads: Sequence[Mapping[str, Any]]
    ) -> SectorCheck:
        """Which sector-critical signals are present across the payloads."""
        ...


class _BrapiProfile:
    depth_label = "quarters"

    def count_depth(self, payload: Any) -> int:
        return _count_quarters(payload)

    def sector_check(
        self, sector: Sector, payloads: Sequence[Mapping[str, Any]]
    ) -> SectorCheck:
        expected = _SECTOR_FIELDS[sector]
        present = tuple(
            f for f in expected if any(_has_nonempty(p, f) for p in payloads)
        )
        missing = tuple(f for f in expected if f not in present)
        return SectorCheck(sector, present, missing)


class _CvmProfile:
    depth_label = "accounts"

    def count_depth(self, payload: Any) -> int:
        accounts = payload.get("accounts") if isinstance(payload, Mapping) else None
        return len(accounts) if isinstance(accounts, list) else 0

    def sector_check(
        self, sector: Sector, payloads: Sequence[Mapping[str, Any]]
    ) -> SectorCheck:
        present: list[str] = []
        missing: list[str] = []
        for anchor in _CVM_SECTOR_ANCHORS[sector]:
            bucket = present if _cvm_has_anchor(payloads, anchor) else missing
            bucket.append(anchor.label)
        return SectorCheck(sector, tuple(present), tuple(missing))


def _profile_for(source: str) -> ReportProfile:
    return _CvmProfile() if source == "cvm" else _BrapiProfile()


class CompletenessReportUseCase:
    """Build the completeness report from the raw mirror."""

    def __init__(
        self,
        repository: RawIngestionRepository,
        modules: Sequence[str],
        *,
        source: str = "brapi",
    ) -> None:
        self._repository = repository
        self._modules = tuple(modules)
        self._profile = _profile_for(source)

    async def execute(self, tickers: Iterable[str]) -> CompletenessReport:
        reports: list[TickerReport] = []
        for ticker in tickers:
            reports.append(await self._report_ticker(ticker))
        return CompletenessReport(
            tickers=tuple(reports), depth_label=self._profile.depth_label
        )

    async def _report_ticker(self, ticker: str) -> TickerReport:
        sector = sector_of(ticker)
        presences: list[ModulePresence] = []
        payloads: list[Mapping[str, Any]] = []
        timestamps: list[datetime] = []

        for module in self._modules:
            snapshot = await self._repository.find_latest(ticker, module)
            if snapshot is None:
                presences.append(ModulePresence(module, False, None, 0, None))
                continue
            payloads.append(snapshot.payload)
            timestamps.append(snapshot.fetched_at)
            presences.append(
                ModulePresence(
                    module=module,
                    present=True,
                    http_status=snapshot.http_status,
                    quarters=self._profile.count_depth(snapshot.payload),
                    fetched_at=snapshot.fetched_at,
                )
            )

        return TickerReport(
            ticker=ticker,
            sector=sector,
            modules=tuple(presences),
            sector_check=self._profile.sector_check(sector, payloads),
            max_quarters=max((p.quarters for p in presences), default=0),
            last_collected_at=max(timestamps, default=None),
        )


def _fold(text: str) -> str:
    """Lowercase and strip accents, so needle matches survive 'ç', 'ã', etc."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()


def _cvm_has_anchor(payloads: Sequence[Mapping[str, Any]], anchor: _Anchor) -> bool:
    """True if any collected statement holds the anchored account."""
    for payload in payloads:
        accounts = payload.get("accounts") if isinstance(payload, Mapping) else None
        if not isinstance(accounts, list):
            continue
        for account in accounts:
            if not isinstance(account, Mapping):
                continue
            if anchor.by == "code" and str(account.get("code", "")) == anchor.needle:
                return True
            if anchor.by == "name" and anchor.needle in _fold(
                str(account.get("name", ""))
            ):
                return True
    return False


def _iter_values(payload: Any) -> Iterable[Any]:
    """Depth-first walk over every nested value in a JSON-like structure."""
    yield payload
    if isinstance(payload, Mapping):
        for value in payload.values():
            yield from _iter_values(value)
    elif isinstance(payload, (list, tuple)):
        for item in payload:
            yield from _iter_values(item)


def _has_nonempty(payload: Any, field: str) -> bool:
    """True if ``field`` appears anywhere with a non-null, non-empty value."""
    for node in _iter_values(payload):
        if isinstance(node, Mapping) and field in node:
            value = node[field]
            if value not in (None, "", [], {}):
                return True
    return False


def _count_quarters(payload: Any) -> int:
    """Longest list-of-records found — a proxy for how many periods arrived."""
    best = 0
    for node in _iter_values(payload):
        if (
            isinstance(node, list)
            and node
            and all(isinstance(item, Mapping) for item in node)
        ):
            best = max(best, len(node))
    return best
