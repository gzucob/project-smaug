"""Reads the CAPITAL raw mirror (Mongo) into a per-year share count.

The ingestion stores one FRE capital row per ticker per mirrored year. The
analysis needs a share count for each view: the fiscal year of a closed-year
analysis, and the current year for the live TTM. A year that was never
ingested falls back to the nearest *earlier* year on file — share counts move
slowly, and an adjacent year beats no per-share indicator at all. A year with
nothing before it yields ``None`` and ``eps``/``bvps`` degrade to null.

Units (SAPR11, TAEE11) are excluded here: their quoted price is the price of a
bundle of shares, so the filed share count is the wrong denominator. See
``portfolio.domain.share_classes``.
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol

from smaug.portfolio.domain.share_classes import is_unit

CAPITAL_MODULE = "CAPITAL"


class RawCollection(Protocol):
    """Minimal read surface over the ``raw_ingestions`` collection."""

    def find(self, filter: Mapping[str, Any], /) -> Any: ...


def _dec(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _year_of(reference_date: Any) -> int | None:
    """Year from an ISO ``YYYY-MM-DD`` reference date, or None if unparseable."""
    if not isinstance(reference_date, str) or len(reference_date) < 4:
        return None
    try:
        return int(reference_date[:4])
    except ValueError:
        return None


class MongoSharesReader:
    """Serves the filed total share count per fiscal year from the raw mirror."""

    def __init__(self, collection: RawCollection) -> None:
        self._collection = collection

    async def outstanding(self, ticker: str, year: int) -> Decimal | None:
        if is_unit(ticker):
            return None
        by_year = await self._by_year(ticker)
        if not by_year:
            return None
        candidates = [filed for filed in by_year if filed <= year]
        if not candidates:
            return None
        return by_year[max(candidates)]

    async def _by_year(self, ticker: str) -> dict[int, Decimal]:
        """Latest filed total share count per year (a later ingestion wins)."""
        # Oldest first, so a re-ingestion of the same year overwrites the older one.
        cursor = self._collection.find(
            {"ticker": ticker, "source": "cvm", "module": CAPITAL_MODULE}
        ).sort("fetched_at", 1)
        by_year: dict[int, Decimal] = {}
        async for document in cursor:
            payload = document.get("payload")
            if not isinstance(payload, Mapping):
                continue
            year = _year_of(payload.get("reference_date"))
            total = _dec(payload.get("total_shares"))
            if year is None or total is None or total <= 0:
                continue
            by_year[year] = total
        return by_year
