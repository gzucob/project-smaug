"""Put an as-traded price on the same share base as the counts it multiplies.

B3 publishes what actually printed on the tape. Yahoo publishes the same series
**back-adjusted for every corporate action**, and ADR 0027 built on that: share
counts are restated onto the current base precisely because "the price's base is,
irrevocably, the current one". Measured over 308 cells, that difference is not
marginal — BBAS3 diverges by exactly 2x in all ten years, VIVT3 in all eleven,
HAPV3 by 15x, and Bradesco's annual bonuses compound backwards year on year.

So an as-traded price needs the same treatment the counts already get, and
``cap = price x shares`` is what makes the arithmetic trivial: restating
multiplies the count by ``k``, so the price is divided by ``k`` and the cap does
not move at all. What moves is the per-share series and the price a screen shows
— which is exactly the pair of bases the reference platforms let a reader toggle
between ("Cotação padrão" / "Cotação ajustada").

**The factor is deliberately the one the counts used, not a better one.** B3's own
corporate-event feed is richer in principle and incomplete in practice — it lists
one Bradesco bonus where the price ratio proves there were many — but even a
perfect factor would be the wrong choice here: adjusting the price by a number the
count was not adjusted by breaks the invariance above, and a cap off by 10% is
worse than a per-share series that is uniformly one base behind. Improving the
chain means moving both sides together, which is an evolution of ADR 0027 and not
of this decorator.

Wrapped around the B3 provider only. The vendor chain arrives pre-adjusted, and
running it through here would adjust it twice.
"""

from __future__ import annotations

from decimal import Decimal

from smaug.analysis.domain.financials import MarketData, YearPrices
from smaug.analysis.domain.ports import PriceProvider, SharesReader
from smaug.shared.logging import get_logger

logger = get_logger(__name__)


class RestatedPriceProvider:
    """A ``PriceProvider`` that divides an as-traded year price by its restatement.

    The live quote passes through untouched: it is already on today's base, which
    is the base everything is restated *to*.
    """

    def __init__(self, inner: PriceProvider, shares_reader: SharesReader) -> None:
        self._inner = inner
        self._shares = shares_reader

    async def get(self, ticker: str) -> MarketData:
        return await self._inner.get(ticker)

    async def year_prices(self, ticker: str, year: int) -> YearPrices:
        prices = await self._inner.year_prices(ticker, year)
        if prices.nominal_avg is None and prices.adjusted_avg is None:
            # Nothing to restate, and asking for a factor would read the mirror
            # for a company whose price is missing anyway.
            return prices
        factor = await self._shares.restatement_factor(ticker, year)
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


def _divided(price: Decimal | None, factor: Decimal) -> Decimal | None:
    return None if price is None else price / factor
