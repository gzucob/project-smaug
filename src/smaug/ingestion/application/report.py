"""Completeness report use case (plan §6).

Reads the raw mirror and answers, per ticker: which modules arrived, how many
quarters of history came, and whether the sector-critical fields are present.
It only *reads* and *counts* — it never derives indicators (that is Phase 2).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from smaug.ingestion.domain.repositories import RawIngestionRepository
from smaug.portfolio.domain.sectors import Sector, sector_of

# Sector-directed field expectations (plan §6.1). Names follow brapi's usual
# vocabulary; a missing field here is a Phase 1 *discovery*, not a bug.
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


@dataclass(frozen=True)
class ModulePresence:
    """Whether a module's snapshot exists and how deep it goes."""

    module: str
    present: bool
    http_status: int | None
    quarters: int
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
    """The full report: one entry per ticker."""

    tickers: tuple[TickerReport, ...]


class CompletenessReportUseCase:
    """Build the completeness report from the raw mirror."""

    def __init__(
        self, repository: RawIngestionRepository, modules: Sequence[str]
    ) -> None:
        self._repository = repository
        self._modules = tuple(modules)

    async def execute(self, tickers: Iterable[str]) -> CompletenessReport:
        reports: list[TickerReport] = []
        for ticker in tickers:
            reports.append(await self._report_ticker(ticker))
        return CompletenessReport(tickers=tuple(reports))

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
                    quarters=_count_quarters(snapshot.payload),
                    fetched_at=snapshot.fetched_at,
                )
            )

        expected = _SECTOR_FIELDS[sector]
        present_fields = tuple(
            field
            for field in expected
            if any(_has_nonempty(payload, field) for payload in payloads)
        )
        missing_fields = tuple(f for f in expected if f not in present_fields)

        return TickerReport(
            ticker=ticker,
            sector=sector,
            modules=tuple(presences),
            sector_check=SectorCheck(sector, present_fields, missing_fields),
            max_quarters=max((p.quarters for p in presences), default=0),
            last_collected_at=max(timestamps, default=None),
        )


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
