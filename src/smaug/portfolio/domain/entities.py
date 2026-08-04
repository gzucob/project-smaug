"""Portfolio domain entity: one ticker the user has favorited (#151)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class PortfolioTicker:
    """One ticker the user chose to watch — stored, not compiled (#151, ADR 0049).

    ``ticker`` is the only identity: this table holds membership, not history —
    a ticker is either in the portfolio or it is not, unlike ``ticker_analysis``,
    which keeps every computation. ``added_at`` is when the user favorited it,
    kept for a stable, meaningful default order (oldest favorite first).
    """

    ticker: str
    added_at: datetime
