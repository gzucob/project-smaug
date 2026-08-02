"""The dividend-adjusted price basis: what a holding returned, not what it cost.

Three price bases exist and must never be mixed (ADR 0032): the price **as
traded**, the price **restated for corporate actions** — which is what the
valuation multiples divide by (ADR 0018) — and this one, the price with cash
taken back out of it.

A dividend is not a corporate action. A split hands the holder more of the same
claim and the price falls to match, so the two sides cancel and nothing is
gained or lost. A dividend takes value *out of the company* and gives it to the
holder: the price falls and the holder is not poorer. Reading a price series
without putting that cash back makes every dividend look like a loss, which is
why a total-return ruler exists at all.

B3 publishes what the adjustment needs and does the arithmetic itself. Each cash
event carries the closing price of the last session before it went ex and, from
those two, ``corporateActionPrice`` — the share of that price the payment
represented. That percentage is the factor, and taking it as filed rather than
recomputing it keeps the reading on the exchange's own numbers, including for
the quarter of Bradesco's history quoted per lot of a thousand shares, where the
payment and the price are both per lot and the ratio cancels the scale away.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from smaug.analysis.domain.financials import SessionClose

_PERCENT = Decimal(100)


@dataclass(frozen=True, slots=True)
class CashEvent:
    """One cash payment, dated by the session it first traded without.

    ``percentage`` is B3's own ``corporateActionPrice``: the payment as a share
    of the closing price it went ex against. It is never a ratio of counts and
    never touches the share base — the number of shares does not move when a
    company pays.
    """

    effective: date
    percentage: Decimal


def dividend_factor(events: Sequence[CashEvent], session: date) -> Decimal:
    """What a close printed on ``session`` is multiplied by to include its payouts.

    Every payment that went ex *after* the session took cash the holder of that
    session's share went on to receive, so the close is scaled down by each of
    them — the same direction, and the same "everything that postdates it" rule,
    as the corporate-action restatement (ADR 0033).
    """
    factor = Decimal(1)
    for event in events:
        if event.effective > session:
            factor *= 1 - event.percentage / _PERCENT
    return factor


def average_dividend_factor(
    events: Sequence[CashEvent], sessions: Sequence[SessionClose]
) -> Decimal:
    """The one factor a *year's average* takes, weighted by the sessions in it.

    A year is one number and its sessions do not all carry the same payouts
    ahead of them, so the factor that turns the year's average into the mean of
    the adjusted closes is ``Σ(p×f) / Σp`` — the mirror of the restatement's
    weighting, which divides where this multiplies.
    """
    traded = sum((s.close for s in sessions), Decimal(0))
    adjusted = sum(
        (s.close * dividend_factor(events, s.session) for s in sessions), Decimal(0)
    )
    if traded <= 0 or adjusted <= 0:
        return Decimal(1)
    return adjusted / traded
