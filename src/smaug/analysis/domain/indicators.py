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
    roic: Decimal | None = None  # NOPAT (EBIT·(1−tax)) / invested capital
    net_margin: Decimal | None = None
    gross_margin: Decimal | None = None
    ebit_margin: Decimal | None = None
    ebitda_margin: Decimal | None = None
    asset_turnover: Decimal | None = None  # revenue / total assets
    # Per share
    eps: Decimal | None = None  # LPA — earnings per share
    bvps: Decimal | None = None  # VPA — book value per share
    # Leverage / liquidity
    net_debt: Decimal | None = None
    net_debt_to_ebitda: Decimal | None = None
    debt_to_equity: Decimal | None = None  # gross debt / equity
    liabilities_to_assets: Decimal | None = None  # (assets − equity) / assets
    current_ratio: Decimal | None = None
    # Growth (needs a prior comparable period)
    revenue_growth: Decimal | None = None
    net_income_growth: Decimal | None = None
    # Market multiples
    pe: Decimal | None = None
    pb: Decimal | None = None
    psr: Decimal | None = None  # P/Receita — price / sales
    price_to_assets: Decimal | None = None
    price_to_ebit: Decimal | None = None
    price_to_working_capital: Decimal | None = None
    payout: Decimal | None = None  # dividends paid / net income
    dividend_yield: Decimal | None = None
    ev_ebitda: Decimal | None = None
    # Free cash flow (CFO − capex)
    fcf: Decimal | None = None  # annualized free cash flow, in absolute reais
    price_to_fcf: Decimal | None = None
    fcf_yield: Decimal | None = None
