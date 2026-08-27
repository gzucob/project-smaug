"""Prices from B3's own published quote series (COTAHIST).

The exchange publishes the complete price history of everything it lists, one
ZIP per year since 1986, free and unauthenticated. That single file answers both
questions the analysis asks — what a share averaged over a closed year, and what
it last closed at — so the two price sources this replaces (Yahoo for the year
history, brapi behind it) collapse into one, and the one that remains is the
exchange itself rather than an undocumented vendor back-end.

Two properties of the file drive the design here:

**It is a reduction, not a document.** The 2025 archive is 748 MB of text and
almost all of it is options. What the analysis wants from a year is, per trading
code, the mean of the daily closes, the last one, and the closes themselves — so
the file is streamed once and collapsed onto the ~2.3k codes that trade on the
spot market, and that reduction is what gets cached. Keeping the parsed file in
memory would be holding a library open to read one page of each book.

The daily closes survive the reduction because a corporate action lands on a
*day*: restating a year's average is not the same operation as restating each
session and averaging (ADR 0033). They cost little — a year is ~336k spot
records, not the tens of millions the archive's size suggests — and they are
kept encoded, one short string per code, so that a whole run holding a decade of
years in memory carries megabytes rather than hundreds of them.

**A price is not always quoted per share.** The layout's `FATCOT` says how many
shares one quote covers — 1 for almost everything, but 1000 for a share still
quoted by the lot (CEGR3 and NORD3 in 2015), and up to 1000000 for a code coming
out of a restructuring (AZUL53 in 2026). It varies *within* a code's year, so it
is divided out record by record. Reading it as 1 is not a rounding error: it is
a price off by three orders of magnitude, and every multiple built on that price
with it.

**A code has no record on a day it did not trade.** An illiquid share is absent
from the sessions nobody bought it in, and absent from a whole year it never
traded — which is a fact about the market, not a gap in the source. This is what
Yahoo could not distinguish: TAEE4 has been *listed* since 2006 and B3's own file
shows it did not trade a single session in 2015, so Yahoo's missing series was
faithful. A year with no record therefore yields a plain null here; naming that
cause precisely is #164's job, not this reader's.

The current year's archive is republished every session (verified: it carried the
same day's closes), so it is re-downloaded when the cached copy predates today
and cached outright once the year is closed and can no longer change.
"""

from __future__ import annotations

import asyncio
import io
import json
import zipfile
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

import httpx

from smaug.analysis.domain.capital import BaseChange
from smaug.analysis.domain.financials import MarketData, SessionClose, YearPrices
from smaug.analysis.domain.indicators import NullReason
from smaug.analysis.domain.succession import crosses
from smaug.shared.download import Sleeper, download_zip
from smaug.shared.errors import (
    CvmDownloadError,
    SourceError,
    SourceMalformedError,
    SourceTimeoutError,
)
from smaug.shared.logging import get_logger

logger = get_logger(__name__)

B3_SERIES_BASE_URL = "https://bvmf.bmfbovespa.com.br/InstDados/SerHist"

# COTAHIST is a fixed-width record of 245 bytes, latin-1, documented by B3's
# "Layout do arquivo - Cotações Históricas". Only the fields below are read;
# offsets are 0-based half-open slices and were validated against Yahoo (PETR4
# 2015: 246 sessions averaging 9.7980 there, 9.80 here).
_TIPREG = slice(0, 2)  # record type: "01" is a quote line
_DATA = slice(2, 10)  # session date, YYYYMMDD
_CODBDI = slice(10, 12)  # BDI market/security code
_CODNEG = slice(12, 24)  # trading code, space-padded
_NOMRES = slice(27, 39)  # B3's own abbreviation of the issuer, 12 wide
_ESPECI = slice(39, 49)  # complete security species/class specification
_TPMERC = slice(24, 27)  # market type: "010" is the spot market
_PREULT = slice(108, 121)  # closing price, 11 integer + 2 decimal digits
_FATCOT = slice(210, 217)  # shares per quote: "1" unitary, "1000" per lot of a thousand
_CODISI = slice(230, 242)  # ISIN, space-padded
_DISMES = slice(242, 245)  # distribution number: the paper's current rights state
# ESPECI's marker is the token after its four-character species/class prefix.
_MARKER = slice(43, 47)

_QUOTE_RECORD = "01"
_SPOT_MARKET = "010"

