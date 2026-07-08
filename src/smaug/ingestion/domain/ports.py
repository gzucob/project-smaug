"""Domain ports for the ingestion context.

Interfaces the application depends on so it never imports infrastructure
directly (plan §3.1). The brapi client implements ``RawDataSource``; tests
can substitute a fake without touching the network.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class RawFetchResult:
    """Raw, uninterpreted result of one source call (no infra types)."""

    module: str
    request: Mapping[str, Any]
    http_status: int
    payload: Mapping[str, Any]


class RawDataSource(Protocol):
    """A source that can fetch one module for one ticker.

    A single call may yield *several* filings: brapi returns one payload, but a
    CVM ITR file carries every quarter of the year (Q1/Q2/Q3), so the source
    returns one result per period. The sequence is never empty — a source that
    finds nothing raises a ``BrapiError`` subclass instead.
    """

    async def fetch(self, ticker: str, module: str) -> Sequence[RawFetchResult]:
        """Return the raw results (one per period), or raise a ``BrapiError``."""
        ...
