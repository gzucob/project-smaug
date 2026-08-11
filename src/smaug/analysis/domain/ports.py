"""Domain ports for the analysis context.

The use case depends only on these interfaces, so it never imports Mongo, httpx
or SQLAlchemy directly. Infrastructure supplies the implementations and the
composition root wires them.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from typing import Protocol

from smaug.analysis.domain.capital import BaseChange, RestatementStep
from smaug.analysis.domain.dividends import CashEvent
from smaug.analysis.domain.entities import PruneResult, TickerAnalysis
from smaug.analysis.domain.financials import (
    MarketData,
    SessionClose,
    ShareCounts,
    StandardizedFinancials,
    YearPrices,
)
from smaug.analysis.domain.indicators import NullReason


class FundamentalsReader(Protocol):
    """Reads standardized financials for a ticker: ITR quarters and the annual DFP."""

    async def history(self, ticker: str) -> list[StandardizedFinancials]:
        """ITR quarterly periods (oldest→newest); the TTM window is built from these."""
        ...

    async def annuals(self, ticker: str) -> list[StandardizedFinancials]:
        """Closed-year DFPs (oldest→newest).

        Each eligible annual derives its missing Q4. Closed-year growth compares
        adjacent DFPs; TTM growth uses annuals only to reconstruct the prior
        comparable trailing window.
        """
        ...


class PriceProvider(Protocol):
    """Provides market data (current quote + per-year history) for a ticker.

    Both methods are answered by one source — B3's own series, which carries the
    last close and every closed year in the same file (ADR 0041). The port stays
    split from the two below because the *concerns* are distinct, not because
    two vendors used to serve them.
    """

    async def get(self, ticker: str) -> MarketData: ...

    async def year_prices(self, ticker: str, year: int) -> YearPrices:
        """Average nominal and dividend-adjusted price over ``year``."""
        ...


class SessionPriceProvider(PriceProvider, Protocol):
    """A ``PriceProvider`` that can also show the individual closes behind a year.

    Only a source publishing the price **as traded** has to offer this: what it
    is for is applying a corporate action to the sessions that preceded it and
    not to the ones that followed (ADR 0033). A series that arrived pre-adjusted
    could not offer it — the adjustment had already been folded in upstream.
    """

    async def year_sessions(self, ticker: str, year: int) -> Sequence[SessionClose]:
        """Every close the code printed in ``year``, as traded, oldest first."""
        ...


class CurrentQuoteProvider(Protocol):
    """Provides a ticker's current market data (the live quote side).

    Split from ``PriceProvider`` so the live quote can be sourced independently
    of the year history. Only the price is read: an implementation's own market cap
    is company-wide, and the use case builds the cap itself by summing each listed
    share class at its own quote (ADR 0014). It is called once per class, so the
    ``ticker`` here is a share class symbol (``PETR3``), not only a portfolio one.
    """

    async def get(self, ticker: str) -> MarketData: ...


class PriceHistoryProvider(Protocol):
    """Provides a ticker's daily price averaged over a closed fiscal year.

    Split out from ``PriceProvider`` because the closed-year basis is a different
    question from the live quote: it is an average over a window, and it is the
    one the historical view reads. Requesting the year by exact window (not a
    fixed range) means extending coverage to more years never hits a ceiling.
    """

    async def year_prices(self, ticker: str, year: int) -> YearPrices:
        """Average nominal and dividend-adjusted price over ``year``."""
        ...


class SharesReader(Protocol):
    """Reads a company's outstanding shares for a given fiscal year.

    Outstanding, not issued: the shares the company holds in treasury are netted
    out of both readings below (ADR 0017), so the cap and closing-count measures
    such as BVPS are built on the same denominator. CPC 41 EPS is separate.
    """

    async def outstanding(self, ticker: str, year: int) -> Decimal | None:
        """Total shares as of ``year``, or the nearest earlier year on file."""
        ...

    def outstanding_null_reason(self, ticker: str, year: int) -> NullReason | None:
        """A structural reason the closing-share denominator is unavailable."""
        ...

    async def counts(self, ticker: str, year: int) -> ShareCounts | None:
        """The same filing split by class (ON/PN) — the multi-class cap's counts.

        Unlike ``outstanding`` this is served for a unit too: the cap sums the
        underlying classes, which is exactly what a unit's bundle price cannot
        give (ADR 0014).
        """
        ...

    async def restatement_timeline(self, ticker: str) -> Sequence[RestatementStep]:
        """The dated share-base moves the counts above were restated by.

        The counts are already restated (ADR 0027); this publishes what they were
        restated *by*, because a price taken as traded has to be divided by the
        same thing. ``cap = price x shares`` is invariant only while both sit on
        one base — pairing an as-traded price with a restated count overstates
        BBAS3's pre-2024 cap by exactly 2x.

        Dated, and not one factor per year, because the price is a daily series:
        a company's action falls mid-year and the sessions either side of it are
        quoted on different bases (ADR 0033). Empty when nothing ever moved.
        """
        ...


class BaseChangeReader(Protocol):
    """Reads the sessions on which a code's share base moved.

    A separate port from ``PriceProvider`` although one file answers both: this
    is not a price, and the reader that needs it is the share side, which owns
    the restatement chain. Wired only where the price source publishes an
    unadjusted series — a vendor's back-adjusted one has already applied these
    and dating them again would restate twice.
    """

    async def base_changes(
        self, ticker: str, years: Sequence[int]
    ) -> Sequence[BaseChange]:
        """Every base change inside ``years``, oldest first."""
        ...


class CashEventReader(Protocol):
    """Reads the cash payments a ticker's share class went ex.

    Separate from the share side although both come from B3: a payment moves no
    share count, so it never enters the restatement — it only fills the third
    price basis (ADR 0039).
    """

    async def cash_events(self, ticker: str) -> Sequence[CashEvent]:
        """Every payment, oldest first, dated by the first session without it."""
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