# A ticker and the earliest year being read -> every code that answers for it,
# oldest first (``analysis.infrastructure.succession``). Taken as a callable
# rather than as the class itself: the succession reads this module, and the
# dependency only runs the other way at composition time.
CodesResolver = Callable[[str, int], Awaitable[tuple[str, ...]]]

# Prices carry two implied decimals, as every monetary field in the layout does.
_PRICE_SCALE = Decimal(100)

# The reduction's on-disk format. Bumped when the shape below changes, so a
# stale cache is rebuilt rather than misread. v2 added the daily closes; v3
# divides by the quote factor, which the centavo encoding could not represent;
# v4 keeps the sessions where the paper's rights state changed; v5 keeps the
# issuer name B3 prints beside the code, which is what confirms a retired
# code against the names its registrant filed (#198); v6 keeps B3's complete
# identity evidence (CODISI, ESPECI and CODBDI) alongside it; v7 retains identity
# transitions by session instead of collapsing a code-year to its first row.
_REDUCTION_VERSION = 7

# A marker with no class attached, used where the field is blank so that the
# encoded triples stay whitespace-separated.
_NO_MARKER = "-"

# The letters that name an event handing the holder a different number of shares.
_BASE_CHANGE_LETTERS = frozenset("BG")


@dataclass(frozen=True, slots=True)
class RightsState:
    """One session and the rights state the paper carried into it.

    ``distribution`` is COTAHIST's ``DISMES``, which the layout defines as the
    paper's "número de seqüência correspondente ao estado de direito vigente" —
    B3's own statement that this field, and not the price, is what says the
    rights attached to the share changed. ``marker`` is the token ESPECI carries
    beside the class on the sessions following that change (``EB``, ``EG``,
    ``ED``…). B3 documents neither the marker's vocabulary nor its values, so it
    is kept as filed and read in one place (``_is_base_change``).
    """

    session: date
    distribution: str
    marker: str


@dataclass(frozen=True, slots=True)
class IdentityState:
    """B3 identity evidence in force for a code on one published session."""

    session: date
    isin: str
    especi: str
    bdi: str
    name: str


