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

from smaug.analysis.domain.capital import RestatementStep, factor_at
from smaug.analysis.domain.financials import SessionClose

_PERCENT = Decimal(100)


@dataclass(frozen=True, slots=True)
class CashEvent:
    """One cash payment, dated by the session it first traded without.

    ``percentage`` is B3's own ``corporateActionPrice``: the payment as a share
    of the closing price it went ex against. ``amount_per_share`` is B3's cash
    value divided by its explicit 1/1,000-share quotation scale. The former
    rebuilds a total-return price; the latter is what per-security dividend
    yield sums. Either can be absent without inventing the other.

    ``last_with_right`` preserves the price/share base on which B3 quoted the
    amount. A split or bonus taking effect on the ex session must divide that
    amount as well as the preceding close before historical yield can compare
    them on today's base.
    """

    effective: date
    percentage: Decimal | None = None
    amount_per_share: Decimal | None = None
    last_with_right: date | None = None
    approval_date: date | None = None


def dividend_factor(events: Sequence[CashEvent], session: date) -> Decimal:
    """What a close printed on ``session`` is multiplied by to include its payouts.

    Every payment that went ex *after* the session took cash the holder of that
    session's share went on to receive, so the close is scaled down by each of
    them — the same direction, and the same "everything that postdates it" rule,
    as the corporate-action restatement (ADR 0033).
    """
    factor = Decimal(1)
    for event in events:
        if event.effective > session and event.percentage is not None:
            factor *= 1 - event.percentage / _PERCENT
    return factor


def cash_distributions(
    events: Sequence[CashEvent],
    start: date,
    end: date,
    timeline: Sequence[RestatementStep] = (),
) -> Decimal | None:
    """Cash rights that went ex in ``[start, end]``, per current-base share.

    The event date identifies which holder earned the cash. Every B3 amount is
    divided by the share-base changes that followed its last cum-right session,
    matching the basis used by ``RestatedPriceProvider``. A relevant row whose
    amount cannot be parsed voids the total instead of being silently skipped.
    No relevant rows is the economic zero.
    """
    total = Decimal(0)
    for event in events:
        if not start <= event.effective <= end:
            continue
        if event.amount_per_share is None:
            return None
        base_date = event.last_with_right or event.effective
        total += event.amount_per_share / factor_at(timeline, base_date)
    return total


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
