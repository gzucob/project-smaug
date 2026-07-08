"""Domain ports for the analysis context.

The use case depends only on these interfaces, so it never imports Mongo,
brapi or SQLAlchemy directly. Infrastructure supplies the implementations and
the composition root wires them.
"""

from __future__ import annotations

from typing import Protocol

from smaug.analysis.domain.entities import TickerAnalysis
from smaug.analysis.domain.financials import MarketData, StandardizedFinancials


class FundamentalsReader(Protocol):
    """Reads the standardized financial history (oldest→newest) for a ticker."""

    async def history(self, ticker: str) -> list[StandardizedFinancials]: ...


class PriceProvider(Protocol):
    """Provides current market data (price, market cap) for a ticker."""

    async def get(self, ticker: str) -> MarketData: ...


class AnalysisRepository(Protocol):
    """Persists and reads back computed analyses."""

    async def save(self, analysis: TickerAnalysis) -> None: ...

    async def latest(self, ticker: str) -> TickerAnalysis | None: ...

    async def all_latest(self) -> list[TickerAnalysis]: ...