@dataclass(frozen=True, slots=True)
class YearQuotes:
    """What one trading code did over one year, reduced to what is asked of it.

    ``closes`` is the year's sessions encoded as ``"<day> <price>"`` pairs, the
    day counted from the 1st of January — a compact string rather than a parsed
    series because most codes are never asked for theirs, and a decade of years
    parsed up front would be tens of millions of objects held for the handful
    that get read. The price is written out rather than kept as an integer of
    centavos because a lot-quoted code has a price finer than a centavo
    (CEGR3 closed 2015 at R$0.09508 a share).

    ``rights`` is the year's *rights states*, encoded the same way as
    ``"<day> <distribution> <marker>"`` triples: the first session of the year,
    plus every session where either half of that pair changed. Holding the first
    session too is what lets a reader join one year to the next — an action on
    2 January (ITUB4 in 2026) shows up as a change only against December's
    state, which lives in the previous year's file. The marker is kept on its own
    changes, and not only on the distribution's, because it is *sticky*: it runs
    for about eight sessions after the event, so knowing whether a given session
    introduced it means knowing what stood immediately before.

    ``isin``, ``especi`` and ``bdi`` retain the first identity values for
    compatibility, while ``identities`` records every subsequent identity
    transition as ``[day, isin, especi, bdi, name]`` JSON rows. They are evidence
    for diagnostics and security resolution, not independent keys; ``code``
    remains the reduction's map key. The marker used by ``rights`` is still
    derived from the complete ``especi`` field rather than replacing it.
    """

    sessions: int
    average: Decimal
    last_session: date
    last_close: Decimal
    closes: str = ""
    rights: str = ""
    name: str = ""
    isin: str = ""
    especi: str = ""
    bdi: str = ""
    identities: str = ""

    @property
    def codisi(self) -> str:
        """The source-named alias for the retained ISIN evidence."""
        return self.isin

    @property
    def isin_code(self) -> str:
        """The domain-style alias for the retained ISIN evidence."""
        return self.isin

    @property
    def codbdi(self) -> str:
        """The source-named alias for the retained BDI evidence."""
        return self.bdi

    @property
    def bdi_code(self) -> str:
        """The descriptive alias for the retained BDI evidence."""
        return self.bdi

    def identity_states(self) -> tuple[IdentityState, ...]:
        """Decode identity transitions, oldest first."""
        if not self.identities:
            return (
                IdentityState(
                    session=self.first_close().session,
                    isin=self.isin,
                    especi=self.especi,
                    bdi=self.bdi,
                    name=self.name,
                ),
            )
        january = date(self.last_session.year, 1, 1).toordinal()
        try:
            rows = json.loads(self.identities)
            return tuple(
                IdentityState(
                    session=date.fromordinal(january + int(row[0])),
                    isin=str(row[1]),
                    especi=str(row[2]),
                    bdi=str(row[3]),
                    name=str(row[4]),
                )
                for row in rows
            )
        except (IndexError, TypeError, ValueError, json.JSONDecodeError):
            return ()

    def identity_at(self, session: date) -> IdentityState | None:
        """Return the identity evidence in force on ``session``."""
        states = self.identity_states()
        for state in reversed(states):
            if state.session <= session:
                return state
        return None

    def rights_states(self) -> tuple[RightsState, ...]:
        """The year's rights states, decoded — oldest first."""
        january = date(self.last_session.year, 1, 1).toordinal()
        fields = self.rights.split()
        return tuple(
            RightsState(
                session=date.fromordinal(january + int(day)),
                distribution=distribution,
                marker="" if marker == _NO_MARKER else marker,
            )
            for day, distribution, marker in zip(
                fields[::3], fields[1::3], fields[2::3], strict=False
            )
        )

    def first_close(self) -> SessionClose:
        """The year's first session, decoded without decoding the rest.

        The succession asks every candidate code where its series starts, and for
        the ~2.3k codes that never changed name the answer is thrown away — so it
        reads the one pair it needs off the front of the string rather than
        building the year.
        """
        if not self.closes:
            return SessionClose(session=self.last_session, close=self.last_close)
        january = date(self.last_session.year, 1, 1).toordinal()
        day, price = self.closes.split(" ", 2)[:2]
        return SessionClose(
            session=date.fromordinal(january + int(day)), close=Decimal(price)
        )

    def session_closes(self) -> tuple[SessionClose, ...]:
        """The year's daily closes, decoded — as traded, oldest first."""
        january = date(self.last_session.year, 1, 1).toordinal()
        fields = self.closes.split()
        return tuple(
            SessionClose(
                session=date.fromordinal(january + int(day)),
                close=Decimal(price),
            )
            for day, price in zip(fields[::2], fields[1::2], strict=False)
        )


def _cents(raw: str) -> int | None:
    """The price field as its own integer of cents — the layout's implied scale."""
    try:
        return int(raw)
    except ValueError:
        return None


def _quote_factor(raw: str) -> int | None:
    """How many shares one quote covers, or ``None`` when the field is unusable.

    Never defaulted to 1: a record whose factor cannot be read is a record whose
    price cannot be scaled, and dropping the session is honest where assuming
    the common case would silently multiply it by a thousand.
    """
    try:
        factor = int(raw)
    except ValueError:
        return None
    return factor if factor > 0 else None


def _session_date(raw: str) -> date | None:
    try:
        return date(int(raw[0:4]), int(raw[4:6]), int(raw[6:8]))
    except ValueError:
        return None


