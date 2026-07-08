"""initial ticker_analysis table

Revision ID: 0001
Revises:
Create Date: 2026-07-07
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

_INDICATORS = (
    "roe",
    "roa",
    "net_margin",
    "gross_margin",
    "ebitda_margin",
    "net_debt",
    "net_debt_to_ebitda",
    "current_ratio",
    "revenue_growth",
    "net_income_growth",
    "pe",
    "pb",
    "dividend_yield",
    "ev_ebitda",
)


def upgrade() -> None:
    op.create_table(
        "ticker_analysis",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("ticker", sa.String(length=12), nullable=False),
        sa.Column("sector", sa.String(length=20), nullable=False),
        sa.Column("reference_date", sa.Date(), nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("price", sa.Numeric(), nullable=True),
        *(sa.Column(name, sa.Numeric(), nullable=True) for name in _INDICATORS),
    )
    op.create_index(op.f("ix_ticker_analysis_ticker"), "ticker_analysis", ["ticker"])
    op.create_index(
        op.f("ix_ticker_analysis_computed_at"), "ticker_analysis", ["computed_at"]
    )


def downgrade() -> None:
    op.drop_table("ticker_analysis")
