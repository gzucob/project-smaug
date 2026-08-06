"""Test doubles and helpers shared across the suite.

No network, no Mongo: the fakes implement the domain interfaces in memory so
use cases can be exercised deterministically (plan §8).
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from smaug.ingestion.domain.entities import RawIngestion
from smaug.ingestion.domain.ports import RawFetchResult
from smaug.ingestion.domain.runs import IngestionRun, ParserIdentity
from smaug.portfolio.domain.sectors import Sector
from smaug.portfolio.domain.share_classes import ShareClass, ShareKind


def make_snapshot(
    ticker: str,
    module: str,
    payload: dict[str, Any],
    *,
    fetched_at: datetime | None = None,
) -> RawIngestion:
    """Build a ``RawIngestion`` for tests with sane defaults."""
    return RawIngestion(
        ticker=ticker,
        source="cvm",
        module=module,
        fetched_at=fetched_at or datetime(2026, 7, 2, tzinfo=UTC),
        request={},
        http_status=200,
        payload=payload,
    )


async def no_sleep(_seconds: float) -> None:
    """Drop-in for ``asyncio.sleep`` so tests don't actually wait."""
    return None


class FakeRawIngestionRepository:
    """In-memory, append-only repository matching ``RawIngestionRepository``."""

    def __init__(self) -> None:
        self.items: list[RawIngestion] = []

    async def add(self, ingestion: RawIngestion) -> RawIngestion:
        stored = replace(ingestion, id=str(len(self.items) + 1))
        self.items.append(stored)
        return stored

    async def find_latest(
        self, ticker: str, module: str, *, cvm_code: str | None = None
    ) -> RawIngestion | None:
        def keyed(item: RawIngestion) -> bool:
            if cvm_code is not None:
                return item.cvm_code == cvm_code
            return item.ticker == ticker

        matches = [item for item in self.items if keyed(item) and item.module == module]
        if not matches:
            return None
        return max(matches, key=lambda item: item.fetched_at)

    async def unlinked_tickers(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                {
                    item.ticker
                    for item in self.items
                    if item.source == "cvm" and item.cvm_code is None
                }
            )
        )

    async def mirrored_for(self, module: str, *, file: str | None = None) -> set[str]:
        return {
            item.cvm_code
            for item in self.items
            if item.module == module
            and item.cvm_code is not None
            and (file is None or item.request.get("file") == file)
        }

    async def link_registrant(self, ticker: str, cvm_code: str) -> int:
        linked = 0
        for index, item in enumerate(self.items):
            if item.source == "cvm" and item.cvm_code is None and item.ticker == ticker:
                self.items[index] = replace(item, cvm_code=cvm_code)
                linked += 1
        return linked


class FakeIngestionRunRepository:
    """In-memory lifecycle store matching ``IngestionRunRepository``."""

    def __init__(self) -> None:
        self.items: dict[str, IngestionRun] = {}

    async def add(self, run: IngestionRun) -> IngestionRun:
        self.items[run.run_id] = run
        return run

    async def update(self, run: IngestionRun) -> IngestionRun:
        if run.run_id not in self.items:
            raise LookupError(f"ingestion run not found: {run.run_id}")
        self.items[run.run_id] = run
        return run

    async def get(self, run_id: str) -> IngestionRun | None:
        return self.items.get(run_id)

    async def recent(self, limit: int) -> tuple[IngestionRun, ...]:
        ordered = sorted(
            self.items.values(), key=lambda run: run.started_at, reverse=True
        )
        return tuple(ordered[:limit])


class FakeDataSource:
    """In-memory ``RawDataSource``: returns canned payloads or raises errors."""

    parser_identity = ParserIdentity("test.fake", 1)

    def __init__(
        self,
        *,
        errors: dict[tuple[str, str], Exception] | None = None,
        payloads: dict[tuple[str, str], dict[str, Any]] | None = None,
    ) -> None:
        self._errors = errors or {}
        self._payloads = payloads or {}
        self.calls: list[tuple[str, str]] = []

    async def fetch(self, ticker: str, module: str) -> list[RawFetchResult]:
        self.calls.append((ticker, module))
        if (ticker, module) in self._errors:
            raise self._errors[(ticker, module)]
        payload = self._payloads.get(
            (ticker, module), {"results": [{"symbol": ticker}]}
        )
        return [
            RawFetchResult(
                module=module,
                request={"params": {"modules": module}},
                http_status=200,
                payload=payload,
            )
        ]


# --- Sector / share-class facts, for tests only -----------------------------
#
# Production code resolves both, for every ticker, from a live CVM FCA
# download (``CvmCompanyRegistry`` — #212): there is no per-ticker shortcut
# left anywhere under ``src/``. A unit test still needs *some* answer for the
# handful of tickers its fixtures name, so it keeps its own small, explicit
# copy here rather than reaching for the network.

_FAKE_SECTORS: dict[str, Sector] = {
    "PETR4": Sector.COMMODITY,
    "VALE3": Sector.COMMODITY,
    "SAPR11": Sector.UTILITY,
    "TAEE11": Sector.UTILITY,
    "WEGE3": Sector.INDUSTRY,
    "BBAS3": Sector.BANK,
    "BBDC4": Sector.BANK,
    "BBSE3": Sector.INSURER,
    "CXSE3": Sector.INSURER,
}


def fake_sector_resolver(ticker: str) -> Sector:
    """A ``Sector`` for the tickers this suite's fixtures name; else INDUSTRY."""
    return _FAKE_SECTORS.get(ticker, Sector.INDUSTRY)


def _on(symbol: str) -> ShareClass:
    return ShareClass(symbol=symbol, kind=ShareKind.COMMON)


def _pn(symbol: str) -> ShareClass:
    return ShareClass(symbol=symbol, kind=ShareKind.PREFERRED)


# The classes each fixture's company lists, including the ticker's own
# siblings — PETR4 is analyzed, but Petrobras is worth PETR3 + PETR4.
_FAKE_CLASSES: dict[str, tuple[ShareClass, ...]] = {
    "PETR4": (_on("PETR3"), _pn("PETR4")),
    "VALE3": (_on("VALE3"),),
    "SAPR11": (_on("SAPR3"), _pn("SAPR4")),
    "TAEE11": (_on("TAEE3"), _pn("TAEE4")),
    "WEGE3": (_on("WEGE3"),),
    "BBAS3": (_on("BBAS3"),),
    "BBDC4": (_on("BBDC3"), _pn("BBDC4")),
    "BBSE3": (_on("BBSE3"),),
    "CXSE3": (_on("CXSE3"),),
}


def fake_classes_resolver(ticker: str) -> tuple[ShareClass, ...]:
    """The listed ON/PN classes for the tickers this suite's fixtures name."""
    return _FAKE_CLASSES.get(ticker, ())


# Underlying shares one unit bundles — both fixtures' units are 1 ON + 2 PN.
_FAKE_UNIT_COMPOSITION: dict[str, int] = {"SAPR11": 3, "TAEE11": 3}


def fake_unit_composition_resolver(ticker: str) -> int | None:
    """Underlying shares one unit bundles, for the tickers this suite names."""
    return _FAKE_UNIT_COMPOSITION.get(ticker)