@dataclass(slots=True)
class _Accumulator:
    """One code's sessions while the archive streams past. Mutable by design.

    Sessions are held as (ordinal, cents, factor) integers rather than as dates
    and decimals: the archive yields hundreds of thousands of them and only a
    few hundred codes are ever asked for their series, so the decimals are built
    once per code in ``freeze`` rather than once per line while streaming.
    """

    ordinals: list[int]
    cents: list[int]
    factors: list[int]
    rights: list[str]
    markers: list[str]
    name: str = ""
    isin: str = ""
    especi: str = ""
    bdi: str = ""
    names: list[str] = field(default_factory=list)
    isins: list[str] = field(default_factory=list)
    especis: list[str] = field(default_factory=list)
    bdis: list[str] = field(default_factory=list)

    def add(
        self,
        cents: int,
        factor: int,
        ordinal: int,
        rights: str,
        marker: str,
        name: str,
        isin: str,
        especi: str,
        bdi: str,
    ) -> None:
        self.ordinals.append(ordinal)
        self.cents.append(cents)
        self.factors.append(factor)
        self.rights.append(rights)
        self.markers.append(marker)
        self.names.append(name)
        self.isins.append(isin)
        self.especis.append(especi)
        self.bdis.append(bdi)

    def freeze(self) -> YearQuotes:
        """The year reduced, with its sessions put back in date order.

        The archive is *usually* ordered by session and the 2025 file is not, so
        the order is established here rather than inherited: ``closes`` promises
        oldest first, and the last close has to be the year's last rather than
        the file's last.
        """
        series = sorted(
            zip(
                self.ordinals,
                self.cents,
                self.factors,
                self.rights,
                self.markers,
                self.names,
                self.isins,
                self.especis,
                self.bdis,
                strict=True,
            )
        )
        prices = [
            Decimal(cents) / (_PRICE_SCALE * factor) for _, cents, factor, *_ in series
        ]
        last_session = date.fromordinal(series[-1][0])
        january = date(last_session.year, 1, 1).toordinal()
        states = [
            f"{row[0] - january} {row[3]} {row[4] or _NO_MARKER}"
            for index, row in enumerate(series)
            if index == 0 or row[3:5] != series[index - 1][3:5]
        ]
        identities = []
        for index, (ordinal, _, _, _, _, name, isin, especi, bdi) in enumerate(series):
            if index == 0 or (name, isin, especi, bdi) != series[index - 1][5:9]:
                identities.append([ordinal - january, isin, especi, bdi, name])
        return YearQuotes(
            sessions=len(prices),
            average=sum(prices, Decimal(0)) / len(prices),
            last_session=last_session,
            last_close=prices[-1],
            closes=" ".join(
                f"{row[0] - january} {price}"
                for row, price in zip(series, prices, strict=True)
            ),
            rights=" ".join(states),
            name=self.name,
            isin=self.isin,
            especi=self.especi,
            bdi=self.bdi,
            identities=json.dumps(identities, separators=(",", ":")),
        )


def _reduce(archive_path: Path) -> dict[str, YearQuotes]:
    """Stream one COTAHIST archive and collapse it per trading code (sync).

    Runs in a worker thread: it is CPU- and IO-bound over a file that reaches
    748 MB, and the accumulator it returns is a few thousand entries.
    """
    totals: dict[str, _Accumulator] = {}
    with zipfile.ZipFile(archive_path) as archive:
        members = [n for n in archive.namelist() if n.upper().endswith(".TXT")]
        if not members:
            raise ValueError(f"{archive_path.name} carries no .TXT member")
        with archive.open(members[0]) as raw:
            stream = io.TextIOWrapper(raw, encoding="latin-1", newline="")
            for line in stream:
                if line[_TIPREG] != _QUOTE_RECORD or line[_TPMERC] != _SPOT_MARKET:
                    continue
                if len(line.rstrip("\r\n")) < 245:
                    raise ValueError("short COTAHIST spot-market record")
                cents = _cents(line[_PREULT])
                factor = _quote_factor(line[_FATCOT])
                session = _session_date(line[_DATA])
                if cents is None or factor is None or session is None:
                    raise ValueError("invalid COTAHIST spot-market record")
                ordinal = session.toordinal()
                code = line[_CODNEG].strip()
                rights = line[_DISMES].strip()
                marker = line[_MARKER].strip()
                entry = totals.get(code)
                if entry is None:
                    name = line[_NOMRES].strip()
                    isin = line[_CODISI].strip()
                    especi = line[_ESPECI].strip()
                    bdi = line[_CODBDI].strip()
                    totals[code] = _Accumulator(
                        ordinals=[ordinal],
                        cents=[cents],
                        factors=[factor],
                        rights=[rights],
                        markers=[marker],
                        name=name,
                        isin=isin,
                        especi=especi,
                        bdi=bdi,
                        names=[name],
                        isins=[isin],
                        especis=[especi],
                        bdis=[bdi],
                    )
                else:
                    entry.add(
                        cents,
                        factor,
                        ordinal,
                        rights,
                        marker,
                        line[_NOMRES].strip(),
                        line[_CODISI].strip(),
                        line[_ESPECI].strip(),
                        line[_CODBDI].strip(),
                    )
    return {code: entry.freeze() for code, entry in totals.items()}


