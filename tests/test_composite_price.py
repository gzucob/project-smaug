"""CompositePriceProvider: quote from one source, year history from another."""

from decimal import Decimal

from smaug.analysis.domain.financials import MarketData, YearPrices
from smaug.analysis.infrastructure.composite_price import CompositePriceProvider


class FakeQuote:
    def __init__(self, data: MarketData) -> None:
        self._data = data
        self.calls: list[str] = []

    async def get(self, ticker: str) -> MarketData:
        self.calls.append(ticker)
        return self._data

    async def year_prices(self, ticker: str, year: int) -> YearPrices:
        raise AssertionError("quote source must not serve year history")


class FakeHistory:
    def __init__(self, prices: YearPrices) -> None:
        self._prices = prices
        self.calls: list[tuple[str, int]] = []

    async def year_prices(self, ticker: str, year: int) -> YearPrices:
        self.calls.append((ticker, year))
        return self._prices


async def test_get_delegates_to_the_quote_source() -> None:
    quote = FakeQuote(MarketData(price=Decimal(10)))
    history = FakeHistory(YearPrices())
    composite = CompositePriceProvider(quote=quote, history=history)

    market = await composite.get("PETR4")

    assert market.price == Decimal(10)
    assert quote.calls == ["PETR4"]
    assert history.calls == []


async def test_year_prices_delegates_to_the_history_source() -> None:
    quote = FakeQuote(MarketData())
    history = FakeHistory(YearPrices(nominal_avg=Decimal(50), adjusted_avg=Decimal(40)))
    composite = CompositePriceProvider(quote=quote, history=history)

    prices = await composite.year_prices("PETR4", 2024)

    assert prices.adjusted_avg == Decimal(40)
    assert history.calls == [("PETR4", 2024)]
    assert quote.calls == []
