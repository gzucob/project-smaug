"""Test doubles and helpers shared across the suite.

No network, no Mongo: the fakes implement the domain interfaces in memory so
use cases can be exercised deterministically (plan §8).
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from smaug.ingestion.domain.entities import RawIngestion
from smaug.ingestion.domain.ports import RawFetchResult

_FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> dict[str, Any]:
    """Load a recorded brapi response from ``tests/fixtures``."""
    return json.loads((_FIXTURES / name).read_text(encoding="utf-8"))


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
        source="brapi",
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

    async def find_latest(self, ticker: str, module: str) -> RawIngestion | None:
        matches = [
            item
            for item in self.items
            if item.ticker == ticker and item.module == module
        ]
        if not matches:
            return None
        return max(matches, key=lambda item: item.fetched_at)


class FakeDataSource:
    """In-memory ``RawDataSource``: returns canned payloads or raises errors."""

    def __init__(
        self,
        *,
        errors: dict[tuple[str, str], Exception] | None = None,
        payloads: dict[tuple[str, str], dict[str, Any]] | None = None,
    ) -> None:
        self._errors = errors or {}
        self._payloads = payloads or {}
        self.calls: list[tuple[str, str]] = []

    async def fetch(self, ticker: str, module: str) -> RawFetchResult:
        self.calls.append((ticker, module))
        if (ticker, module) in self._errors:
            raise self._errors[(ticker, module)]
        payload = self._payloads.get(
            (ticker, module), {"results": [{"symbol": ticker}]}
        )
        return RawFetchResult(
            module=module,
            request={"params": {"modules": module}},
            http_status=200,
            payload=payload,
        )