def _dump(reduction: Mapping[str, YearQuotes], built_on: date) -> str:
    return json.dumps(
        {
            "version": _REDUCTION_VERSION,
            "built_on": built_on.isoformat(),
            "codes": {
                code: [
                    quotes.sessions,
                    str(quotes.average),
                    quotes.last_session.isoformat(),
                    str(quotes.last_close),
                    quotes.closes,
                    quotes.rights,
                    quotes.name,
                    quotes.isin,
                    quotes.especi,
                    quotes.bdi,
                    quotes.identities,
                ]
                for code, quotes in reduction.items()
            },
        }
    )


def _load(text: str) -> tuple[dict[str, YearQuotes], date] | None:
    """Parse a cached reduction, or ``None`` when it is unusable/outdated."""
    try:
        payload = json.loads(text)
        if payload.get("version") != _REDUCTION_VERSION:
            return None
        built_on = date.fromisoformat(payload["built_on"])
        codes = {
            code: YearQuotes(
                sessions=int(row[0]),
                average=Decimal(row[1]),
                last_session=date.fromisoformat(row[2]),
                last_close=Decimal(row[3]),
                closes=row[4],
                rights=row[5],
                name=row[6],
                isin=row[7],
                especi=row[8],
                bdi=row[9],
                identities=row[10],
            )
            for code, row in payload["codes"].items()
        }
    except (ValueError, KeyError, TypeError, InvalidOperation):
        return None
    return codes, built_on


class CotahistArchive:
    """B3's yearly quote series: downloaded once, reduced once, served per code.

    One instance is shared by both providers below and by every ticker of a run,
    so a whole-exchange analysis streams each year's archive at most once. The
    reduction is memoized in memory and persisted beside the ZIP, which is what
    keeps a second run from re-reading gigabytes it has already summarized.
    """

    def __init__(
        self,
        http_client: httpx.AsyncClient,
        *,
        cache_dir: str,
        base_url: str = B3_SERIES_BASE_URL,
        sleep: Sleeper = asyncio.sleep,
        today: Callable[[], date] = date.today,
    ) -> None:
        self._http = http_client
        self._cache_dir = Path(cache_dir)
        self._base_url = base_url.rstrip("/")
        self._sleep = sleep
        self._today = today
        self._years: dict[int, Mapping[str, YearQuotes]] = {}
        self._lock = asyncio.Lock()

    def _zip_name(self, year: int) -> str:
        return f"COTAHIST_A{year}.ZIP"

    def _reduction_path(self, year: int) -> Path:
        return self._cache_dir / f"cotahist_{year}.json"

    async def year(self, year: int) -> Mapping[str, YearQuotes]:
        """Every spot-market code quoted in ``year``, reduced."""
        cached = self._years.get(year)
        if cached is not None:
            return cached
        async with self._lock:
            cached = self._years.get(year)
            if cached is not None:
                return cached
            reduction = await self._resolve(year)
            self._years[year] = reduction
            return reduction

    async def _resolve(self, year: int) -> Mapping[str, YearQuotes]:
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        today = self._today()
        reduction_path = self._reduction_path(year)
        if reduction_path.exists():
            loaded = await asyncio.to_thread(
                lambda: _load(reduction_path.read_text(encoding="utf-8"))
            )
            if loaded is not None and self._is_fresh(loaded[1], year, today):
                logger.info(
                    "B3 %d: %d codes from the cached reduction", year, len(loaded[0])
                )
                return loaded[0]

        archive = self._cache_dir / self._zip_name(year)
        if not archive.exists() or not self._is_fresh(
            date.fromtimestamp(archive.stat().st_mtime), year, today
        ):
            url = f"{self._base_url}/{self._zip_name(year)}"
            logger.info("Downloading B3 quote series %d from %s", year, url)
            try:
                await download_zip(self._http, url, archive, sleep=self._sleep)
            except CvmDownloadError as exc:
                # ``download_zip`` retains the transport exception as its cause;
                # keep a timeout distinct from an unavailable archive for the
                # persisted indicator null reason.
                if isinstance(exc.__cause__, httpx.TimeoutException):
                    raise SourceTimeoutError(
                        f"timed out downloading B3 {self._zip_name(year)}"
                    ) from exc
                raise

        try:
            reduction = await asyncio.to_thread(_reduce, archive)
        except (IndexError, UnicodeError, ValueError, zipfile.BadZipFile) as exc:
            raise SourceMalformedError(
                f"malformed B3 quote archive {archive.name}"
            ) from exc
        await asyncio.to_thread(
            lambda: reduction_path.write_text(_dump(reduction, today), encoding="utf-8")
        )
        logger.info(
            "B3 %d: reduced %s to %d spot-market codes",
            year,
            self._zip_name(year),
            len(reduction),
        )
        return reduction

    def _is_fresh(self, built_on: date, year: int, today: date) -> bool:
        """Whether a cached artifact for ``year`` can still be trusted.

        A closed year is immutable, so any copy of it is current forever. The
        running year is republished every session, so a copy built before today
        is missing sessions and gets replaced.
        """
        if year < today.year:
            return True
        return built_on >= today


