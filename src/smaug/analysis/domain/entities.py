"""Analysis domain entity: the computed result for one ticker."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from smaug.analysis.domain.indicators import Indicators
from smaug.portfolio.domain.sectors import Sector

# The two analysis perspectives the system produces for each ticker:
# the live trailing-twelve-months snapshot and one row per closed fiscal year.
AnalysisView = Literal["ttm_live", "closed_year"]
VIEW_TTM: AnalysisView = "ttm_live"
VIEW_CLOSED_YEAR: AnalysisView = "closed_year"


@dataclass(frozen=True)
class TickerAnalysis:
    """Indicators for one ticker, tagged with the inputs' provenance."""

    ticker: str
    sector: Sector
    reference_date: date  # CVM period the fundamentals came from
    computed_at: datetime
    indicators: Indicators
    price: Decimal | None = None  # price used for the market multiples
    price_nominal: Decimal | None = None  # same period, nominal (unadjusted) basis
    price_basis: str | None = None  # how ``price`` was derived (e.g. adjusted_year_avg)
    view: AnalysisView = VIEW_TTM  # which perspective this row represents
