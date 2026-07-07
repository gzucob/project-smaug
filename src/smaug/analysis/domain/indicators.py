"""The computed indicators (Phase 2 output).

Pure value object. Every field is ``Decimal | None`` — ``None`` means "not
applicable to this sector" (e.g. net debt for a bank) or "input missing", never
zero. Ratios are fractions (0.18 = 18%), not percentages, so the presentation
layer decides formatting.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class Indicators:
    """Fundamental + market indicators for one ticker at one point in time."""

    # Profitability
    roe: Decimal | None = None
    roa: Decimal | None = None
    net_margin: Decimal | None = None
    gross_margin: Decimal | None = None
    ebitda_margin: Decimal | None = None
    # Leverage / liquidity
    net_debt: Decimal | None = None
    net_debt_to_ebitda: Decimal | None = None
    current_ratio: Decimal | None = None
    # Growth (needs a prior comparable period)
    revenue_growth: Decimal | None = None
    net_income_growth: Decimal | None = None
    # Market multiples
    pe: Decimal | None = None
    pb: Decimal | None = None
    dividend_yield: Decimal | None = None
    ev_ebitda: Decimal | None = None
