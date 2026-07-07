"""add price_nominal and price_basis to ticker_analysis

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-07
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "ticker_analysis",
        sa.Column("price_nominal", sa.Numeric(), nullable=True),
    )
    op.add_column(
        "ticker_analysis",
        sa.Column("price_basis", sa.String(length=24), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("ticker_analysis", "price_basis")
    op.drop_column("ticker_analysis", "price_nominal")
