"""Daily B3 observations and versioned, split-adjusted history snapshots."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Protocol

from smaug.analysis.domain.capital import RestatementStep

PRICE_HISTORY_CONTRACT = "b3_daily_close@1"


@dataclass(frozen=True, slots=True)
class TradedClose:
    """One official close and the trading code that printed it."""

    session: date
    code: str
    close: Decimal


@dataclass(frozen=True, slots=True)
class HistoricalClose:
    """A close restated onto the snapshot's share base, with its divisor."""

    session: date
    code: str
    as_traded: Decimal
    adjusted: Decimal
    factor: Decimal


@dataclass(frozen=True)
class PriceHistoryGap:
    """A year without a resolvable complete price series."""

    year: int
    reason: str


@dataclass(frozen=True)
class PriceHistory:
    """One append-only calculation of a security's daily history."""

    ticker: str
    computed_at: datetime
    start_year: int
    end_year: int
    points: tuple[HistoricalClose, ...]
    gaps: tuple[PriceHistoryGap, ...]
    contract_version: str = PRICE_HISTORY_CONTRACT
    price_basis: str = "split_adjusted"
    source: str = "B3/COTAHIST"


class DailyHistorySource(Protocol):
    """Resolve daily sessions and ticker succession without partial years."""

    async def history_year(
        self, ticker: str, year: int
    ) -> tuple[Sequence[TradedClose], str | None]: ...


class RestatementReader(Protocol):
    """Read the same dated share-base moves used by the indicators."""

    async def restatement_timeline(self, ticker: str) -> Sequence[RestatementStep]: ...


class PriceHistoryRepository(Protocol):
    """Append snapshots and read the latest complete calculation."""

    async def save(self, history: PriceHistory) -> None: ...

    async def latest(self, ticker: str) -> PriceHistory | None: ...
