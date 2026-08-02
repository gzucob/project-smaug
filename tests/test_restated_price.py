"""RestatedPriceProvider: an as-traded price put on the counts' share base."""

from __future__ import annotations

from decimal import Decimal

from smaug.analysis.domain.financials import MarketData, ShareCounts, YearPrices
from smaug.analysis.domain.indicators import NullReason
from smaug.analysis.infrastructure.restated_price import RestatedPriceProvider


class FakeAsTradedPrices:
    """A source that publishes what printed on the tape, like B3's own series."""

    def __init__(self, year: YearPrices, quote: MarketData | None = None) -> None:
        self._year = year
        self._quote = quote or MarketData()
        self.year_calls: list[tuple[str, int]] = []

    async def get(self, ticker: str) -> MarketData:
        return self._quote

    async def year_prices(self, ticker: str, year: int) -> YearPrices:
        self.year_calls.append((ticker, year))
        return self._year


class FakeShares:
    def __init__(self, factors: dict[int, Decimal] | None = None) -> None:
        self._factors = factors or {}
        self.factor_calls: list[tuple[str, int]] = []

    async def outstanding(self, ticker: str, year: int) -> Decimal | None:
        return None

    async def counts(self, ticker: str, year: int) -> ShareCounts | None:
        return None

    async def restatement_factor(self, ticker: str, year: int) -> Decimal:
        self.factor_calls.append((ticker, year))
        return self._factors.get(year, Decimal(1))


async def test_the_price_is_divided_by_the_factor_the_counts_were_multiplied_by() -> (
    None
):
    # BBAS3 2023, measured: it traded at 45.4933 and B3 publishes that; the 2024
    # bonus doubled the count, so the count side is x2 and the price side is /2.
    inner = FakeAsTradedPrices(YearPrices(nominal_avg=Decimal("45.4933")))
    provider = RestatedPriceProvider(inner, FakeShares({2023: Decimal(2)}))

    prices = await provider.year_prices("BBAS3", 2023)

    assert prices.nominal_avg == Decimal("22.74665")
    # Which is what the vendor series published for the same year (22.7463),
    # because that series was already back-adjusted for the same event.


async def test_both_bases_are_divided_so_the_pair_stays_consistent() -> None:
    inner = FakeAsTradedPrices(
        YearPrices(nominal_avg=Decimal("30"), adjusted_avg=Decimal("24"))
    )
    provider = RestatedPriceProvider(inner, FakeShares({2020: Decimal(3)}))

    prices = await provider.year_prices("SAPR4", 2020)

    assert prices.nominal_avg == Decimal(10)
    assert prices.adjusted_avg == Decimal(8)


async def test_a_year_with_no_corporate_action_passes_through_untouched() -> None:
    original = YearPrices(nominal_avg=Decimal("9.7963"))
    provider = RestatedPriceProvider(FakeAsTradedPrices(original), FakeShares())

    prices = await provider.year_prices("PETR4", 2015)

    assert prices is original  # not merely equal: nothing was rebuilt


async def test_a_missing_price_is_not_restated_and_costs_no_mirror_read() -> None:
    shares = FakeShares({2015: Decimal(2)})
    inner = FakeAsTradedPrices(YearPrices(null_reason=NullReason.NOT_YET_LISTED))
    provider = RestatedPriceProvider(inner, shares)

    prices = await provider.year_prices("TAEE4", 2015)

    assert prices.nominal_avg is None
    assert prices.null_reason is NullReason.NOT_YET_LISTED
    # Reading the capital history to restate nothing would be a wasted query per
    # ticker-year, and there are 5,544 of them.
    assert shares.factor_calls == []


async def test_the_live_quote_is_never_restated() -> None:
    # It is already on today's base — the base everything else is restated *to*.
    shares = FakeShares({2026: Decimal(2)})
    inner = FakeAsTradedPrices(YearPrices(), MarketData(price=Decimal("21.35")))
    provider = RestatedPriceProvider(inner, shares)

    quote = await provider.get("BBAS3")

    assert quote.price == Decimal("21.35")
    assert shares.factor_calls == []


async def test_the_cap_is_invariant_under_the_restatement() -> None:
    """The property the whole decorator exists to preserve.

    ``cap = price x shares``: the counts are multiplied by k and the price
    divided by it, so the capitalization is the same number on either base. If
    this ever fails, every market multiple is wrong by the factor.
    """
    as_traded, count, factor = Decimal("45.4933"), Decimal("2870000000"), Decimal(2)
    inner = FakeAsTradedPrices(YearPrices(nominal_avg=as_traded))
    provider = RestatedPriceProvider(inner, FakeShares({2023: factor}))

    restated = (await provider.year_prices("BBAS3", 2023)).nominal_avg
    assert restated is not None

    assert restated * (count * factor) == as_traded * count
