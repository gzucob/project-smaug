"""Current market data from brapi's quote endpoint (price side of Phase 2).

CVM has no share price, so the market multiples (P/E, P/B, DY, EV/EBITDA) need a
quote. brapi's basic ``GET /quote/{ticker}`` (price + market cap) is available on
the free plan for the whole portfolio — unlike the fundamental modules that 403.
The token is required here and kept out of any persisted metadata.

Dividend yield does not come from here: the free plan does not expose trailing
dividends, so the trailing payout is sourced from the CVM cash-flow statement
instead (see ``mongo_fundamentals``). This provider supplies only the price side:
price, market cap, and the share count derived from the two.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from smaug.analysis.domain.financials import MarketData, YearPrices
from smaug.ingestion.infrastructure.brapi_client import BrapiClient
from smaug.shared.errors import BrapiTimeoutError


def _dec(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _avg(values: list[Decimal]) -> Decimal | None:
    return sum(values, Decimal(0)) / Decimal(len(values)) if values else None


def _shares(quote: dict[str, Any]) -> Decimal | None:
    """Share count, preferring the quote's own field over the derived identity.

    The free plan omits ``sharesOutstanding`` but does return ``marketCap``, and
    market cap is *defined* as price x shares — so dividing them back out is
    exact, not an estimate. Without it ``eps``/``bvps`` degrade to null.
    """
    outstanding = _dec(quote.get("sharesOutstanding"))
    if outstanding is not None and outstanding > 0:
        return outstanding

    market_cap = _dec(quote.get("marketCap"))
    price = _dec(quote.get("regularMarketPrice"))
    if market_cap is None or price is None or price == 0:
        return None
    return market_cap / price


class BrapiPriceProvider:
    """Fetches price/market-cap for a ticker from brapi (raises on API failure)."""

    def __init__(
        self, base_url: str, token: str, http_client: httpx.AsyncClient
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._http = http_client

    async def _get(self, ticker: str, params: dict[str, Any]) -> httpx.Response:
        """Fetch the quote endpoint, mapping transport failures to a typed error.

        A timeout or connection error raises before any HTTP response exists, so
        it never passes through ``_raise_for_status``; translating it to
        ``BrapiTimeoutError`` keeps it inside the ``BrapiError`` family the use
        case already degrades on (see ``AnalyzePortfolioUseCase._current_quote``).
        """
        url = f"{self._base_url}/quote/{ticker}"
        try:
            response = await self._http.get(
                url, params={"token": self._token, **params}
            )
        except httpx.TransportError as exc:
            raise BrapiTimeoutError(
                f"transport failure fetching quote for {ticker}: {exc!r}"
            ) from exc
        BrapiClient._raise_for_status(response, ticker, "quote")
        return response

    async def get(self, ticker: str) -> MarketData:
        response = await self._get(ticker, {})

        results = response.json().get("results") or []
        if not results:
            return MarketData()
        quote = results[0]
        return MarketData(
            price=_dec(quote.get("regularMarketPrice")),
            market_cap=_dec(quote.get("marketCap")),
            shares=_shares(quote),
        )

    async def year_prices(self, ticker: str, year: int) -> YearPrices:
        """Average nominal (``close``) and adjusted (``adjustedClose``) over ``year``.

        Uses the free-plan daily history (``range=5y``), which covers recent
        closed years. Days outside ``year`` are ignored; a year with no data
        yields ``YearPrices()`` (both ``None``) and the multiples degrade to null.
        """
        response = await self._get(ticker, {"range": "5y", "interval": "1d"})

        results = response.json().get("results") or []
        if not results:
            return YearPrices()
        history = results[0].get("historicalDataPrice") or []

        closes: list[Decimal] = []
        adjusted: list[Decimal] = []
        for point in history:
            stamp = point.get("date")
            if stamp is None or datetime.fromtimestamp(stamp, UTC).year != year:
                continue
            close = _dec(point.get("close"))
            if close is not None:
                closes.append(close)
            adj = _dec(point.get("adjustedClose"))
            if adj is not None:
                adjusted.append(adj)
        return YearPrices(nominal_avg=_avg(closes), adjusted_avg=_avg(adjusted))
