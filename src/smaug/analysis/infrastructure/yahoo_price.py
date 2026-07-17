"""Closed-year daily price history from Yahoo Finance (history side of Phase 2).

brapi's free plan withholds multi-year daily history for all but its demo
tickers (ADR 0007/0011), so the dividend-adjusted year averages that price the
closed-year view are sourced from Yahoo's public chart endpoint instead. Only
the history is fetched here; the live quote still comes from brapi.

The year is requested by exact window (``period1``/``period2`` timestamps),
never a fixed ``range=Ny`` — so extending coverage to more closed years (future
work) is just a wider window, never a plan/range ceiling. A symbol Yahoo does
not know (a delisted company, say) or a year with no trading simply yields
``YearPrices()`` (null), degrading the multiples without failing the run; only a
transport failure raises, so a mid-run timeout stays loud and distinguishable.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from smaug.analysis.domain.financials import MarketData, YearPrices
from smaug.analysis.domain.indicators import NullReason
from smaug.portfolio.domain.market_symbols import market_symbol
from smaug.shared.errors import BrapiTimeoutError
from smaug.shared.logging import get_logger

logger = get_logger(__name__)

# Yahoo rejects requests without a browser-like User-Agent (403/429 otherwise).
_USER_AGENT = (
    "Mozilla/5.0 (compatible; smaug/1.0; +https://github.com/gzucob/project-smaug)"
)


def _dec(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _avg(values: list[Decimal]) -> Decimal | None:
    return sum(values, Decimal(0)) / Decimal(len(values)) if values else None


def _yahoo_symbol(ticker: str) -> str:
    """Map a B3 ticker to its Yahoo symbol (``PETR4`` → ``PETR4.SA``).

    Goes through the portfolio override map first, so a renamed/delisted ticker
    can point at the symbol Yahoo actually carries its history under (#64).
    """
    return f"{market_symbol(ticker)}.SA"


class YahooQuoteProvider:
    """Fetches a ticker's current price from Yahoo (the live-quote side).

    Yahoo's chart ``meta`` carries ``regularMarketPrice`` without auth, but not
    market cap or share count — the richer ``v7/finance/quote`` endpoint now
    requires a crumb (HTTP 401). So this returns the price only, which is all the
    use case wants: it capitalizes the company itself, summing each listed share
    class at its own quote from CVM's filed counts (ADR 0014). A vendor's
    company-wide cap would be the wrong number anyway. An unresolved symbol or a
    bad response yields ``MarketData()`` (null price); only a transport failure
    raises.
    """

    def __init__(self, base_url: str, http_client: httpx.AsyncClient) -> None:
        self._base_url = base_url.rstrip("/")
        self._http = http_client

    async def get(self, ticker: str) -> MarketData:
        symbol = _yahoo_symbol(ticker)
        url = f"{self._base_url}/v8/finance/chart/{symbol}"
        try:
            response = await self._http.get(
                url,
                params={"range": "1d", "interval": "1d"},
                headers={"User-Agent": _USER_AGENT},
            )
        except httpx.TransportError as exc:
            raise BrapiTimeoutError(
                f"transport failure fetching Yahoo quote for {symbol}: {exc!r}"
            ) from exc

        if response.status_code != httpx.codes.OK:
            logger.warning(
                "Yahoo quote for %s: HTTP %d; price will be null",
                symbol,
                response.status_code,
            )
            return MarketData()

        results = (response.json().get("chart") or {}).get("result") or []
        if not results:
            return MarketData()
        meta = results[0].get("meta") or {}
        return MarketData(price=_dec(meta.get("regularMarketPrice")))


class YahooPriceHistory:
    """Fetches a ticker's daily close / adjusted-close for a fiscal year from Yahoo."""

    def __init__(self, base_url: str, http_client: httpx.AsyncClient) -> None:
        self._base_url = base_url.rstrip("/")
        self._http = http_client

    async def year_prices(self, ticker: str, year: int) -> YearPrices:
        symbol = _yahoo_symbol(ticker)
        url = f"{self._base_url}/v8/finance/chart/{symbol}"
        # Exact-year window by timestamp — no fixed range to raise when history grows.
        period1 = int(datetime(year, 1, 1, tzinfo=UTC).timestamp())
        period2 = int(datetime(year + 1, 1, 1, tzinfo=UTC).timestamp())
        try:
            response = await self._http.get(
                url,
                params={"period1": period1, "period2": period2, "interval": "1d"},
                headers={"User-Agent": _USER_AGENT},
            )
        except httpx.TransportError as exc:
            raise BrapiTimeoutError(
                f"transport failure fetching Yahoo history for {symbol}: {exc!r}"
            ) from exc

        if response.status_code == httpx.codes.NOT_FOUND:
            # Yahoo answers 404 for a symbol it does not know — a delisted/renamed
            # ticker with no override. A *non-transient* null, distinct from a mere
            # gap: report the cause so the chain and ``smaug doctor`` can tell them
            # apart (#64).
            logger.warning(
                "Yahoo history %d for %s: symbol not found (HTTP 404); "
                "delisted/renamed? — add a market-symbol override",
                year,
                symbol,
            )
            return YearPrices(null_reason=NullReason.PRICE_SYMBOL_NOT_FOUND)

        if response.status_code != httpx.codes.OK:
            # A transient/bad window: expected null, not a crash. No reason recorded,
            # so the chain treats it as a gap this source could not fill.
            logger.warning(
                "Yahoo history %d for %s: HTTP %d; year multiples will be null",
                year,
                symbol,
                response.status_code,
            )
            return YearPrices()

        return _parse(response.json(), year)


def _parse(payload: dict[str, Any], year: int) -> YearPrices:
    """Average the nominal and adjusted closes falling in ``year`` from a chart body.

    Yahoo returns column arrays aligned by index: ``timestamp[i]`` pairs with
    ``quote[0].close[i]`` and ``adjclose[0].adjclose[i]``. Any of them can be
    null on a non-trading slot, and days outside ``year`` are ignored defensively
    even though the request already windows the year.
    """
    results = (payload.get("chart") or {}).get("result") or []
    if not results:
        return YearPrices()
    result = results[0]
    timestamps = result.get("timestamp") or []
    indicators = result.get("indicators") or {}
    closes_raw = (indicators.get("quote") or [{}])[0].get("close") or []
    adjusted_raw = (indicators.get("adjclose") or [{}])[0].get("adjclose") or []

    closes: list[Decimal] = []
    adjusted: list[Decimal] = []
    for i, stamp in enumerate(timestamps):
        if stamp is None or datetime.fromtimestamp(stamp, UTC).year != year:
            continue
        close = _dec(closes_raw[i]) if i < len(closes_raw) else None
        if close is not None:
            closes.append(close)
        adj = _dec(adjusted_raw[i]) if i < len(adjusted_raw) else None
        if adj is not None:
            adjusted.append(adj)
    return YearPrices(nominal_avg=_avg(closes), adjusted_avg=_avg(adjusted))
