"""Serve a security's price under every code it has traded as (#193).

Two readers need the same answer and would otherwise disagree: the price, which
averages a year's sessions, and the base-change reader, which dates the corporate
actions off the same tape. If only the price were joined, an action that happened
under the old code would go undated and the recovered sessions would never be
restated — the joined year would then mix two share bases, which is exactly what
joining exists to avoid. So the chain is resolved once, here, and both take it.

The resolution walks backwards from the code trading today, because that is the
direction the evidence runs: the FCA names the codes a registrant has filed for a
share class, and the archive says when each of them traded. What decides a join
is in ``analysis.domain.succession``; this module only fetches what that decision
needs and caches it per ticker.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from datetime import date
from decimal import Decimal

from smaug.analysis.domain.capital import RestatementStep
from smaug.analysis.domain.financials import MarketData, SessionClose, YearPrices
from smaug.analysis.domain.indicators import NullReason
from smaug.analysis.domain.ports import SessionPriceProvider
from smaug.analysis.domain.succession import (
    CodeWindow,
    Explains,
    candidates_of,
    joined,
    structural_gap,
)
from smaug.analysis.infrastructure.b3_prices import CotahistArchive
from smaug.portfolio.domain.securities import SiblingCodesResolver, no_siblings
from smaug.shared.logging import get_logger

logger = get_logger(__name__)

ListedSinceResolver = Callable[[str], date | None]

# The dated share-base moves of one ticker (``SharesReader.restatement_timeline``),
# taken as a callable so the price side can read it without depending on the whole
# share port — and so the composition root can build it after the tape reader that
# feeds it.
TimelineReader = Callable[[str], Awaitable[Sequence[RestatementStep]]]

# How many years before the earliest one asked about the chain is resolved over.
# It exists so that "this code's series begins inside the year" is a claim the
# window can actually falsify (see ``_chain``).
_LOOKBACK_YEARS = 2


def _unknown_listed_since(ticker: str) -> date | None:
    return None


class CodeSuccession:
    """The chain of codes one security has traded under, over a run's years."""

    def __init__(
        self,
        archive: CotahistArchive,
        *,
        siblings: SiblingCodesResolver = no_siblings,
        listed_since: ListedSinceResolver = _unknown_listed_since,
        today: Callable[[], date] = date.today,
    ) -> None:
        self._archive = archive
        self._siblings = siblings
        self._listed_since = listed_since
        self._today = today
        self._chains: dict[str, tuple[int, tuple[CodeWindow, ...]]] = {}
        self._calendars: dict[int, tuple[date, ...]] = {}

    async def candidates(self, ticker: str, since: int) -> tuple[str, ...]:
        """Every code that could answer for ``ticker``, oldest first.

        What the **tape** reader walks, and deliberately wider than what the
        price averages: a seam the price refuses to cross is still the session an
        action took effect on, and reading it is how that action gets dated at
        all (ADR 0043).
        """
        return tuple(window.code for window in await self._chain(ticker, since))

    async def chain(
        self, ticker: str, since: int, *, explains: Explains
    ) -> tuple[CodeWindow, ...]:
        """The candidates whose seams the price crosses or the restatement explains."""
        return joined(await self._chain(ticker, since), explains=explains)

    async def sessions(
        self, codes: Sequence[CodeWindow], year: int
    ) -> tuple[SessionClose, ...]:
        """``year``'s closes across ``codes``, as traded, oldest first."""
        merged: list[SessionClose] = []
        for window in codes:
            quotes = (await self._archive.year(year)).get(window.code)
            if quotes is not None:
                merged.extend(quotes.session_closes())
        return tuple(sorted(merged, key=lambda close: close.session))

    async def unpriceable(
        self, ticker: str, year: int, resolved: Sequence[CodeWindow]
    ) -> bool:
        """Whether ``year`` precedes everything ``resolved`` names (see the domain)."""
        calendar = await self._calendar(year)
        start = resolved[0].first_session if resolved else None
        return structural_gap(
            year,
            chain_start=start,
            listed_since=self._listed_since(ticker),
            year_opened_on=calendar[0] if calendar else None,
            coverage=await self._coverage(resolved, year, start, calendar),
        )

    async def _coverage(
        self,
        resolved: Sequence[CodeWindow],
        year: int,
        start: date | None,
        calendar: tuple[date, ...],
    ) -> Decimal | None:
        """What share of the year's sessions from ``start`` the chain printed.

        A code that debuted mid-year trades from then on; an illiquid one prints
        a handful of sessions whenever somebody wants it. Only asked when the
        chain begins inside the year, which is the only case that needs telling
        those two apart.
        """
        if start is None or start.year != year or not calendar:
            return None
        remaining = sum(1 for session in calendar if session >= start)
        if remaining <= 0:
            return None
        printed = sum(
            1 for close in await self.sessions(resolved, year) if close.session >= start
        )
        return Decimal(printed) / Decimal(remaining)

    async def _chain(self, ticker: str, since: int) -> tuple[CodeWindow, ...]:
        code = ticker.strip().upper()
        cached = self._chains.get(code)
        if cached is not None and cached[0] <= since:
            return cached[1]
        # Read from before the year asked about, or "the chain starts inside this
        # year" is true of every code by construction — the window would have no
        # earlier year to have started in. Two years, because a code can miss a
        # whole one and still be old: BDLL3 traded 7 sessions in 2020 and 183 in
        # 2021, and read from 2021 alone that thickening looks like a debut.
        opened = since - _LOOKBACK_YEARS
        served = await self._window(code, opened)
        if served is None:
            resolved: tuple[CodeWindow, ...] = ()
        else:
            siblings = [
                window
                for sibling in self._siblings(code)
                if (window := await self._window(sibling, opened)) is not None
            ]
            resolved = candidates_of(
                served, siblings, listed_since=self._listed_since(ticker)
            )
            if len(resolved) > 1:
                logger.info(
                    "%s has traded as %s since %d (#193)",
                    code,
                    " -> ".join(window.code for window in resolved),
                    since,
                )
        self._chains[code] = (since, resolved)
        return resolved

    async def _window(self, code: str, since: int) -> CodeWindow | None:
        """Where ``code``'s series starts and ends inside the readable years."""
        first: SessionClose | None = None
        last: SessionClose | None = None
        for year in range(since, self._today().year + 1):
            quotes = (await self._archive.year(year)).get(code)
            if quotes is None:
                continue
            if first is None:
                first = quotes.first_close()
            last = SessionClose(session=quotes.last_session, close=quotes.last_close)
        if first is None or last is None:
            return None
        return CodeWindow(
            code=code,
            first_session=first.session,
            last_session=last.session,
            first_close=first.close,
            last_close=last.close,
        )

    async def _calendar(self, year: int) -> tuple[date, ...]:
        """The year's trading sessions, taken from the code that traded most.

        The exchange publishes no calendar in this file, and the busiest code of
        a year is quoted on every session of it — 250 of 250 in 2025. It is read
        once per year and shared by every ticker of the run.
        """
        cached = self._calendars.get(year)
        if cached is not None:
            return cached
        quotes = await self._archive.year(year)
        busiest = max(
            quotes.values(), key=lambda year_quotes: year_quotes.sessions, default=None
        )
        calendar = (
            ()
            if busiest is None
            else tuple(close.session for close in busiest.session_closes())
        )
        self._calendars[year] = calendar
        return calendar


