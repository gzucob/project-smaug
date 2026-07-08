"""Current market data from brapi's quote endpoint (price side of Phase 2).

CVM has no share price, so the market multiples (P/E, P/B, DY, EV/EBITDA) need a
quote. brapi's basic ``GET /quote/{ticker}`` (price + market cap) is available on
the free plan for the whole portfolio — unlike the fundamental modules that 403.
The token is required here and kept out of any persisted metadata.

Dividend yield needs trailing dividends, which the free plan does not expose for
most tickers, so ``dividends_12m`` stays ``None`` (DY then computes as ``None``).
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from smaug.analysis.domain.financials import MarketData
from smaug.ingestion.infrastructure.brapi_client import BrapiClient


def _dec(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


class BrapiPriceProvider:
    """Fetches price/market-cap for a ticker from brapi (raises on API failure)."""

    def __init__(
        self, base_url: str, token: str, http_client: httpx.AsyncClient
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._http = http_client

    async def get(self, ticker: str) -> MarketData:
        url = f"{self._base_url}/quote/{ticker}"
        response = await self._http.get(url, params={"token": self._token})
        BrapiClient._raise_for_status(response, ticker, "quote")

        results = response.json().get("results") or []
        if not results:
            return MarketData()
        quote = results[0]
        return MarketData(
            price=_dec(quote.get("regularMarketPrice")),
            market_cap=_dec(quote.get("marketCap")),
            shares=_dec(quote.get("sharesOutstanding")),
            dividends_12m=None,
        )
