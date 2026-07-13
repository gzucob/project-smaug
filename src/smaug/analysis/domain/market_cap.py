"""What the company is worth: the sum over its listed share classes (ADR 0014).

A quote prices one class of shares, not the company. Multiplying a single quote
by every share the company filed therefore misprices any company that lists more
than one class — it pays the ON price for the PN shares, or the reverse. And for
a unit (SAPR11, TAEE11) it has nothing to multiply at all, because the quoted
bundle has no share count of its own. Both cases are the same mistake, and both
are fixed by the same identity:

    cap = Σ over listed classes (class price × shares filed for that class)

Summing class by class needs no bundle composition, which is why a unit is
capitalized here without #38 being solved first.

A class whose price or share count is missing makes the **whole** cap null, never
a partial company: half a capitalization is a wrong number, and a wrong number is
worse than a named null (the project's ``None``-over-sentinel rule). Which of the
two inputs went missing is returned alongside, so the null keeps a cause (#30).
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal

from smaug.analysis.domain.financials import ShareCounts
from smaug.analysis.domain.indicators import NullReason
from smaug.portfolio.domain.share_classes import listed_classes


def capitalize(
    ticker: str,
    counts: ShareCounts | None,
    prices: Mapping[str, Decimal | None],
) -> tuple[Decimal | None, NullReason | None]:
    """Capitalize ``ticker``'s company from its class prices and filed counts.

    ``prices`` is keyed by class symbol (``PETR3``, ``PETR4``). Returns the cap
    and ``None``, or ``None`` and the reason it could not be built.
    """
    classes = listed_classes(ticker)
    if not classes:
        # No composition on file for this ticker: we do not know what shares to
        # price, so the cap is unknown rather than guessed.
        return None, NullReason.MISSING_SHARE_COUNT
    if counts is None:
        return None, NullReason.MISSING_SHARE_COUNT

    cap = Decimal(0)
    for share_class in classes:
        count = counts.of(share_class.kind)
        if count is None:
            return None, NullReason.MISSING_SHARE_COUNT
        price = prices.get(share_class.symbol)
        if price is None:
            return None, NullReason.MISSING_PRICE
        cap += price * count
    return cap, None