class SuccessionPriceProvider:
    """A ``SessionPriceProvider`` that reads a year under the codes that traded it.

    Innermost of the price decorators, so the dividend basis and the restatement
    both see the joined series (a session recovered from an older code still has
    to be put on today's share base, ADR 0027).

    It is here, and not in ``CodeSuccession``, that a seam is finally accepted or
    refused: the answer depends on the restatement timeline, which is read from
    the share side, and the share side reads the tape across the *candidates*.
    Keeping the two apart is what keeps that from being a circle — the tape needs
    the wider chain, the average needs the narrower one.

    A ticker whose chain is only itself — every code but twenty-odd of them — is
    delegated untouched, so the overwhelming case is byte-for-byte what it was.
    """

    def __init__(
        self,
        inner: SessionPriceProvider,
        succession: CodeSuccession,
        *,
        timeline: TimelineReader | None = None,
    ) -> None:
        self._inner = inner
        self._succession = succession
        self._timeline = timeline
        self._steps: dict[str, Sequence[RestatementStep]] = {}

    async def get(self, ticker: str) -> MarketData:
        # The live quote is today's code by definition; nothing to join.
        return await self._inner.get(ticker)

    async def year_sessions(self, ticker: str, year: int) -> tuple[SessionClose, ...]:
        resolved = await self._chain(ticker, year)
        if len(resolved) < 2:
            return tuple(await self._inner.year_sessions(ticker, year))
        return await self._succession.sessions(resolved, year)

    async def year_prices(self, ticker: str, year: int) -> YearPrices:
        resolved = await self._chain(ticker, year)
        if await self._succession.unpriceable(ticker, year, resolved):
            logger.warning(
                "%s traded %d under a code we cannot name; its price is null (#193)",
                ticker,
                year,
            )
            return YearPrices(null_reason=NullReason.PRICE_SYMBOL_NOT_FOUND)
        if len(resolved) < 2:
            return await self._inner.year_prices(ticker, year)
        sessions = await self._succession.sessions(resolved, year)
        if not sessions:
            return await self._inner.year_prices(ticker, year)
        total = sum((close.close for close in sessions), Decimal(0))
        return YearPrices(nominal_avg=total / Decimal(len(sessions)))

    async def _chain(self, ticker: str, year: int) -> tuple[CodeWindow, ...]:
        steps = await self._restatement(ticker)

        def explains(predecessor: CodeWindow, successor: CodeWindow) -> bool:
            """Whether a dated share-base move sits on this seam.

            Dated *on* it, not merely near it: the seam is one session, and the
            restatement chain only ever puts a step there by having matched its
            size to the seam's own (``_session_dated``). A step somewhere else in
            the gap is a different event and would leave this one unrestated.
            """
            return any(
                predecessor.last_session < step.effective <= successor.first_session
                for step in steps
            )

        return await self._succession.chain(ticker, year, explains=explains)

    async def _restatement(self, ticker: str) -> Sequence[RestatementStep]:
        if self._timeline is None:
            return ()
        cached = self._steps.get(ticker)
        if cached is None:
            cached = await self._timeline(ticker)
            self._steps[ticker] = cached
        return cached
