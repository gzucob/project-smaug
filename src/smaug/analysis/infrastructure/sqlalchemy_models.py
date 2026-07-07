"""SQLAlchemy ORM for the derived indicators (Phase 2 persistence).

One row per (ticker, computation): each ``analyze`` run inserts a fresh row with
its ``computed_at``, so history is preserved and "latest" is just the newest row
per ticker. Every indicator is a nullable ``Numeric`` — nulls are meaningful
(not applicable to the sector, or input missing).
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, Numeric, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Declarative base for the analysis schema."""


class TickerAnalysisRow(Base):
    """A computed indicator snapshot for one ticker."""

    __tablename__ = "ticker_analysis"

    id: Mapped[int] = mapped_column(primary_key=True)
    ticker: Mapped[str] = mapped_column(String(12), index=True)
    sector: Mapped[str] = mapped_column(String(20))
    reference_date: Mapped[date] = mapped_column(Date)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    price: Mapped[Decimal | None] = mapped_column(Numeric)

    roe: Mapped[Decimal | None] = mapped_column(Numeric)
    roa: Mapped[Decimal | None] = mapped_column(Numeric)
    net_margin: Mapped[Decimal | None] = mapped_column(Numeric)
    gross_margin: Mapped[Decimal | None] = mapped_column(Numeric)
    ebitda_margin: Mapped[Decimal | None] = mapped_column(Numeric)
    net_debt: Mapped[Decimal | None] = mapped_column(Numeric)
    net_debt_to_ebitda: Mapped[Decimal | None] = mapped_column(Numeric)
    current_ratio: Mapped[Decimal | None] = mapped_column(Numeric)
    revenue_growth: Mapped[Decimal | None] = mapped_column(Numeric)
    net_income_growth: Mapped[Decimal | None] = mapped_column(Numeric)
    pe: Mapped[Decimal | None] = mapped_column(Numeric)
    pb: Mapped[Decimal | None] = mapped_column(Numeric)
    dividend_yield: Mapped[Decimal | None] = mapped_column(Numeric)
    ev_ebitda: Mapped[Decimal | None] = mapped_column(Numeric)
