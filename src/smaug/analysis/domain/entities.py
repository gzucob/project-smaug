"""Analysis domain entity: the computed result for one ticker."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from smaug.analysis.domain.indicators import Indicators
from smaug.portfolio.domain.sectors import Sector


@dataclass(frozen=True)
class TickerAnalysis:
    """Indicators for one ticker, tagged with the inputs' provenance."""

    ticker: str
    sector: Sector
    reference_date: date  # CVM period the fundamentals came from
    computed_at: datetime
    indicators: Indicators
    price: Decimal | None = None  # brapi price used for the market multiples
