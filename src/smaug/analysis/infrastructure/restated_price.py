"""Put an as-traded price on the same share base as the counts it multiplies.

B3 publishes what actually printed on the tape, while the filed share counts are
restated onto the current base (ADR 0027). Without the same-base transformation,
every historical price before a split or grouping would multiply a count expressed
on another basis.

So an as-traded price needs the same treatment the counts already get, and
``cap = price x shares`` is what makes the arithmetic trivial: restating
multiplies the count by ``k``, so the price is divided by ``k`` and the cap does
not move at all. What moves is the per-share series and the price a screen shows.
This split-adjusted basis remains distinct from both the as-traded tape and the
dividend-adjusted total-return series.

**The factor is deliberately the one the counts used, not a better one.** Even a
more accurate factor would be the wrong choice: adjusting the price by a number
the count was not adjusted by breaks the invariance above, and a cap off by 10%
is worse than a per-share series that is uniformly one base behind. Improving the
chain means moving both sides together, which is an evolution of ADR 0027 and not
of this decorator.

**It is applied session by session, not to the year's average** (ADR 0033). An
action lands on a day: the closes before it are quoted on the old base and the
ones after it on the new, so a year *containing* an action has no single base to
restate. Dividing its average by the whole factor treats every session as
pre-action, which is why the residual against the vendor series used to grow with
the number of actions a company had — Bradesco, which pays a bonus most years,
came out 6% short. The year's one divisor is therefore session-weighted
(``average_factor``), and the count side keeps its yearly factor, which is all a
yearly series can carry.

Wrapped around the B3 provider only. The vendor chain arrives pre-adjusted, and
running it through here would adjust it twice.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from smaug.analysis.domain.capital import average_factor, factor_at
from smaug.analysis.domain.financials import MarketData, YearPrices
from smaug.analysis.domain.ports import SessionPriceProvider, SharesReader
from smaug.shared.logging import get_logger

logger = get_logger(__name__)


class RestatedPriceProvider:
    """A ``PriceProvider`` that divides an as-traded year price by its restatement.

    The live quote passes through untouched: it is already on today's base, which
    is the base everything is restated *to*.
    """

    def __init__(
        self, inner: SessionPriceProvider, shares_reader: SharesReader
    ) -> None:
        self._inner = inner
        self._shares = shares_reader

    async def get(self, ticker: str) -> MarketData:
        return await self._inner.get(ticker)

    async def year_prices(self, ticker: str, year: int) -> YearPrices:
        prices = await self._inner.year_prices(ticker, year)
        if prices.nominal_avg is None and prices.adjusted_avg is None:
            # Nothing to restate, and asking for a timeline would read the mirror
            # for a company whose price is missing anyway.
            return prices
        factor = await self._factor(ticker, year)
        if factor == 1:
            return prices
        logger.info(
            "Restating %s %d price onto the current share base (/%s, ADR 0027)",
            ticker,
            year,
            factor,
        )
        return YearPrices(
            nominal_avg=_divided(prices.nominal_avg, factor),
            adjusted_avg=_divided(prices.adjusted_avg, factor),
            null_reason=prices.null_reason,
        )

    async def _factor(self, ticker: str, year: int) -> Decimal:
        """The one divisor for ``year``'s average, weighted by the sessions in it.

        A year with no action inside it has every session on one base and the
        weighting collapses to that base's factor — which is what makes this a
        strict refinement of the yearly factor rather than a different rule.
        Falls back to the year-end factor when the source publishes an average
        with no sessions behind it, which only a non-B3 inner could do.
        """
        timeline = await self._shares.restatement_timeline(ticker)
        if not timeline:
            return Decimal(1)
        sessions = await self._inner.year_sessions(ticker, year)
        if not sessions:
            return factor_at(timeline, date(year, 12, 31))
        return average_factor(timeline, sessions)


def _divided(price: Decimal | None, factor: Decimal) -> Decimal | None:
    return None if price is None else price / factor
