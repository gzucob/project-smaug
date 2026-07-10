"""Fallback chains: try the primary, consult the fallback only when it yields none."""

from decimal import Decimal

import pytest

from smaug.analysis.domain.financials import MarketData, YearPrices
from smaug.analysis.infrastructure.fallback_price import (
    FallbackPriceHistory,
    FallbackQuoteProvider,
)
from smaug.shared.errors import BrapiTimeoutError


class FakeQuote:
    def __init__(self, result: MarketData | Exception) -> None:
        self._result = result
        self.calls = 0

    async def get(self, ticker: str) -> MarketData:
        self.calls += 1
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


class FakeHistory:
    def __init__(self, result: YearPrices | Exception) -> None:
        self._result = result
        self.calls = 0

    async def year_prices(self, ticker: str, year: int) -> YearPrices:
        self.calls += 1
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


async def test_quote_uses_primary_when_it_has_a_price() -> None:
    primary = FakeQuote(MarketData(price=Decimal(40)))
    fallback = FakeQuote(MarketData(price=Decimal(99)))
    chain = FallbackQuoteProvider(primary=primary, fallback=fallback)

    market = await chain.get("PETR4")

    assert market.price == Decimal(40)
    assert fallback.calls == 0  # primary sufficed


async def test_quote_falls_back_when_primary_has_no_price() -> None:
    primary = FakeQuote(MarketData())  # price None
    fallback = FakeQuote(MarketData(price=Decimal(99)))
    chain = FallbackQuoteProvider(primary=primary, fallback=fallback)

    market = await chain.get("PETR4")

    assert market.price == Decimal(99)
    assert primary.calls == 1
    assert fallback.calls == 1


async def test_quote_falls_back_when_primary_raises() -> None:
    primary = FakeQuote(BrapiTimeoutError("yahoo down"))
    fallback = FakeQuote(MarketData(price=Decimal(99)))
    chain = FallbackQuoteProvider(primary=primary, fallback=fallback)

    market = await chain.get("PETR4")

    assert market.price == Decimal(99)
    assert fallback.calls == 1


async def test_quote_propagates_when_both_fail() -> None:
    chain = FallbackQuoteProvider(
        primary=FakeQuote(BrapiTimeoutError("yahoo down")),
        fallback=FakeQuote(BrapiTimeoutError("brapi down")),
    )
    with pytest.raises(BrapiTimeoutError):
        await chain.get("PETR4")


async def test_history_uses_primary_when_it_has_a_price() -> None:
    primary = FakeHistory(YearPrices(adjusted_avg=Decimal(30)))
    fallback = FakeHistory(YearPrices(adjusted_avg=Decimal(99)))
    chain = FallbackPriceHistory(primary=primary, fallback=fallback)

    prices = await chain.year_prices("PETR4", 2024)

    assert prices.adjusted_avg == Decimal(30)
    assert fallback.calls == 0


async def test_history_falls_back_when_primary_is_empty() -> None:
    primary = FakeHistory(YearPrices())  # adjusted_avg None
    fallback = FakeHistory(YearPrices(adjusted_avg=Decimal(99)))
    chain = FallbackPriceHistory(primary=primary, fallback=fallback)

    prices = await chain.year_prices("PETR4", 2024)

    assert prices.adjusted_avg == Decimal(99)
    assert primary.calls == 1
    assert fallback.calls == 1


async def test_history_falls_back_when_primary_raises() -> None:
    primary = FakeHistory(BrapiTimeoutError("yahoo down"))
    fallback = FakeHistory(YearPrices(adjusted_avg=Decimal(99)))
    chain = FallbackPriceHistory(primary=primary, fallback=fallback)

    prices = await chain.year_prices("PETR4", 2024)

    assert prices.adjusted_avg == Decimal(99)
    assert fallback.calls == 1
