"""RestatedPriceProvider: an as-traded price put on the counts' share base."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from smaug.analysis.domain.capital import RestatementStep
from smaug.analysis.domain.financials import (
    MarketData,
    SessionClose,
    ShareCounts,
    YearPrices,
)
from smaug.analysis.domain.indicators import NullReason
from smaug.analysis.infrastructure.restated_price import RestatedPriceProvider


class FakeAsTradedPrices:
    """A source that publishes what printed on the tape, like B3's own series."""

    def __init__(
        self,
        year: YearPrices,
        quote: MarketData | None = None,
        sessions: tuple[SessionClose, ...] = (),
    ) -> None:
        self._year = year
        self._quote = quote or MarketData()
        self._sessions = sessions
        self.year_calls: list[tuple[str, int]] = []

    async def get(self, ticker: str) -> MarketData:
        return self._quote

    async def year_prices(self, ticker: str, year: int) -> YearPrices:
        self.year_calls.append((ticker, year))
        return self._year

    async def year_sessions(self, ticker: str, year: int) -> tuple[SessionClose, ...]:
        return self._sessions


class FakeShares:
    def __init__(self, timeline: tuple[RestatementStep, ...] = ()) -> None:
        self._timeline = timeline
        self.timeline_calls: list[str] = []

    async def outstanding(self, ticker: str, year: int) -> Decimal | None:
        return None

    async def counts(self, ticker: str, year: int) -> ShareCounts | None:
        return None

    async def restatement_timeline(self, ticker: str) -> tuple[RestatementStep, ...]:
        self.timeline_calls.append(ticker)
        return self._timeline


def _traded(*closes: tuple[str, str]) -> tuple[SessionClose, ...]:
    return tuple(
        SessionClose(session=date.fromisoformat(day), close=Decimal(price))
        for day, price in closes
    )


def provider_for(
    inner: FakeAsTradedPrices, timeline: tuple[RestatementStep, ...]
) -> RestatedPriceProvider:
    return RestatedPriceProvider(inner, FakeShares(timeline))


BONUS_2024 = RestatementStep(effective=date(2024, 4, 25), ratio=Decimal(2))


async def test_the_price_is_divided_by_the_factor_the_counts_were_multiplied_by() -> (
    None
):
    # BBAS3 2023, measured: it traded at 45.4933 and B3 publishes that; the 2024
    # bonus doubled the count, so the count side is x2 and the price side is /2.
    inner = FakeAsTradedPrices(
        YearPrices(nominal_avg=Decimal("45.4933")),
        sessions=_traded(("2023-03-10", "44"), ("2023-09-11", "46.9866")),
    )
    provider = RestatedPriceProvider(inner, FakeShares((BONUS_2024,)))

    prices = await provider.year_prices("BBAS3", 2023)

    assert prices.nominal_avg == Decimal("22.74665")
    # Which is what the vendor series published for the same year (22.7463),
    # because that series was already back-adjusted for the same event.


async def test_a_year_containing_the_action_is_split_at_its_date() -> None:
    """The whole point of dating the timeline (ADR 0033).

    A 2:1 split on 1 April: January's sessions are quoted on the old base and
    October's on the new one, so the year has no single base to restate. Applying
    the factor to the year's average would divide October's closes by a split
    they were already quoted after.
    """
    inner = FakeAsTradedPrices(
        # As traded: (20 + 10) / 2 = 15, which is a price the share never had.
        YearPrices(nominal_avg=Decimal(15)),
        sessions=_traded(("2016-01-20", "20"), ("2016-10-20", "10")),
    )
    timeline = (RestatementStep(effective=date(2016, 4, 1), ratio=Decimal(2)),)

    prices = await provider_for(inner, timeline).year_prices("WEGE3", 2016)

    # Restated: 20/2 next to 10/1 — one base, and the flat year it really was.
    assert prices.nominal_avg == Decimal(10)


async def test_an_action_after_the_year_still_covers_every_session_in_it() -> None:
    """The refinement only bites inside the year — elsewhere nothing moves."""
    inner = FakeAsTradedPrices(
        YearPrices(nominal_avg=Decimal(30)),
        sessions=_traded(("2015-02-02", "20"), ("2015-11-30", "40")),
    )
    timeline = (RestatementStep(effective=date(2016, 4, 1), ratio=Decimal(2)),)

    prices = await provider_for(inner, timeline).year_prices("WEGE3", 2015)

    assert prices.nominal_avg == Decimal(15)


