"""Reads the CAPITAL raw mirror (Mongo) into per-year share counts.

The ingestion stores one FRE capital row per ticker per mirrored year, already
split by class (ON/PN) plus the filer's own total. The analysis needs a count for
each view: the fiscal year of a closed-year analysis, and the current year for
the live TTM. A year that was never ingested falls back to the nearest *earlier*
year on file — share counts move slowly, and an adjacent year beats no indicator
at all. A year with nothing before it yields ``None``.

Two readings of the same filing, for two different jobs:

* ``counts`` — the classes, which the market cap sums price by price (ADR 0014).
  Served for every ticker, units included: the cap needs the underlying classes
  precisely because a unit's quote prices a bundle.
* ``outstanding`` — the filed total, the denominator of the per-share indicators
  (LPA/VPA) alone. Suppressed for a unit, whose per-unit price does not line up
  with a per-underlying-share figure (#38). See ``portfolio.domain.share_classes``.
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol

from smaug.analysis.domain.financials import ShareCounts
from smaug.portfolio.domain.share_classes import is_unit
from smaug.shared.logging import get_logger

logger = get_logger(__name__)

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


def _positive(value: Any) -> Decimal | None:
    """A share count that can serve as a denominator, else ``None``.

    The FRE writes a class the company does not have as ``0`` (every single-class
    filer writes zero preferred shares), and zero shares is never a fact to divide
    by — it is an absence. Naming it ``None`` here keeps it out of the cap.
    """
    count = _dec(value)
    return count if count is not None and count > 0 else None


def _sum(*counts: Decimal | None) -> Decimal | None:
    """The class counts added up — the stand-in when the filer's total is unusable.

    BBAS3's 2023 FRE files its 5.73 bn ordinary shares and then writes ``0`` in the
    total column (#39). Dropping the whole filing over that one blank column sent
    the year back to 2022's 2.87 bn — pricing the company at half its size, right
    across the 2:1 bonus. The classes are the filing's own numbers, so adding them
    is a reading of what was filed, not a repair of it.
    """
    present = [count for count in counts if count is not None]
    return sum(present, Decimal(0)) if present else None


def _rank(payload: Mapping[str, Any]) -> tuple[int, str]:
    """How one filed capital row beats another: newest amendment, newest approval.

    The approval date is an ISO ``YYYY-MM-DD`` string, so it sorts as filed; a row
    that carries none (any document mirrored before #86) ranks below every dated
    one rather than competing on ingestion order.
    """
    version = payload.get("version")
    approval = payload.get("approval_date")
    return (
        version if isinstance(version, int) else 0,
        approval if isinstance(approval, str) else "",
    )


def _year_of(reference_date: Any) -> int | None:
    """Year from an ISO ``YYYY-MM-DD`` reference date, or None if unparseable."""
    if not isinstance(reference_date, str) or len(reference_date) < 4:
        return None
    try:
        return int(reference_date[:4])
    except ValueError:
        return None


class MongoSharesReader:
    """Serves the filed share counts per fiscal year from the raw mirror."""

    def __init__(self, collection: RawCollection) -> None:
        self._collection = collection

    async def outstanding(self, ticker: str, year: int) -> Decimal | None:
        if is_unit(ticker):
            return None
        filed = await self.counts(ticker, year)
        return filed.total if filed is not None else None

    async def counts(self, ticker: str, year: int) -> ShareCounts | None:
        by_year = await self._by_year(ticker)
        candidates = [filed for filed in by_year if filed <= year]
        if not candidates:
            return None
        served = max(candidates)
        if served != year:
            # Say so: a year priced on an adjacent year's shares is an
            # approximation, and a silent one is indistinguishable from a fact
            # (#39). VALE3 has no 2023/2024 FRE in the mirror.
            logger.info(
                "No %d capital filing for %s; using the %d one", year, ticker, served
            )
        return by_year[served]

    async def _by_year(self, ticker: str) -> dict[int, ShareCounts]:
        """The capital composition that supersedes the rest, per year.

        Two filed facts order the candidates, in this order:

        * ``version`` — the mirror holds every FRE amendment (ADR 0016), so
          ingestion time is not enough: the highest amendment stands, whenever it
          happened to be ingested.
        * ``approval_date`` — *within* an amendment, the member is a history of
          capital events, and several of its rows are paid-in. SANEPAR's 2021 FRE
          files the 2020 split (1.51 bn shares) next to two 2016 approvals (503 M);
          the company's capital is the one most recently approved. Picking by
          cursor order instead served SANEPAR's 2016 capital, pricing the company
          at a third of its size (#86).

        ``fetched_at`` only breaks a tie between two copies of the same row. An
        undated row (mirrored before #86) sorts below every dated one, so a stale
        copy in the append-only mirror can never win.
        """
        cursor = self._collection.find(
            {"ticker": ticker, "source": "cvm", "module": CAPITAL_MODULE}
        ).sort("fetched_at", 1)
        by_year: dict[int, ShareCounts] = {}
        best: dict[int, tuple[int, str]] = {}
        async for document in cursor:
            payload = document.get("payload")
            if not isinstance(payload, Mapping):
                continue
            year = _year_of(payload.get("reference_date"))
            if year is None:
                continue
            rank = _rank(payload)
            if year in best and rank < best[year]:
                continue
            common = _positive(payload.get("common_shares"))
            preferred = _positive(payload.get("preferred_shares"))
            total = _positive(payload.get("total_shares")) or _sum(common, preferred)
            if total is None:
                continue
            best[year] = rank
            by_year[year] = ShareCounts(common=common, preferred=preferred, total=total)
        return by_year
