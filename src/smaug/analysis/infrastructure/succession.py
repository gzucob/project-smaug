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

from collections.abc import Awaitable, Callable, Mapping, Sequence
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
    crosses,
    joined,
    joins,
    structural_gap,
)
from smaug.analysis.infrastructure.b3_prices import CotahistArchive, YearQuotes
from smaug.portfolio.domain.securities import (
    RegistrantNamesResolver,
    SiblingCodesResolver,
    confirms_name,
    no_names,
    no_siblings,
    share_class_suffix,
)
from smaug.shared.logging import get_logger

logger = get_logger(__name__)

ListedSinceResolver = Callable[[str], date | None]

# The dated share-base moves of one ticker (``SharesReader.restatement_timeline``),
# taken as a callable so the price side can read it without depending on the whole
# share port — and so the composition root can build it after the tape reader that
# feeds it.
TimelineReader = Callable[[str], Awaitable[Sequence[RestatementStep]]]

# COTAHIST's first published annual archive. A lower FCA listing date is not a
# reason to ask for files that cannot exist; it is clamped to this source floor.
_FIRST_COTAHIST_YEAR = 1986

# How far back the tape may be asked to name a code the cadastre cannot. Each
# hop is a rename, and no security in the window has had more than two.
_MAX_TAPE_HOPS = 4


def _especi_class(especi: str) -> str | None:
    """Return B3's explicit species/class token when the tape supplied one."""
    fields = especi.strip().upper().split()
    return fields[0] if fields else None


def _unknown_listed_since(ticker: str) -> date | None:
    return None