class B3PriceHistory:
    """A ``PriceHistoryProvider`` reading year prices off COTAHIST."""

    def __init__(self, archive: CotahistArchive) -> None:
        self._archive = archive

    async def year_prices(self, ticker: str, year: int) -> YearPrices:
        quotes = (await self._archive.year(year)).get(ticker.strip().upper())
        if quotes is None:
            # The code has no session in the exchange's own record for that year.
            # Reported as a plain gap, not as a symbol the source rejected: B3
            # does not reject symbols, it simply has nothing where nothing traded.
            return YearPrices()
        # ``adjusted_avg`` is the *dividend*-adjusted basis, which this file does
        # not carry and the valuation multiples do not want (ADR 0018). The
        # corporate-action basis is put on this average by ``RestatedPriceProvider``,
        # which is the only thing that knows the company's share-base history.
        return YearPrices(
            nominal_avg=quotes.average,
            closing=quotes.last_close,
            closing_session=quotes.last_session,
        )

    async def year_sessions(self, ticker: str, year: int) -> tuple[SessionClose, ...]:
        """Every close the code printed in ``year``, as traded.

        Published alongside the average because the two answer different halves of
        one question: the average is what an indicator divides by, the sessions are
        what a corporate action has to be applied to first (ADR 0033).
        """
        quotes = (await self._archive.year(year)).get(ticker.strip().upper())
        return () if quotes is None else quotes.session_closes()


class B3QuoteProvider:
    """A ``CurrentQuoteProvider`` serving the last close B3 has published.

    Not an intraday quote, and deliberately so: the multiples are computed over a
    twelve-month accounting window, where a fifteen-minute-old price and the last
    session's close are the same number to every decimal that reaches a screen —
    while the close makes a run *reproducible*, which a moving quote never was.

    The last close comes from the running year's archive rather than the latest
    session's, so a share that did not trade today still reports what it last
    traded at. One that has not traded at all this year reports nothing, which is
    the honest answer for a code the market has stopped pricing.
    """

    def __init__(
        self, archive: CotahistArchive, today: Callable[[], date] = date.today
    ) -> None:
        self._archive = archive
        self._today = today

    async def get(self, ticker: str) -> MarketData:
        quotes = (await self._archive.year(self._today().year)).get(
            ticker.strip().upper()
        )
        if quotes is None:
            logger.warning(
                "B3 has no %d session for %s; its price will be null",
                self._today().year,
                ticker,
            )
            return MarketData(price_null_reason=NullReason.MISSING_PRICE)
        return MarketData(price=quotes.last_close)


def _opened_base_change(before: frozenset[str], after: frozenset[str]) -> bool:
    """Whether a rights state introduces an event that moved the share base.

    ``B`` is bonificação and ``G`` grupamento, and B3's tape marks a
    desdobramento as a bonificação too (BBAS3's 2024 split trades ``EB``). Both
    hand the holder a different number of shares, which is exactly what a
    restatement is. The markers combine, so the test is on the letters rather
    than on a list: ``EDB`` is a dividend and a bonus on one session, ``EJB``
    interest and a bonus.

    **Against the span before it, never on its own**, because the marker is
    sticky: it stays up for about eight sessions after the event. Itaúsa's 2022
    bonus went ex on 11 November as ``EB`` and its interest on 21 November as
    ``EJB`` — the ``B`` on the second date belongs to the first, and reading the
    marker alone counts one bonus twice.

    Everything else is deliberately out. ``ED`` and ``EJ`` are cash leaving the
    company — the price drops and no share is created, which is the third basis
    ADR 0018 keeps separate; 253 of them moved the price more than 15% and every
    one would be a false action here. ``ES`` is a subscription, which issues
    shares against new money: a dilution, not a restatement (ADR 0027). ``EX``
    is left out too, at a known cost — VIVT3's 2025 split-and-grupamento carries
    it — because 39 of its 40 occurrences are not composite actions at all, and
    a candidate that is not an action can only steal a date. That case is what
    B3's event feed still answers (ADR 0034).

    B3 publishes no table for these values; the reading is measured, not cited,
    which is why it lives behind one function with the evidence written down.
    """
    return bool(_BASE_CHANGE_LETTERS & set(after) - set(before))


