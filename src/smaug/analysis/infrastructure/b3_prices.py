"""Prices from B3's own published quote series (COTAHIST).

The exchange publishes the complete price history of everything it lists, one
ZIP per year since 1986, free and unauthenticated. That single file answers both
questions the analysis asks — what a share averaged over a closed year, and what
it last closed at — so the two price sources this replaces (Yahoo for the year
history, brapi behind it) collapse into one, and the one that remains is the
exchange itself rather than an undocumented vendor back-end.

Two properties of the file drive the design here:

**It is a reduction, not a document.** The 2025 archive is 748 MB of text and
almost all of it is options. Everything the analysis wants from a year is, per
trading code, the mean of the daily closes and the last one — so the file is
streamed once and collapsed into ~26k entries, and that reduction is what gets
cached. Keeping the parsed file in memory would be holding a library open to
read one page of each book.

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
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

import httpx

from smaug.analysis.domain.financials import MarketData, YearPrices
from smaug.shared.download import Sleeper, download_zip
from smaug.shared.logging import get_logger

logger = get_logger(__name__)

B3_SERIES_BASE_URL = "https://bvmf.bmfbovespa.com.br/InstDados/SerHist"

# COTAHIST is a fixed-width record of 245 bytes, latin-1, documented by B3's
# "Layout do arquivo - Cotações Históricas". Only the fields below are read;
# offsets are 0-based half-open slices and were validated against Yahoo (PETR4
# 2015: 246 sessions averaging 9.7980 there, 9.80 here).
_TIPREG = slice(0, 2)  # record type: "01" is a quote line
_DATA = slice(2, 10)  # session date, YYYYMMDD
_CODNEG = slice(12, 24)  # trading code, space-padded
_TPMERC = slice(24, 27)  # market type: "010" is the spot market
_PREULT = slice(108, 121)  # closing price, 11 integer + 2 decimal digits

_QUOTE_RECORD = "01"
_SPOT_MARKET = "010"

# Prices carry two implied decimals, as every monetary field in the layout does.
_PRICE_SCALE = Decimal(100)

# The reduction's on-disk format. Bumped when the shape below changes, so a
# stale cache is rebuilt rather than misread.
_REDUCTION_VERSION = 1


@dataclass(frozen=True, slots=True)
class YearQuotes:
    """What one trading code did over one year, reduced to what is asked of it."""

    sessions: int
    average: Decimal
    last_session: date
    last_close: Decimal


def _decimal(raw: str) -> Decimal | None:
    try:
        return Decimal(raw) / _PRICE_SCALE
    except InvalidOperation:
        return None


def _session_date(raw: str) -> date | None:
    try:
        return date(int(raw[0:4]), int(raw[4:6]), int(raw[6:8]))
    except ValueError:
        return None


@dataclass(slots=True)
class _Accumulator:
    """One code's running totals while the archive streams past. Mutable by design."""

    total: Decimal
    sessions: int
    last_session: date
    last_close: Decimal

    def add(self, close: Decimal, session: date) -> None:
        self.total += close
        self.sessions += 1
        if session >= self.last_session:
            self.last_session = session
            self.last_close = close

    def freeze(self) -> YearQuotes:
        return YearQuotes(
            sessions=self.sessions,
            average=self.total / self.sessions,
            last_session=self.last_session,
            last_close=self.last_close,
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
                close = _decimal(line[_PREULT])
                session = _session_date(line[_DATA])
                if close is None or session is None:
                    continue
                code = line[_CODNEG].strip()
                entry = totals.get(code)
                if entry is None:
                    totals[code] = _Accumulator(close, 1, session, close)
                else:
                    entry.add(close, session)
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
            await download_zip(self._http, url, archive, sleep=self._sleep)

        reduction = await asyncio.to_thread(_reduce, archive)
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
    """A ``PriceHistoryProvider`` reading the closed-year average off COTAHIST."""

    def __init__(self, archive: CotahistArchive) -> None:
        self._archive = archive

    async def year_prices(self, ticker: str, year: int) -> YearPrices:
        quotes = (await self._archive.year(year)).get(ticker.strip().upper())
        if quotes is None:
            # The code has no session in the exchange's own record for that year.
            # Reported as a plain gap, not as a symbol the source rejected: B3
            # does not reject symbols, it simply has nothing where nothing traded.
            return YearPrices()
        # ``adjusted_avg`` stays null until the corporate-event series lands: the
        # published file carries the traded price and no dividend adjustment, and
        # the valuation multiples divide by the traded price anyway (ADR 0018).
        return YearPrices(nominal_avg=quotes.average)


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
            return MarketData()
        return MarketData(price=quotes.last_close)


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
