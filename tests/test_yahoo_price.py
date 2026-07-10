"""YahooPriceHistory: per-year daily averages, degrading to null on absence."""

from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest

from smaug.analysis.infrastructure.yahoo_price import YahooPriceHistory
from smaug.shared.errors import BrapiTimeoutError


def _mock_client(handler: object) -> httpx.AsyncClient:
    transport = httpx.MockTransport(handler)  # type: ignore[arg-type]
    return httpx.AsyncClient(transport=transport)


def _ts(year: int, month: int, day: int) -> int:
    return int(datetime(year, month, day, tzinfo=UTC).timestamp())


def _chart(timestamps: list[int], closes: list[object], adjusted: list[object]) -> dict:
    return {
        "chart": {
            "result": [
                {
                    "timestamp": timestamps,
                    "indicators": {
                        "quote": [{"close": closes}],
                        "adjclose": [{"adjclose": adjusted}],
                    },
                }
            ],
            "error": None,
        }
    }


async def test_year_prices_averages_only_the_requested_year() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v8/finance/chart/PETR4.SA"
        # The year is requested as an exact window, not a fixed range.
        assert int(request.url.params["period1"]) == _ts(2024, 1, 1)
        assert int(request.url.params["period2"]) == _ts(2025, 1, 1)
        assert request.headers["user-agent"].startswith("Mozilla/")
        body = _chart(
            [_ts(2023, 12, 29), _ts(2024, 6, 3), _ts(2024, 9, 3)],
            [100, 40, 60],
            [80, 30, 50],
        )
        return httpx.Response(200, json=body)

    async with _mock_client(handler) as http:
        provider = YahooPriceHistory("https://query1.finance.yahoo.com", http)
        prices = await provider.year_prices("PETR4", 2024)

    assert prices.nominal_avg == Decimal(50)  # (40 + 60) / 2, ignores 2023
    assert prices.adjusted_avg == Decimal(40)  # (30 + 50) / 2


async def test_year_prices_skips_null_slots() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = _chart(
            [_ts(2024, 1, 2), _ts(2024, 1, 3), _ts(2024, 1, 4)],
            [10, None, 30],  # holiday / missing close
            [10, None, 30],
        )
        return httpx.Response(200, json=body)

    async with _mock_client(handler) as http:
        provider = YahooPriceHistory("https://query1.finance.yahoo.com", http)
        prices = await provider.year_prices("PETR4", 2024)

    assert prices.nominal_avg == Decimal(20)  # (10 + 30) / 2


async def test_year_prices_null_when_symbol_unknown() -> None:
    # A delisted / unknown ticker: Yahoo answers 404 -> expected null, no crash.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"chart": {"result": None, "error": {}}})

    async with _mock_client(handler) as http:
        provider = YahooPriceHistory("https://query1.finance.yahoo.com", http)
        prices = await provider.year_prices("DEAD3", 2024)

    assert prices.nominal_avg is None
    assert prices.adjusted_avg is None


async def test_year_prices_null_when_no_result() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"chart": {"result": [], "error": None}})

    async with _mock_client(handler) as http:
        provider = YahooPriceHistory("https://query1.finance.yahoo.com", http)
        prices = await provider.year_prices("PETR4", 2024)

    assert prices.nominal_avg is None
    assert prices.adjusted_avg is None


async def test_year_prices_maps_transport_error_to_brapi_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    async with _mock_client(handler) as http:
        provider = YahooPriceHistory("https://query1.finance.yahoo.com", http)
        with pytest.raises(BrapiTimeoutError):
            await provider.year_prices("PETR4", 2024)
