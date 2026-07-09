"""Domain ports for the analysis context.

The use case depends only on these interfaces, so it never imports Mongo,
brapi or SQLAlchemy directly. Infrastructure supplies the implementations and
the composition root wires them.
"""

from __future__ import annotations

from decimal import Decimal
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

    async def annuals(self, ticker: str) -> list[StandardizedFinancials]:
        """Closed-year DFPs (oldest→newest): the latest derives the missing Q4, and
        the prior year is the year-over-year growth base."""
        ...


class PriceProvider(Protocol):
    """Provides current market data (price, market cap) for a ticker."""

    async def get(self, ticker: str) -> MarketData: ...

    async def year_prices(self, ticker: str, year: int) -> YearPrices:
        """Average nominal and dividend-adjusted price over ``year``."""
        ...


class SharesReader(Protocol):
    """Reads the share count a company had filed for a given fiscal year."""

    async def outstanding(self, ticker: str, year: int) -> Decimal | None:
        """Total shares as of ``year``, or the nearest earlier year on file."""
        ...


class AnalysisRepository(Protocol):
    """Persists and reads back computed analyses."""

    async def save(self, analysis: TickerAnalysis) -> None: ...

    async def latest(self, ticker: str) -> TickerAnalysis | None:
        """The latest live TTM analysis for a ticker (the principal view)."""
        ...

    async def all_latest(self) -> list[TickerAnalysis]:
        """The latest TTM analysis per ticker — the portfolio overview."""
        ...

    async def history(self, ticker: str) -> list[TickerAnalysis]:
        """Closed-year analyses for a ticker: latest computation per fiscal year,
        oldest → newest."""
        ...
