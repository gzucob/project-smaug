"""BrapiPriceProvider.year_prices: per-year averages of nominal and adjusted closes."""

from datetime import UTC, datetime
from decimal import Decimal

import httpx

from smaug.analysis.infrastructure.brapi_price import BrapiPriceProvider


def _mock_client(handler: object) -> httpx.AsyncClient:
    transport = httpx.MockTransport(handler)  # type: ignore[arg-type]
    return httpx.AsyncClient(transport=transport)


def _ts(year: int, month: int, day: int) -> int:
    return int(datetime(year, month, day, tzinfo=UTC).timestamp())


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
