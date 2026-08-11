"""The third price basis: the traded price with the cash paid out put back.

B3 publishes one series and it is the price as traded, so this basis has no
source to read — it is rebuilt from the exchange's own cash-payout record, where
each payment carries the share of the closing price it represented (ADR 0039).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from decimal import Decimal

from smaug.analysis.domain.capital import RestatementStep
from smaug.analysis.domain.dividends import (
    CashEvent,
    average_dividend_factor,
    cash_distributions,
    dividend_factor,
)
from smaug.analysis.domain.financials import MarketData, SessionClose, YearPrices
from smaug.analysis.infrastructure.dividend_adjusted_price import (
    DividendAdjustedPriceProvider,
)


class FakeSessions:
    """A ``SessionPriceProvider`` over a fixed year of closes."""

    def __init__(self, closes: Sequence[SessionClose]) -> None:
        self._closes = tuple(closes)

    async def get(self, ticker: str) -> MarketData:
        return MarketData(price=self._closes[-1].close if self._closes else None)

    async def year_prices(self, ticker: str, year: int) -> YearPrices:
        if not self._closes:
            return YearPrices()
        total = sum((c.close for c in self._closes), Decimal(0))
        return YearPrices(nominal_avg=total / len(self._closes))

    async def year_sessions(self, ticker: str, year: int) -> tuple[SessionClose, ...]:
        return self._closes


class FakeEvents:
    def __init__(self, events: Sequence[CashEvent] | None) -> None:
        self._events = None if events is None else tuple(events)

    async def cash_events(
        self, ticker: str, *, per_share_class: object | None = None
    ) -> tuple[CashEvent, ...] | None:
        return self._events


def _close(day: str, price: str) -> SessionClose:
    return SessionClose(session=date.fromisoformat(day), close=Decimal(price))


def _event(day: str, percentage: str) -> CashEvent:
    return CashEvent(effective=date.fromisoformat(day), percentage=Decimal(percentage))


def test_a_payment_scales_only_the_sessions_that_preceded_it() -> None:
    # The holder of a session before the ex date went on to receive the cash, so
    # that close is scaled down by it. A session after it received nothing.
    events = [_event("2025-07-01", "2")]

    assert dividend_factor(events, date(2025, 6, 30)) == Decimal("0.98")
    assert dividend_factor(events, date(2025, 7, 1)) == 1


def test_payments_compound_backwards_through_the_year() -> None:
    # Bradesco pays monthly; a January close carries every one of them ahead of
    # it, and reading only the nearest understates a decade by the rest.
    events = [_event("2025-04-01", "1"), _event("2025-08-01", "2")]

    assert dividend_factor(events, date(2025, 1, 2)) == Decimal("0.98") * Decimal(
        "0.99"
    )
    assert dividend_factor(events, date(2025, 5, 2)) == Decimal("0.98")


def test_the_year_takes_one_factor_weighted_by_its_sessions() -> None:
    """A year's average is one number and its sessions do not share one factor.

    The weighting is the mirror of the restatement's (ADR 0033): there the year
    divides, here it multiplies, and both must yield the mean of the adjusted
    closes rather than the adjusted mean.
    """
    sessions = [_close("2025-01-02", "10"), _close("2025-12-01", "20")]
    events = [_event("2025-07-01", "10")]

    factor = average_dividend_factor(events, sessions)

    average = (Decimal(10) + Decimal(20)) / 2
    adjusted_mean = (Decimal(10) * Decimal("0.9") + Decimal(20)) / 2
    assert average * factor == adjusted_mean


def test_cash_rights_are_rebased_with_the_same_later_actions_as_prices() -> None:
    events = [
        CashEvent(
            effective=date(2024, 4, 16),
            last_with_right=date(2024, 4, 15),
            amount_per_share=Decimal(2),
        )
    ]
    timeline = [RestatementStep(effective=date(2024, 4, 16), ratio=Decimal(2))]

    assert cash_distributions(
        events, date(2024, 1, 1), date(2024, 12, 31), timeline
    ) == Decimal(1)


def test_cash_rights_use_the_ex_date_window_and_reject_an_unreadable_amount() -> None:
    events = [
        CashEvent(effective=date(2023, 12, 31), amount_per_share=Decimal("0.25")),
        CashEvent(effective=date(2024, 6, 1), amount_per_share=None),
    ]

    assert cash_distributions(events, date(2024, 1, 1), date(2024, 12, 31)) is None
    assert cash_distributions(events, date(2025, 1, 1), date(2025, 12, 31)) == 0


def test_total_return_ignores_an_event_that_only_has_an_absolute_amount() -> None:
    event = CashEvent(effective=date(2025, 7, 1), amount_per_share=Decimal("0.50"))

    assert dividend_factor([event], date(2025, 1, 2)) == 1


async def test_the_provider_fills_the_adjusted_basis_and_leaves_the_traded_one() -> (
    None
):
    inner = FakeSessions([_close("2025-01-02", "10"), _close("2025-12-01", "20")])
    provider = DividendAdjustedPriceProvider(
        inner, FakeEvents([_event("2025-07-01", "10")])
    )

    prices = await provider.year_prices("PETR4", 2025)

    assert prices.nominal_avg == 15  # untouched: what actually printed
    assert prices.adjusted_avg == (Decimal(10) * Decimal("0.9") + Decimal(20)) / 2


async def test_a_company_that_never_paid_has_both_bases_in_one_number() -> None:
    # Saying so is more useful than leaving the column null: there is nothing to
    # put back, and the two rulers genuinely coincide.
    inner = FakeSessions([_close("2025-01-02", "10")])
    provider = DividendAdjustedPriceProvider(inner, FakeEvents([]))

    prices = await provider.year_prices("RDNI3", 2025)

    assert prices.nominal_avg == 10
    assert prices.adjusted_avg == 10


async def test_an_unmirrored_cash_history_does_not_claim_zero_distributions() -> None:
    inner = FakeSessions([_close("2025-01-02", "10")])
    provider = DividendAdjustedPriceProvider(inner, FakeEvents(None))

    prices = await provider.year_prices("RDNI3", 2025)

    assert prices.nominal_avg == 10
    assert prices.adjusted_avg is None


async def test_the_live_quote_passes_through_unadjusted() -> None:
    # It is today's price, and no payment comes after today to be put back.
    inner = FakeSessions([_close("2025-12-01", "20")])
    provider = DividendAdjustedPriceProvider(
        inner, FakeEvents([_event("2025-07-01", "10")])
    )

    assert (await provider.get("PETR4")).price == 20


async def test_a_year_with_no_sessions_is_left_alone() -> None:
    inner = FakeSessions([])
    provider = DividendAdjustedPriceProvider(
        inner, FakeEvents([_event("2025-07-01", "10")])
    )

    prices = await provider.year_prices("TAEE4", 2015)

    assert prices.nominal_avg is None
    assert prices.adjusted_avg is None


async def test_a_unit_has_no_dividend_basis_at_all() -> None:
    """B3 files a rate per class and none for the bundle (#38).

    Filling the column with the traded price would claim the two rulers
    coincide, which for a company that pays is the one thing certainly false.
    """
    inner = FakeSessions([_close("2025-01-02", "10")])
    provider = DividendAdjustedPriceProvider(
        inner, FakeEvents([]), unit_resolver=lambda ticker: ticker == "TAEE11"
    )

    prices = await provider.year_prices("TAEE11", 2025)

    assert prices.nominal_avg == 10
    assert prices.adjusted_avg is None


async def test_a_non_unit_suffix_11_security_does_not_take_the_unit_branch() -> None:
    inner = FakeSessions([_close("2025-01-02", "10")])
    provider = DividendAdjustedPriceProvider(inner, FakeEvents([]))

    prices = await provider.year_prices("BEEF11", 2025)

    assert prices.nominal_avg == 10
    assert prices.adjusted_avg == 10
