"""add extended indicators to ticker_analysis

Adds the derived per-share, extra profitability/leverage/multiple indicators and
the free-cash-flow family. Every column is a nullable Numeric — a null stays
meaningful (not applicable to the sector, or input missing).

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-08
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

_NEW_COLUMNS = (
    "roic",
    "ebit_margin",
    "asset_turnover",
    "eps",
    "bvps",
    "debt_to_equity",
    "liabilities_to_assets",
    "psr",
    "price_to_assets",
    "price_to_ebit",
    "price_to_working_capital",
    "payout",
    "fcf",
    "price_to_fcf",
    "fcf_yield",
    "revenue",
    "net_income",
    "dividends",
)


def upgrade() -> None:
    for name in _NEW_COLUMNS:
        op.add_column(
            "ticker_analysis",
            sa.Column(name, sa.Numeric(), nullable=True),
        )


def downgrade() -> None:
    for name in reversed(_NEW_COLUMNS):
        op.drop_column("ticker_analysis", name)