class CodeSuccession:
    """The chain of codes one security has traded under, over a run's years."""

    def __init__(
        self,
        archive: CotahistArchive,
        *,
        siblings: SiblingCodesResolver = no_siblings,
        names: RegistrantNamesResolver = no_names,
        listed_since: ListedSinceResolver = _unknown_listed_since,
        today: Callable[[], date] = date.today,
    ) -> None:
        self._archive = archive
        self._siblings = siblings
        self._names = names
        self._listed_since = listed_since
        self._today = today
        self._chains: dict[str, tuple[int, tuple[CodeWindow, ...]]] = {}
        self._current_codes: dict[str, str | None] = {}
        self._windows: dict[tuple[str, int], CodeWindow | None] = {}
        self._years: dict[int, Mapping[str, YearQuotes]] = {}
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
        quotes_by_code = await self._year(year)
        merged: list[SessionClose] = []
        for window in codes:
            quotes = quotes_by_code.get(window.code)
            if quotes is not None:
                merged.extend(quotes.session_closes())
        return tuple(sorted(merged, key=lambda close: close.session))

    async def current_code(self, ticker: str) -> str | None:
        """Return the cached current successor, resolving it once per code."""
        code = ticker.strip().upper()
        if code in self._current_codes:
            return self._current_codes[code]
        resolved = await self._resolve_current_code(ticker)
        self._current_codes[code] = resolved
        return resolved

    async def _resolve_current_code(self, ticker: str) -> str | None:
        """Return the code carrying the latest B3 session for this security.

        The historical chain is walked backwards from the requested code, which
        is the right direction when reading a closed year.  A live quote can be
        requested by a code that was just retired, however, so this answer walks
        the same FCA sibling set forwards.  A forward edge still needs the B3
        seam to be adjacent and price-continuous; a registrant or root match on
        its own never supplies a current quote.

        Only plain share classes enter this path.  Units and other instruments
        have no class suffix that can safely be compared, and two simultaneous
        classes must never lend one another a quote.
        """
        code = ticker.strip().upper()
        suffix = share_class_suffix(code)
        if suffix is None:
            return None
        sibling_codes = {
            sibling.strip().upper()
            for sibling in self._siblings(code)
            if share_class_suffix(sibling.strip().upper()) == suffix
        }
        latest_year = self._today().year
        latest_quotes = await self._year(latest_year)
        latest_candidates = {
            candidate
            for candidate in (code, *sorted(sibling_codes))
            if candidate in latest_quotes
        }
        if not latest_candidates:
            return None
        if latest_candidates == {code}:
            return code

        # If no FCA listing floor is available, the tape still gives us a
        # narrow lower bound: the archive containing the B3 session immediately
        # before a current-year candidate.  This is evidence from the seam, not
        # a fixed lookback window.
        boundary_years = [latest_year]
        for candidate in sorted(latest_candidates):
            before = await self._preceding_session(
                latest_quotes[candidate].first_close().session
            )
            if before is not None:
                boundary_years.append(before.year)
        opened = self._window_start(code, min(boundary_years), ticker=ticker)
        listed_since = self._listed_since(ticker)
        windows: list[CodeWindow] = []
        for candidate in (code, *sorted(sibling_codes)):
            window = (
                await self._year_window(candidate, latest_year, latest_quotes)
                if candidate in latest_candidates
                else await self._window(candidate, opened)
            )
            if window is not None and (
                candidate == code
                or listed_since is None
                or window.last_session >= listed_since
            ):
                windows.append(window)
        served = next((window for window in windows if window.code == code), None)
        if served is None:
            return None

        path = [served]
        seen = {served.code}
        while True:
            successors = [
                candidate
                for candidate in windows
                if candidate.code not in seen and joins(path[-1], candidate)
            ]
            if len(successors) != 1:
                # No edge is the normal endpoint. More than one is ambiguous:
                # choosing by ticker, root, name, or quote would be an unproven
                # identity change.
                if len(successors) > 1:
                    return None
                break
            successor = successors[0]
            path.append(successor)
            seen.add(successor.code)

        latest = [
            (quotes.last_session, window.code)
            for window in path
            if (quotes := latest_quotes.get(window.code)) is not None
        ]
        if not latest:
            return None
        latest_session = max(session for session, _ in latest)
        codes = {code for session, code in latest if session == latest_session}
        return next(iter(codes)) if len(codes) == 1 else None

    async def code_for_session(
        self, codes: Sequence[CodeWindow], year: int, session: date
    ) -> str | None:
        """Return the code whose COTAHIST row supplied ``session``'s close."""
        quotes_by_code = await self._year(year)
        matches = [
            window.code
            for window in codes
            if (
                (quotes := quotes_by_code.get(window.code)) is not None
                and any(close.session == session for close in quotes.session_closes())
            )
        ]
        return matches[0] if len(matches) == 1 else None

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
        opened = self._window_start(code, since, ticker=ticker)
        cached = self._chains.get(code)
        if cached is not None and cached[0] <= opened:
            return cached[1]
        # Start at the earliest evidence available for this security: the FCA
        # listing floor, an already cached wider window, or the requested year.
        # Never widen this by an arbitrary number of years.
        served = await self._window(code, opened)
        if served is None:
            resolved: tuple[CodeWindow, ...] = ()
        else:
            siblings = [
                window
                for sibling in self._siblings(code)
                if (window := await self._window(sibling, opened)) is not None
            ]
            floor = self._listed_since(ticker)
            resolved = candidates_of(served, siblings, listed_since=floor)
            for _hop in range(_MAX_TAPE_HOPS):
                # The cadastre stops at 2018 (``FIRST_YEAR_WITH_TRADING_CODES``),
                # so a code retired before it is named by nothing — and the walk
                # above simply ends. The tape can still propose one (#198).
                found = await self._tape_predecessor(code, resolved[0], opened)
                if found is None:
                    break
                siblings.append(found)
                resolved = candidates_of(served, siblings, listed_since=floor)
            if len(resolved) > 1:
                logger.info(
                    "%s has traded as %s since %d (#193)",
                    code,
                    " -> ".join(window.code for window in resolved),
                    since,
                )
        self._chains[code] = (opened, resolved)
        return resolved

    async def _tape_predecessor(
        self, ticker: str, head: CodeWindow, since: int
    ) -> CodeWindow | None:
        """The code that stopped where ``head`` started, if the tape names one.

        A rename leaves a signature the cadastre is not needed to read: one code
        prints its last session, and on the very next one another starts, at the
        same price. Over the whole window that signature fires 65 times and is
        right nearly always — but "nearly" is what #190 is about, so it is only
        ever a *proposal*. Two independent records have to agree (ADR 0044):

        * the **tape**, which must offer a candidate of the same class stopping on
          the session immediately before, whose price carries over;
        * **CVM's cadastre**, which must have filed this registrant under the name
          B3 printed beside that candidate at some point. Brookfield's BISA3
          stopped the session before Celpa's CELP3 began, 8 characters and 18%
          apart — and Celpa was never called Brookfield.

        The two witnesses are also what disambiguates. Codes retire on the same
        day by coincidence — Melhoramentos' MSPA3 printed its last session on the
        one before Engie's EGIE3 opened, alongside Tractebel's TBLE3 — so
        uniqueness is required of the *survivors*, not of the proposals.
        """
        before = await self._preceding_session(head.first_session)
        if before is None:
            return None
        head_quotes = (await self._year(head.first_session.year)).get(head.code)
        head_identity = (
            None if head_quotes is None else head_quotes.identity_at(head.first_session)
        )
        wanted = None if head_identity is None else _especi_class(head_identity.especi)
        proposed: set[str] = set()
        for year in {before.year, head.first_session.year}:
            for code, quotes in (await self._year(year)).items():
                if quotes.last_session != before or code == head.code:
                    continue
                candidate_identity = quotes.identity_at(before)
                candidate_class = (
                    None
                    if candidate_identity is None
                    else _especi_class(candidate_identity.especi)
                )
                if (
                    wanted is not None
                    and candidate_class is not None
                    and candidate_class == wanted
                ) or (
                    wanted is None
                    and share_class_suffix(code) == share_class_suffix(head.code)
                ):
                    proposed.add(code)
        filed = self._names(ticker)
        survivors: list[tuple[CodeWindow, str]] = []
        for code in sorted(proposed):
            # The preceding B3 session is the evidence that bounds this extra
            # archive when no FCA listing floor is available.
            candidate = await self._window(code, min(since, before.year))
            if candidate is None or not crosses(candidate.last_close, head.first_close):
                continue
            printed = await self._tape_name(candidate)
            if printed and confirms_name(filed, printed):
                survivors.append((candidate, printed))
        if len(survivors) != 1:
            return None
        candidate, printed = survivors[0]
        logger.info(
            "%s: the tape puts %s before %s and CVM filed as %r (#198)",
            ticker,
            candidate.code,
            head.code,
            printed,
        )
        return candidate

    async def _tape_name(self, window: CodeWindow) -> str:
        quotes = (await self._year(window.last_session.year)).get(window.code)
        if quotes is None:
            return ""
        identity = quotes.identity_at(window.last_session)
        return "" if identity is None else identity.name

    async def _preceding_session(
        self, session: date, *, minimum_year: int | None = None
    ) -> date | None:
        """The trading session before ``session``, across the year boundary."""
        for year in (session.year, session.year - 1):
            if minimum_year is not None and year < minimum_year:
                continue
            calendar = await self._calendar(year)
            earlier = [day for day in calendar if day < session]
            if earlier:
                return earlier[-1]
        return None

    async def _following_session(self, session: date) -> date | None:
        """The trading session after ``session``, across the year boundary."""
        years = (
            (session.year,)
            if session.year >= self._today().year
            else (
                session.year,
                session.year + 1,
            )
        )
        for year in years:
            calendar = await self._calendar(year)
            later = [day for day in calendar if day > session]
            if later:
                return later[0]
        return None

    def _window_start(self, code: str, since: int, *, ticker: str | None = None) -> int:
        """Choose the earliest archive year justified by available evidence."""
        opened = max(_FIRST_COTAHIST_YEAR, since)
        listed_since = self._listed_since(ticker or code)
        if listed_since is not None:
            opened = min(opened, max(_FIRST_COTAHIST_YEAR, listed_since.year))
        cached = self._chains.get(code.strip().upper())
        if cached is not None:
            opened = min(opened, cached[0])
        return opened

    async def _window(self, code: str, since: int) -> CodeWindow | None:
        """Where ``code``'s series starts and ends inside the readable years."""
        normalized = code.strip().upper()
        opened = max(_FIRST_COTAHIST_YEAR, since)
        cache_key = (normalized, opened)
        if cache_key in self._windows:
            return self._windows[cache_key]
        first: SessionClose | None = None
        last: SessionClose | None = None
        for year in range(opened, self._today().year + 1):
            quotes = (await self._year(year)).get(normalized)
            if quotes is None:
                continue
            if first is None:
                first = quotes.first_close()
            last = SessionClose(session=quotes.last_session, close=quotes.last_close)
        if first is None or last is None:
            self._windows[cache_key] = None
            return None
        window = CodeWindow(
            code=normalized,
            first_session=first.session,
            last_session=last.session,
            first_close=first.close,
            last_close=last.close,
            # Do not look below the evidence bound merely to populate a
            # witness that cannot affect a forward seam.  The successor's
            # previous session (and this window's following session) still
            # prove the join exactly, without reopening an older archive.
            previous_session=await self._preceding_session(
                first.session, minimum_year=opened
            ),
            next_session=await self._following_session(last.session),
        )
        self._windows[cache_key] = window
        return window

    async def _year_window(
        self,
        code: str,
        year: int,
        quotes_by_code: Mapping[str, YearQuotes],
    ) -> CodeWindow | None:
        """Build a current-year window without merging older code lifetimes."""
        quotes = quotes_by_code.get(code.strip().upper())
        if quotes is None:
            return None
        first = quotes.first_close()
        return CodeWindow(
            code=code.strip().upper(),
            first_session=first.session,
            last_session=quotes.last_session,
            first_close=first.close,
            last_close=quotes.last_close,
            previous_session=await self._preceding_session(first.session),
            next_session=await self._following_session(quotes.last_session),
        )

    async def _calendar(self, year: int) -> tuple[date, ...]:
        """The year's trading sessions, taken from B3's complete spot tape.

        The union is used instead of a single busiest code: an illiquid code's
        seam may be absent from the busiest series while still present in B3's
        spot records. It is reduced once and shared by every ticker of the run.
        """
        cached = self._calendars.get(year)
        if cached is not None:
            return cached
        quotes = await self._year(year)
        calendar = tuple(
            sorted(
                {
                    close.session
                    for year_quotes in quotes.values()
                    for close in year_quotes.session_closes()
                }
            )
        )
        self._calendars[year] = calendar
        return calendar

    async def _year(self, year: int) -> Mapping[str, YearQuotes]:
        """Read one reduced archive at most once through this succession."""
        cached = self._years.get(year)
        if cached is not None:
            return cached
        value = await self._archive.year(year)
        self._years[year] = value
        return value


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
        original = await self._inner.get(ticker)
        # B3 already proved the requested code and session.  This is the normal
        # path: do not resolve a historical chain (or ask for another archive)
        # just to rediscover an observation whose provenance is complete.
        if (
            original.price is not None
            and original.price_source_code is not None
            and original.price_source_session is not None
        ):
            return original
        current = await self._succession.current_code(ticker)
        if current is None or current == ticker.strip().upper():
            return original
        successor = await self._inner.get(current)
        if successor.price is None:
            # The succession is evidence about identity, not a second price
            # source. Keep the source's own null when it cannot serve the chosen
            # B3 code.
            return original
        logger.info(
            "%s current quote follows proven B3 succession to %s (#270)",
            ticker,
            current,
        )
        return successor

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
        last = sessions[-1]
        closing_code = await self._succession.code_for_session(
            resolved, year, last.session
        )
        return YearPrices(
            nominal_avg=total / Decimal(len(sessions)),
            closing=last.close,
            closing_session=last.session,
            closing_code=closing_code,
        )

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
