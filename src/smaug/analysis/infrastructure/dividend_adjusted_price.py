"""Fill the dividend-adjusted basis, which B3's quote file does not carry.

B3 publishes one price series and it is the price as traded (ADR 0032). The
second basis — restated for corporate actions — is derived from the share
history by ``RestatedPriceProvider``. This is the third: the same series with
the cash the company paid out put back into it, which is what a total-return
ruler means and the only basis on which a price chart does not read a dividend
as a loss (ADR 0018).

It wraps the B3 provider and fills ``adjusted_avg``, leaving ``nominal_avg``
exactly as it found it. ``RestatedPriceProvider`` then divides both by the share
restatement, so the two bases stay on one share base and differ only by the
cash. Order matters and is fixed at the composition root: dividends first,
restatement outermost.

The live quote is not adjusted. It is today's price, and there is no payment
after today to put back.

**A unit has no basis here at all.** B3 files the total-return percentage per
*class* — one for ON, another for PN — and none for the bundle. Absolute class
amounts can compose the unit's dividend yield (ADR 0055), but they do not make
those class percentages a unit percentage: that would also require the unit
price at every ex event and the bundle composition in force on that date.
Leaving this column null says that; filling it with the traded price would claim
the two rulers coincide, which for a payer is the one thing certainly false.
"""

from __future__ import annotations

from decimal import Decimal

from smaug.analysis.domain.dividends import average_dividend_factor
from smaug.analysis.domain.financials import MarketData, SessionClose, YearPrices
from smaug.analysis.domain.ports import CashEventReader, SessionPriceProvider
from smaug.portfolio.domain.company import UnitResolver, no_units
from smaug.shared.logging import get_logger

logger = get_logger(__name__)


class DividendAdjustedPriceProvider:
    """A ``SessionPriceProvider`` that publishes the total-return basis too."""

    def __init__(
        self,
        inner: SessionPriceProvider,
        events: CashEventReader,
        *,
        unit_resolver: UnitResolver = no_units,
    ) -> None:
        self._inner = inner
        self._events = events
        self._is_unit = unit_resolver

    async def get(self, ticker: str) -> MarketData:
        return await self._inner.get(ticker)

    async def year_sessions(self, ticker: str, year: int) -> tuple[SessionClose, ...]:
        return tuple(await self._inner.year_sessions(ticker, year))

    async def year_prices(self, ticker: str, year: int) -> YearPrices:
        prices = await self._inner.year_prices(ticker, year)
        if prices.nominal_avg is None or self._is_unit(ticker):
            return prices
        events = await self._events.cash_events(ticker)
        if events is None:
            # Missing mirror coverage is not evidence of an empty payment history.
            return prices
        if not events:
            # A company that has never paid has both bases in one number, and
            # saying so is more useful than leaving the column null.
            return YearPrices(
                nominal_avg=prices.nominal_avg,
                adjusted_avg=prices.nominal_avg,
                closing=prices.closing,
                closing_session=prices.closing_session,
                closing_code=prices.closing_code,
                null_reason=prices.null_reason,
            )
        sessions = await self._inner.year_sessions(ticker, year)
        if not sessions:
            return prices
        factor = average_dividend_factor(events, sessions)
        logger.info(
            "Putting %s %d's payouts back into the price (x%s, ADR 0039)",
            ticker,
            year,
            factor,
        )
        return YearPrices(
            nominal_avg=prices.nominal_avg,
            adjusted_avg=_scaled(prices.nominal_avg, factor),
            closing=prices.closing,
            closing_session=prices.closing_session,
            closing_code=prices.closing_code,
            null_reason=prices.null_reason,
        )


def _scaled(price: Decimal | None, factor: Decimal) -> Decimal | None:
    return None if price is None else price * factor
