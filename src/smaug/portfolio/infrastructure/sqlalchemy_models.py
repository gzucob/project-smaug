"""SQLAlchemy ORM for the user's portfolio (#151, ADR 0049).

A separate ``DeclarativeBase`` from ``analysis``'s — the two contexts share one
Postgres database but never one schema/model file, matching how Mongo and
Postgres models never leak across contexts either (``RULES_ENTITIES.md``).
``alembic/env.py`` targets both bases' metadata.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class PortfolioBase(DeclarativeBase):
    """Declarative base for the portfolio schema."""


class PortfolioTickerRow(PortfolioBase):
    """One favorited ticker. Membership, not history — no id, ``ticker`` is the key."""

    __tablename__ = "portfolio"

    ticker: Mapped[str] = mapped_column(String(12), primary_key=True)
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