async def test_two_actions_a_year_apart_compound_only_over_what_precedes_them() -> None:
    """Bradesco's shape: a bonus most years, so a year sits between two of them.

    The residual this fixes grew with the number of actions a company had, because
    every one of them was applied to whole years that were partly quoted after it.
    """
    inner = FakeAsTradedPrices(
        YearPrices(nominal_avg=Decimal("23.1")),  # (24.2 + 22) / 2, as traded
        sessions=_traded(("2016-01-11", "24.2"), ("2016-06-10", "22")),
    )
    timeline = (
        RestatementStep(effective=date(2016, 3, 15), ratio=Decimal("1.1")),
        RestatementStep(effective=date(2017, 3, 15), ratio=Decimal("1.1")),
    )

    prices = await provider_for(inner, timeline).year_prices("BBDC4", 2016)

    # January is behind both bonuses (24.2/1.21), June behind only the 2017 one
    # (22/1.1) — and on the current base the year was flat at 20 throughout.
    assert prices.nominal_avg == Decimal(20)


async def test_both_bases_are_divided_so_the_pair_stays_consistent() -> None:
    inner = FakeAsTradedPrices(
        YearPrices(nominal_avg=Decimal(30), adjusted_avg=Decimal(24)),
        sessions=_traded(("2020-05-05", "30")),
    )
    timeline = (RestatementStep(effective=date(2021, 1, 1), ratio=Decimal(3)),)

    prices = await provider_for(inner, timeline).year_prices("SAPR4", 2020)

    assert prices.nominal_avg == Decimal(10)
    assert prices.adjusted_avg == Decimal(8)


async def test_a_year_with_no_corporate_action_passes_through_untouched() -> None:
    original = YearPrices(nominal_avg=Decimal("9.7963"))
    provider = RestatedPriceProvider(FakeAsTradedPrices(original), FakeShares())

    prices = await provider.year_prices("PETR4", 2015)

    assert prices is original  # not merely equal: nothing was rebuilt


async def test_a_source_with_no_sessions_falls_back_to_the_year_factor() -> None:
    """A provider that publishes an average and no closes behind it.

    B3's series always carries both, so this is the vendor-shaped case: with
    nothing to weigh, the year takes the factor standing at its end — which is
    what the yearly rule did for every year before this change.
    """
    inner = FakeAsTradedPrices(YearPrices(nominal_avg=Decimal("45.4933")))
    provider = RestatedPriceProvider(inner, FakeShares((BONUS_2024,)))

    prices = await provider.year_prices("BBAS3", 2023)

    assert prices.nominal_avg == Decimal("22.74665")


async def test_a_missing_price_is_not_restated_and_costs_no_mirror_read() -> None:
    shares = FakeShares((BONUS_2024,))
    inner = FakeAsTradedPrices(YearPrices(null_reason=NullReason.NOT_YET_LISTED))
    provider = RestatedPriceProvider(inner, shares)

    prices = await provider.year_prices("TAEE4", 2015)

    assert prices.nominal_avg is None
    assert prices.null_reason is NullReason.NOT_YET_LISTED
    # Reading the capital history to restate nothing would be a wasted query per
    # ticker-year, and there are 5,544 of them.
    assert shares.timeline_calls == []


async def test_the_live_quote_is_never_restated() -> None:
    # It is already on today's base — the base everything else is restated *to*.
    shares = FakeShares((BONUS_2024,))
    inner = FakeAsTradedPrices(YearPrices(), MarketData(price=Decimal("21.35")))
    provider = RestatedPriceProvider(inner, shares)

    quote = await provider.get("BBAS3")

    assert quote.price == Decimal("21.35")
    assert shares.timeline_calls == []


async def test_the_cap_is_invariant_under_the_restatement() -> None:
    """The property the whole decorator exists to preserve.

    ``cap = price x shares``: the counts are multiplied by k and the price
    divided by it, so the capitalization is the same number on either base. If
    this ever fails, every market multiple is wrong by the factor. Stated over a
    year that holds no action, where the count's yearly factor and the price's
    session-weighted one are the same number by construction.
    """
    as_traded, count, factor = Decimal("45.4933"), Decimal("2870000000"), Decimal(2)
    inner = FakeAsTradedPrices(
        YearPrices(nominal_avg=as_traded), sessions=_traded(("2023-06-15", "45.4933"))
    )
    provider = RestatedPriceProvider(inner, FakeShares((BONUS_2024,)))

    restated = (await provider.year_prices("BBAS3", 2023)).nominal_avg
    assert restated is not None

    assert restated * (count * factor) == as_traded * count
