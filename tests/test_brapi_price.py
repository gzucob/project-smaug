"""BrapiPriceProvider: current quote (price, cap, shares) and per-year averages."""

from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest

from smaug.analysis.infrastructure.brapi_price import BrapiPriceProvider
from smaug.shared.errors import BrapiTimeoutError


def _mock_client(handler: object) -> httpx.AsyncClient:
    transport = httpx.MockTransport(handler)  # type: ignore[arg-type]
    return httpx.AsyncClient(transport=transport)


def _ts(year: int, month: int, day: int) -> int:
    return int(datetime(year, month, day, tzinfo=UTC).timestamp())


def _quote_client(quote: dict[str, object]) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/quote/PETR4"
        return httpx.Response(200, json={"results": [quote]})

    return _mock_client(handler)


async def test_get_prefers_shares_outstanding_when_the_quote_has_it() -> None:
    quote = {
        "regularMarketPrice": 40,
        "marketCap": 500,
        "sharesOutstanding": 12,
    }
    async with _quote_client(quote) as http:
        provider = BrapiPriceProvider("https://brapi.dev/api", "SECRET", http)
        market = await provider.get("PETR4")

    assert market.shares == Decimal(12)  # not the derived 500 / 40 = 12.5


async def test_get_derives_shares_from_market_cap_over_price() -> None:
    quote = {"regularMarketPrice": 40, "marketCap": 500}  # free plan: no shares
    async with _quote_client(quote) as http:
        provider = BrapiPriceProvider("https://brapi.dev/api", "SECRET", http)
        market = await provider.get("PETR4")

    assert market.price == Decimal(40)
    assert market.market_cap == Decimal(500)
    assert market.shares == Decimal("12.5")


async def test_get_leaves_shares_none_when_price_is_zero_or_missing() -> None:
    async with _quote_client({"marketCap": 500, "regularMarketPrice": 0}) as http:
        provider = BrapiPriceProvider("https://brapi.dev/api", "SECRET", http)
        zero_price = await provider.get("PETR4")

    async with _quote_client({"marketCap": 500}) as http:
        provider = BrapiPriceProvider("https://brapi.dev/api", "SECRET", http)
        no_price = await provider.get("PETR4")

    assert zero_price.shares is None
    assert no_price.shares is None


async def test_year_prices_averages_only_the_requested_year() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/quote/PETR4"
        assert request.url.params["range"] == "5y"
        assert request.url.params["interval"] == "1d"
        points = [
            {"date": _ts(2023, 6, 3), "close": 100, "adjustedClose": 80},
            {"date": _ts(2024, 6, 3), "close": 40, "adjustedClose": 30},
            {"date": _ts(2024, 9, 3), "close": 60, "adjustedClose": 50},
        ]
        return httpx.Response(200, json={"results": [{"historicalDataPrice": points}]})

    async with _mock_client(handler) as http:
        provider = BrapiPriceProvider("https://brapi.dev/api", "SECRET", http)
        prices = await provider.year_prices("PETR4", 2024)

    assert prices.nominal_avg == Decimal(50)  # (40 + 60) / 2, ignores 2023
    assert prices.adjusted_avg == Decimal(40)  # (30 + 50) / 2


async def test_year_prices_empty_when_no_results() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"results": []})

    async with _mock_client(handler) as http:
        provider = BrapiPriceProvider("https://brapi.dev/api", "SECRET", http)
        prices = await provider.year_prices("PETR4", 2024)

    assert prices.nominal_avg is None
    assert prices.adjusted_avg is None


async def test_get_maps_timeout_to_brapi_error() -> None:
    # A timeout raises before any HTTP response, so it must be translated into
    # the BrapiError family the analyze use case degrades on — not escape it.
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    async with _mock_client(handler) as http:
        provider = BrapiPriceProvider("https://brapi.dev/api", "SECRET", http)
        with pytest.raises(BrapiTimeoutError):
            await provider.get("PETR4")


async def test_year_prices_maps_transport_error_to_brapi_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    async with _mock_client(handler) as http:
        provider = BrapiPriceProvider("https://brapi.dev/api", "SECRET", http)
        with pytest.raises(BrapiTimeoutError):
            await provider.year_prices("PETR4", 2024)
