"""YahooPriceHistory: per-year daily averages, degrading to null on absence."""

from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest

from smaug.analysis.domain.financials import MarketData
from smaug.analysis.domain.indicators import NullReason
from smaug.analysis.infrastructure.yahoo_price import (
    YahooPriceHistory,
    YahooQuoteProvider,
)
from smaug.portfolio.domain import market_symbols
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


async def test_year_prices_symbol_not_found_is_named_non_transient() -> None:
    # A delisted / unknown ticker: Yahoo answers 404 -> a *named* null (#64), not a
    # bare one, so a fallback chain and smaug doctor can tell it from a gap.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"chart": {"result": None, "error": {}}})

    async with _mock_client(handler) as http:
        provider = YahooPriceHistory("https://query1.finance.yahoo.com", http)
        prices = await provider.year_prices("DEAD3", 2024)

    assert prices.nominal_avg is None
    assert prices.adjusted_avg is None
    assert prices.null_reason is NullReason.PRICE_SYMBOL_NOT_FOUND


async def test_year_prices_other_http_error_stays_a_bare_gap() -> None:
    # A transient non-404 (e.g. 500): expected null with no reason, so the chain
    # treats it as a gap rather than a delisting.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={})

    async with _mock_client(handler) as http:
        provider = YahooPriceHistory("https://query1.finance.yahoo.com", http)
        prices = await provider.year_prices("PETR4", 2024)

    assert prices.null_reason is None


async def test_year_prices_uses_the_market_symbol_override() -> None:
    # A renamed ticker resolves to the overridden Yahoo symbol, not its own (#64).
    market_symbols.TICKER_SYMBOL_OVERRIDES["OLDX3"] = "NEWX3"
    try:

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/v8/finance/chart/NEWX3.SA"
            body = _chart([_ts(2024, 6, 3)], [40], [30])
            return httpx.Response(200, json=body)

        async with _mock_client(handler) as http:
            provider = YahooPriceHistory("https://query1.finance.yahoo.com", http)
            prices = await provider.year_prices("OLDX3", 2024)

        assert prices.nominal_avg == Decimal(40)
    finally:
        del market_symbols.TICKER_SYMBOL_OVERRIDES["OLDX3"]


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


async def test_quote_reads_price_from_chart_meta() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v8/finance/chart/PETR4.SA"
        assert request.headers["user-agent"].startswith("Mozilla/")
        body = {"chart": {"result": [{"meta": {"regularMarketPrice": 39.65}}]}}
        return httpx.Response(200, json=body)

    async with _mock_client(handler) as http:
        provider = YahooQuoteProvider("https://query1.finance.yahoo.com", http)
        market = await provider.get("PETR4")

    assert market.price == Decimal("39.65")
    assert market.market_cap is None  # Yahoo does not expose it for free
    assert market.shares is None


async def test_quote_null_when_symbol_unknown() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"chart": {"result": None}})

    async with _mock_client(handler) as http:
        provider = YahooQuoteProvider("https://query1.finance.yahoo.com", http)
        market = await provider.get("DEAD3")

    assert market == MarketData()


async def test_quote_maps_transport_error_to_brapi_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    async with _mock_client(handler) as http:
        provider = YahooQuoteProvider("https://query1.finance.yahoo.com", http)
        with pytest.raises(BrapiTimeoutError):
            await provider.get("PETR4")
