"""Domain ports for the analysis context.

The use case depends only on these interfaces, so it never imports Mongo,
brapi or SQLAlchemy directly. Infrastructure supplies the implementations and
the composition root wires them.
"""

from __future__ import annotations

from typing import Protocol

from smaug.analysis.domain.entities import TickerAnalysis
from smaug.analysis.domain.financials import (
    MarketData,
    StandardizedFinancials,
    YearPrices,
)


class FundamentalsReader(Protocol):
    """Reads standardized financials for a ticker: ITR quarters and the annual DFP."""

    async def history(self, ticker: str) -> list[StandardizedFinancials]:
        """ITR quarterly periods (oldest→newest); the TTM window is built from these."""
        ...

    async def annual(self, ticker: str) -> StandardizedFinancials | None:
        """The most recent annual DFP (closed year), used to derive the missing Q4."""
        ...


class PriceProvider(Protocol):
    """Provides current market data (price, market cap) for a ticker."""

    async def get(self, ticker: str) -> MarketData: ...

    async def year_prices(self, ticker: str, year: int) -> YearPrices:
        """Average nominal and dividend-adjusted price over ``year``."""
        ...


class AnalysisRepository(Protocol):
    """Persists and reads back computed analyses."""

    async def save(self, analysis: TickerAnalysis) -> None: ...

    async def latest(self, ticker: str) -> TickerAnalysis | None: ...

    async def all_latest(self) -> list[TickerAnalysis]: ...
