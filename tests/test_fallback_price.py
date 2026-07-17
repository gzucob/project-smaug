"""Fallback chains: try the primary, consult the fallback only when it yields none."""

from decimal import Decimal

import pytest

from smaug.analysis.domain.financials import MarketData, YearPrices
from smaug.analysis.domain.indicators import NullReason
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
    chain = FallbackPriceHistory([primary, fallback])

    prices = await chain.year_prices("PETR4", 2024)

    assert prices.adjusted_avg == Decimal(30)
    assert fallback.calls == 0


async def test_history_falls_back_when_primary_is_empty() -> None:
    primary = FakeHistory(YearPrices())  # adjusted_avg None
    fallback = FakeHistory(YearPrices(adjusted_avg=Decimal(99)))
    chain = FallbackPriceHistory([primary, fallback])

    prices = await chain.year_prices("PETR4", 2024)

    assert prices.adjusted_avg == Decimal(99)
    assert primary.calls == 1
    assert fallback.calls == 1


async def test_history_falls_back_when_primary_raises() -> None:
    primary = FakeHistory(BrapiTimeoutError("yahoo down"))
    fallback = FakeHistory(YearPrices(adjusted_avg=Decimal(99)))
    chain = FallbackPriceHistory([primary, fallback])

    prices = await chain.year_prices("PETR4", 2024)

    assert prices.adjusted_avg == Decimal(99)
    assert fallback.calls == 1


async def test_history_tries_a_third_source_when_the_first_two_yield_none() -> None:
    # The contracted slot (#67): a three-deep chain where only the last has data.
    yahoo = FakeHistory(YearPrices())
    brapi = FakeHistory(YearPrices())
    contracted = FakeHistory(YearPrices(adjusted_avg=Decimal(99)))
    chain = FallbackPriceHistory([yahoo, brapi, contracted])

    prices = await chain.year_prices("PETR4", 2024)

    assert prices.adjusted_avg == Decimal(99)
    assert yahoo.calls == brapi.calls == contracted.calls == 1


async def test_history_reports_symbol_not_found_when_every_source_agrees() -> None:
    # A delisted/renamed ticker no source knows (#64): the empty result is named
    # non-transient, not a bare null.
    chain = FallbackPriceHistory(
        [
            FakeHistory(YearPrices(null_reason=NullReason.PRICE_SYMBOL_NOT_FOUND)),
            FakeHistory(YearPrices(null_reason=NullReason.PRICE_SYMBOL_NOT_FOUND)),
        ]
    )

    prices = await chain.year_prices("DEAD3", 2024)

    assert prices.adjusted_avg is None
    assert prices.null_reason is NullReason.PRICE_SYMBOL_NOT_FOUND


async def test_history_stays_a_plain_gap_when_one_source_knew_the_symbol() -> None:
    # One source recognised the symbol but had no data for the year: ambiguous, so
    # the null stays transient (no reason), not a delisting.
    chain = FallbackPriceHistory(
        [
            FakeHistory(YearPrices(null_reason=NullReason.PRICE_SYMBOL_NOT_FOUND)),
            FakeHistory(YearPrices()),  # symbol known, just no data this year
        ]
    )

    prices = await chain.year_prices("PETR4", 2024)

    assert prices.adjusted_avg is None
    assert prices.null_reason is None


async def test_history_needs_at_least_one_provider() -> None:
    with pytest.raises(ValueError, match="at least one provider"):
        FallbackPriceHistory([])
