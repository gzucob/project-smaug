"""Chart-of-accounts drift across a ticker's closed years (#156).

``smaug doctor`` answers "what is true right now" for one exercise at a time,
which is exactly the question that cannot catch a needle going stale. The mapper
reads the CVM statements by code and by label, and filers change both: when a
needle stops matching, nothing raises — the account reads ``None``, the indicator
reads null, and the null is classified ``source_account_absent``, "the filing has
no such line". That message accuses the source for a gap that is ours, and on
screen it is indistinguishable from a line the filer genuinely does not publish
(#140, #155 — both found by someone looking at a screen, neither by a command).

**The signal is the transition, not the level.** An account read for every year
from 2020 to 2025 and absent in 2019 is a needle that does not reach the older
chart; an account absent in *every* year is a line that filer does not publish,
and reporting it would bury the first kind under the second. So only accounts
with *both* kinds of year are reported, and the report names the boundary rather
than a direction — a needle can fail on the old filings (the mapper is written
against recent ones) or on the new (the filer changed under it), and which one
happened is the reader's call, not a guess this module should make.

Read-only, like ``doctor``: it reads standardized accounts back through the
``FundamentalsReader`` port and never recomputes or persists. It reports on the
**accounts**, not the indicators — an indicator can be null for a dozen reasons,
an account either mapped or did not.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, fields
from decimal import Decimal

from smaug.analysis.domain.financials import StandardizedFinancials
from smaug.analysis.domain.ports import FundamentalsReader

# The mapped accounts a drift report is about: every numeric line the mapper
# fills. Derived from the entity rather than listed, so a newly mapped account is
# watched the day it is added — the failure mode #140 was.
_ACCOUNT_FIELDS: tuple[str, ...] = tuple(
    f.name for f in fields(StandardizedFinancials) if f.type == "Decimal | None"
)


@dataclass(frozen=True)
class AccountDrift:
    """One account that mapped in some of a ticker's closed years and not others.

    ``read``/``missing`` are the years of each kind, oldest→newest. ``boundaries``
    counts the year-to-year changes: exactly one is the signature of a chart
    generation changing, more than one is usually a line the filer reports only
    when it has something to report.
    """

    account: str
    read: tuple[int, ...]
    missing: tuple[int, ...]
    boundaries: int

    @property
    def missing_side(self) -> str:
        """Which end of the series lacks the account — where to look first.

        ``older`` is the classic stale needle: the mapper is written against the
        recent filings, so it is the old chart it fails to reach (#155). ``newer``
        means the account stopped mapping in filings we do read, which is the more
        urgent of the two. ``mixed`` is neither, and needs a human.
        """
        if not self.read or not self.missing:
            return "mixed"
        if max(self.missing) < min(self.read):
            return "older"
        if min(self.missing) > max(self.read):
            return "newer"
        return "mixed"


@dataclass(frozen=True)
class TickerDrift:
    """Every drifting account for one ticker, plus the years examined."""

    ticker: str
    years: tuple[int, ...]
    accounts: tuple[AccountDrift, ...]


@dataclass(frozen=True)
class DriftReport:
    """The full drift report: one entry per requested ticker."""

    tickers: tuple[TickerDrift, ...]

    @property
    def drifting(self) -> int:
        return sum(len(t.accounts) for t in self.tickers)


def _drift_of(annuals: list[StandardizedFinancials]) -> tuple[AccountDrift, ...]:
    """Compare each account's presence across one ticker's closed years."""
    years = tuple(f.reference_date.year for f in annuals)
    drifts: list[AccountDrift] = []
    for account in _ACCOUNT_FIELDS:
        present = tuple(_is_read(getattr(f, account)) for f in annuals)
        # Both kinds of year, or there is nothing to compare: an account read
        # everywhere is healthy, one read nowhere is a line the filer omits.
        if all(present) or not any(present):
            continue
        boundaries = sum(
            1 for i in range(1, len(present)) if present[i] != present[i - 1]
        )
        drifts.append(
            AccountDrift(
                account=account,
                read=tuple(y for y, p in zip(years, present, strict=True) if p),
                missing=tuple(y for y, p in zip(years, present, strict=True) if not p),
                boundaries=boundaries,
            )
        )
    return tuple(drifts)


def _is_read(value: Decimal | None) -> bool:
    """Whether the mapper found the account. A filed zero counts as found."""
    return value is not None


class AccountDriftUseCase:
    """Build the chart-of-accounts drift report (read-only)."""

    def __init__(self, reader: FundamentalsReader) -> None:
        self._reader = reader

    async def execute(self, tickers: Iterable[str]) -> DriftReport:
        reports: list[TickerDrift] = []
        for ticker in tickers:
            annuals = await self._reader.annuals(ticker)
            # One closed year has no transition to show; the comparison needs two.
            accounts = _drift_of(annuals) if len(annuals) > 1 else ()
            reports.append(
                TickerDrift(
                    ticker=ticker,
                    years=tuple(f.reference_date.year for f in annuals),
                    accounts=accounts,
                )
            )
        return DriftReport(tuple(reports))
