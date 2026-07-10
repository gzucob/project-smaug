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
    """Provides market data (current quote + per-year history) for a ticker.

    The two methods now come from different sources (ADR 0011): the live quote
    from brapi, the closed-year history from Yahoo. The use case still depends
    on this single port; ``CompositePriceProvider`` routes each call to the
    source that serves it.
    """

    async def get(self, ticker: str) -> MarketData: ...

    async def year_prices(self, ticker: str, year: int) -> YearPrices:
        """Average nominal and dividend-adjusted price over ``year``."""
        ...


class CurrentQuoteProvider(Protocol):
    """Provides a ticker's current market data (the live quote side).

    Split from ``PriceProvider`` so the live quote can be sourced and chained
    independently of the year history (ADR 0013): Yahoo is the primary quote,
    brapi the fallback. An implementation may return only the price (Yahoo does
    not expose market cap / shares for free); the use case derives the cap from
    price × filed shares when it is absent.
    """

    async def get(self, ticker: str) -> MarketData: ...


class PriceHistoryProvider(Protocol):
    """Provides a ticker's daily price averaged over a closed fiscal year.

    Split out from ``PriceProvider`` because the closed-year basis is sourced
    independently of the live quote (ADR 0011): brapi's free plan withholds
    multi-year daily history for all but its demo tickers, so the year averages
    come from Yahoo Finance. Requesting the year by exact window (not a fixed
    range) means extending coverage to more years never hits a range ceiling.
    """

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
