"""Analysis domain entity: the computed result for one ticker."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from smaug.analysis.domain.indicators import Indicators
from smaug.portfolio.domain.taxonomy import Classification

# The two analysis perspectives the system produces for each ticker:
# the live trailing-twelve-months snapshot and one row per closed fiscal year.
AnalysisView = Literal["ttm_live", "closed_year"]
VIEW_TTM: AnalysisView = "ttm_live"
VIEW_CLOSED_YEAR: AnalysisView = "closed_year"


@dataclass(frozen=True)
class TickerAnalysis:
    """Indicators for one ticker, tagged with the inputs' provenance."""

    ticker: str
    # The B3 economic taxonomy (setor → subsetor → segmento), or the CVM
    # single-level fallback for a ticker outside the snapshot (ADR 0024). Replaces
    # the old five-value ``Sector`` enum, which survives only as an internal
    # regime hint on ``StandardizedFinancials``.
    classification: Classification
    reference_date: date  # CVM period the fundamentals came from
    computed_at: datetime
    indicators: Indicators
    # The price the market multiples divide by: what the shares actually traded at —
    # the live quote, or the closed year's nominal average (ADR 0018).
    price: Decimal | None = None
    # The same year's dividend-adjusted average: a total-return ruler, not a
    # valuation one. Kept for return comparisons; ``None`` for the live view, which
    # has had no payout since to adjust for.
    price_adjusted: Decimal | None = None
    price_basis: str | None = None  # how ``price`` was derived (e.g. nominal_year_avg)
    view: AnalysisView = VIEW_TTM  # which perspective this row represents