@dataclass(slots=True)
class _Span:
    """One distribution number's run of sessions, and everything its markers said.

    A span, and not a session, because the marker and the rights state do not
    always move together. Itaúsa's December 2025 bonus steps the distribution on
    the 19th under ``EX`` and only says ``EB`` from the 22nd, still on the same
    number — read session by session, the event names itself a day after it is
    dated, and neither session on its own is a base change.

    What it is measured against is the marker standing on the session **before**
    it opened, not the whole span before it. A span can carry a bonus of its own
    and still be followed by another: SLC Agrícola's May and December 2023
    bonuses are consecutive spans, and comparing their unions hides the second.
    """

    distribution: str | None = None
    letters: set[str] = field(default_factory=set)
    previous: frozenset[str] = frozenset()
    opened_on: date | None = None
    before: Decimal | None = None
    at_open: Decimal | None = None
    reported: bool = True  # nothing precedes the first span, so it states nothing

    def opened(
        self,
        state: RightsState,
        *,
        standing: str,
        before: Decimal | None,
        at_open: Decimal,
    ) -> _Span:
        return _Span(
            distribution=state.distribution,
            letters=set(state.marker),
            previous=frozenset(standing),
            opened_on=state.session,
            before=before,
            at_open=at_open,
            reported=self.distribution is None,
        )

    def seen(self, marker: str) -> None:
        self.letters |= set(marker)

    def base_change(self) -> BaseChange | None:
        """The change this span opened with, once its markers have named one."""
        if self.reported or self.opened_on is None:
            return None
        if not _opened_base_change(self.previous, frozenset(self.letters)):
            return None
        self.reported = True
        if self.before is None or self.before <= 0 or self.at_open is None:
            return None
        return BaseChange(self.opened_on, self.before / self.at_open)


