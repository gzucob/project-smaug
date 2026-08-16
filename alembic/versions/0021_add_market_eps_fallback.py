"""add the market-convention EPS/P-E fallback (#251)

Revision ID: 0021
Revises: 0020
Create Date: 2026-08-15

The strict CPC 41 fields remain unchanged. These columns hold a separate
closing-share estimate that the application may surface only when the strict
TTM result is unavailable.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "ticker_analysis", sa.Column("eps_basic_market", sa.Numeric(), nullable=True)
    )
    op.add_column(
        "ticker_analysis", sa.Column("pe_basic_market", sa.Numeric(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("ticker_analysis", "pe_basic_market")
    op.drop_column("ticker_analysis", "eps_basic_market")
