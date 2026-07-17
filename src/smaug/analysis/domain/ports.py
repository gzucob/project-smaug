"""Domain ports for the analysis context.

The use case depends only on these interfaces, so it never imports Mongo,
brapi or SQLAlchemy directly. Infrastructure supplies the implementations and
the composition root wires them.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Protocol

from smaug.analysis.domain.entities import PruneResult, TickerAnalysis
from smaug.analysis.domain.financials import (
    MarketData,
    ShareCounts,
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
    brapi the fallback. Only the price is read: an implementation's own market cap
    is company-wide, and the use case builds the cap itself by summing each listed
    share class at its own quote (ADR 0014). It is called once per class, so the
    ``ticker`` here is a share class symbol (``PETR3``), not only a portfolio one.
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
    """Reads a company's outstanding shares for a given fiscal year.

    Outstanding, not issued: the shares the company holds in treasury are netted
    out of both readings below (ADR 0017), so the cap and the per-share indicators
    are built on the same denominator.
    """

    async def outstanding(self, ticker: str, year: int) -> Decimal | None:
        """Total shares as of ``year``, or the nearest earlier year on file."""
        ...

    async def counts(self, ticker: str, year: int) -> ShareCounts | None:
        """The same filing split by class (ON/PN) — the multi-class cap's counts.

        Unlike ``outstanding`` this is served for a unit too: the cap sums the
        underlying classes, which is exactly what a unit's bundle price cannot
        give (ADR 0014).
        """
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

    async def prune(self) -> PruneResult:
        """Delete superseded runs, keeping only the latest per cell (#71).

        A cell is one (ticker, view, reference_date); the kept row is its newest
        ``computed_at`` — exactly what the reads above already surface, so pruning
        reclaims space without changing any read. A deliberate maintenance action,
        never a side effect of ``analyze``."""
        ...