class B3BaseChanges:
    """The sessions B3's own tape says a code's share base moved on.

    The price series dates the corporate actions the counts can only place in a
    year (ADR 0035): B3 numbers each paper's rights state and increments it on
    the first session quoted on the new base, so the cut a restatement needs is
    already inside the file the price is read from.

    The ratio published alongside is the **market's** reading — the close before
    over the close after — never a declared one. It exists to identify which
    declared action a session belongs to, not to state its size.
    """

    def __init__(
        self,
        archive: CotahistArchive,
        today: Callable[[], date] = date.today,
        *,
        codes: CodesResolver | None = None,
    ) -> None:
        self._archive = archive
        self._today = today
        self._codes = codes

    async def base_changes(
        self, ticker: str, years: Sequence[int]
    ) -> tuple[BaseChange, ...]:
        """Every base change in ``years``, oldest first.

        A year the exchange cannot have published yet is dropped rather than
        asked for. The caller looks one year past the last filed one, because the
        FRE reports an action late — and the last filed year is the running one
        for a company that has already filed this year, which asks for an archive
        that will not exist until January.

        The years are walked in order so a change on a year's first session is
        seen against December's state, which lives in the previous file. When
        the requested window starts at a year boundary, the previous archive is
        loaded as a lookback if the tape carries a base-change marker. A code
        with no preceding publication remains unreportable on its first session:
        the file cannot tell that apart from a genuinely new listing.

        **The tape walked is the security's, not the code's** (#193): a company
        that renamed its code has its earlier actions filed under the earlier
        code, and a chain that stopped at today's would recover those sessions
        (the price side joins them) without ever restating them. The codes come
        back oldest first and never overlap, so reading them in order is still
        reading one series in date order.
        """
        code = ticker.strip().upper()
        tapes = (
            (code,)
            if self._codes is None
            else await self._codes(code, min(years, default=self._today().year))
        )
        changes: list[BaseChange] = []
        span = _Span()
        december: Decimal | None = None
        standing = ""  # the marker in force on the session last read
        reading = ""  # the code whose tape the last session was read from
        published = self._today().year
        for year in sorted({year for year in years if year <= published}):
            for tape in tapes:
                quotes = (await self._archive.year(year)).get(tape)
                if quotes is None:
                    continue
                series = quotes.session_closes()
                if reading not in ("", tape) and december is not None:
                    seam = _seam(december, series[0])
                    if seam is not None:
                        changes.append(seam)
                if span.distribution is None and december is None and reading == "":
                    opening = await self._opening_evidence(tape, year, quotes, series)
                    if opening is not None:
                        previous_close, previous_state = opening
                        # Seed only the state immediately before the requested
                        # window.  The first state in a genuinely new listing
                        # remains unreportable because there is no predecessor
                        # against which B3's rights transition can be measured.
                        december = previous_close
                        standing = previous_state.marker
                        span = _Span(
                            distribution=previous_state.distribution,
                            reported=False,
                        )
                reading = tape
                position = {close.session: index for index, close in enumerate(series)}
                for state in quotes.rights_states():
                    at = position.get(state.session)
                    if at is None:
                        continue
                    if state.distribution != span.distribution:
                        # Measured across the cut, so the numerator is the session
                        # immediately before it — not the previous *change*, which
                        # can be a year away.
                        before = series[at - 1].close if at > 0 else december
                        span = span.opened(
                            state,
                            standing=standing,
                            before=before,
                            at_open=series[at].close,
                        )
                    else:
                        span.seen(state.marker)
                    standing = state.marker
                    change = span.base_change()
                    if change is not None:
                        changes.append(change)
                december = quotes.last_close
        return tuple(changes)

    async def _opening_evidence(
        self,
        tape: str,
        year: int,
        quotes: YearQuotes,
        series: Sequence[SessionClose],
    ) -> tuple[Decimal, RightsState] | None:
        """Read the same code's last published state before a window starts.

        ``rights`` deliberately stores the first state in every reduction.  When
        the caller asks only for a year whose first session carries a new state,
        that first state needs the last state from the preceding B3 publication
        to be classified.  Looking back one archive is enough at a year boundary;
        no predecessor means a new listing (or a real source gap), never a made-up
        corporate action.
        """
        states = quotes.rights_states()
        if not states or not _BASE_CHANGE_LETTERS.intersection(states[0].marker):
            return None
        try:
            previous = (await self._archive.year(year - 1)).get(tape)
        except SourceError:
            # The lookback is supplementary evidence.  If B3 has not published
            # the prior archive (or it is unavailable), there is no predecessor
            # to prove an action on this opening session.
            logger.info(
                "B3 %d: no prior archive available to evidence %s's opening state",
                year - 1,
                tape,
            )
            return None
        if previous is None or not previous.session_closes():
            return None
        previous_states = previous.rights_states()
        if not previous_states or not series:
            return None
        if previous.last_session >= series[0].session:
            return None
        return previous.last_close, previous_states[-1]


def _seam(before: Decimal, opening: SessionClose) -> BaseChange | None:
    """The base change a change of trading code is the only witness to.

    B3 restarts a new code's rights state from scratch — ``VSTE3``'s first
    session carries a clean ``ESPECI`` and a ``DISMES`` of 100 — so an action
    executed on the day a company renames its code leaves no mark on the tape
    that ADR 0035 reads. The **seam itself** is the mark: the last close under
    the old code against the first under the new one, which is the same
    measurement every other base change here publishes.

    Only where the price did not carry over. A rename on its own moves no share,
    and offering a ratio of ~1 as a candidate would let it be paired with a real
    action of a different date.
    """
    if crosses(before, opening.close) or before <= 0 or opening.close <= 0:
        return None
    return BaseChange(session=opening.session, ratio=before / opening.close, seam=True)


class B3PriceProvider:
    """The single ``PriceProvider``: one archive, both the quote and the history.

    Replaces ``CompositePriceProvider`` + the two fallback chains, which existed
    only because the live quote and the year history came from different vendors
    (ADR 0011/0013). One source serving both needs neither a router nor a chain.
    """

    def __init__(
        self, archive: CotahistArchive, today: Callable[[], date] = date.today
    ) -> None:
        self._quote = B3QuoteProvider(archive, today=today)
        self._history = B3PriceHistory(archive)

    async def get(self, ticker: str) -> MarketData:
        return await self._quote.get(ticker)

    async def year_prices(self, ticker: str, year: int) -> YearPrices:
        return await self._history.year_prices(ticker, year)

    async def year_sessions(self, ticker: str, year: int) -> tuple[SessionClose, ...]:
        return await self._history.year_sessions(ticker, year)
