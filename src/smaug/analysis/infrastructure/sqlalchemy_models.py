"""SQLAlchemy ORM for the derived indicators (Phase 2 persistence).

One row per (ticker, computation): each ``analyze`` run inserts a fresh row with
its ``computed_at``, so history is preserved and "latest" is just the newest row
per ticker. Every indicator is a nullable ``Numeric`` — nulls are meaningful
(not applicable to the sector, or input missing).
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import JSON, Date, DateTime, Numeric, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Declarative base for the analysis schema."""


class TickerAnalysisRow(Base):
    """A computed indicator snapshot for one ticker."""

    __tablename__ = "ticker_analysis"

    id: Mapped[int] = mapped_column(primary_key=True)
    ticker: Mapped[str] = mapped_column(String(12), index=True)
    view: Mapped[str] = mapped_column(String(16), index=True)  # ttm_live | closed_year
    sector: Mapped[str] = mapped_column(String(20))
    reference_date: Mapped[date] = mapped_column(Date)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    price: Mapped[Decimal | None] = mapped_column(Numeric)
    price_adjusted: Mapped[Decimal | None] = mapped_column(Numeric)
    price_basis: Mapped[str | None] = mapped_column(String(24))

    roe: Mapped[Decimal | None] = mapped_column(Numeric)
    roa: Mapped[Decimal | None] = mapped_column(Numeric)
    roic: Mapped[Decimal | None] = mapped_column(Numeric)
    net_margin: Mapped[Decimal | None] = mapped_column(Numeric)
    gross_margin: Mapped[Decimal | None] = mapped_column(Numeric)
    ebit_margin: Mapped[Decimal | None] = mapped_column(Numeric)
    ebitda_margin: Mapped[Decimal | None] = mapped_column(Numeric)
    asset_turnover: Mapped[Decimal | None] = mapped_column(Numeric)
    eps: Mapped[Decimal | None] = mapped_column(Numeric)
    bvps: Mapped[Decimal | None] = mapped_column(Numeric)
    net_debt: Mapped[Decimal | None] = mapped_column(Numeric)
    net_debt_to_ebitda: Mapped[Decimal | None] = mapped_column(Numeric)
    debt_to_equity: Mapped[Decimal | None] = mapped_column(Numeric)
    liabilities_to_assets: Mapped[Decimal | None] = mapped_column(Numeric)
    current_ratio: Mapped[Decimal | None] = mapped_column(Numeric)
    revenue_growth: Mapped[Decimal | None] = mapped_column(Numeric)
    net_income_growth: Mapped[Decimal | None] = mapped_column(Numeric)
    pe: Mapped[Decimal | None] = mapped_column(Numeric)
    pb: Mapped[Decimal | None] = mapped_column(Numeric)
    psr: Mapped[Decimal | None] = mapped_column(Numeric)
    price_to_assets: Mapped[Decimal | None] = mapped_column(Numeric)
    price_to_ebit: Mapped[Decimal | None] = mapped_column(Numeric)
    price_to_working_capital: Mapped[Decimal | None] = mapped_column(Numeric)
    payout: Mapped[Decimal | None] = mapped_column(Numeric)
    dividend_yield: Mapped[Decimal | None] = mapped_column(Numeric)
    ev_ebitda: Mapped[Decimal | None] = mapped_column(Numeric)
    fcf: Mapped[Decimal | None] = mapped_column(Numeric)
    price_to_fcf: Mapped[Decimal | None] = mapped_column(Numeric)
    fcf_yield: Mapped[Decimal | None] = mapped_column(Numeric)
    net_interest_margin: Mapped[Decimal | None] = mapped_column(Numeric)
    efficiency_ratio: Mapped[Decimal | None] = mapped_column(Numeric)
    cost_of_risk: Mapped[Decimal | None] = mapped_column(Numeric)
    revenue: Mapped[Decimal | None] = mapped_column(Numeric)
    net_income: Mapped[Decimal | None] = mapped_column(Numeric)
    dividends: Mapped[Decimal | None] = mapped_column(Numeric)
    # Cause per null indicator, keyed by column name (#30's NullReason values).
    # NULL on rows computed before the vocabulary existed.
    null_reasons: Mapped[dict[str, str] | None] = mapped_column(JSON)
