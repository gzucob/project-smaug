"""add view discriminator to ticker_analysis

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-08
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    # Existing rows are all the live TTM view, so backfill them via server_default.
    op.add_column(
        "ticker_analysis",
        sa.Column(
            "view",
            sa.String(length=16),
            nullable=False,
            server_default="ttm_live",
        ),
    )
    op.create_index(
        "ix_ticker_analysis_ticker_view",
        "ticker_analysis",
        ["ticker", "view"],
    )


def downgrade() -> None:
    op.drop_index("ix_ticker_analysis_ticker_view", table_name="ticker_analysis")
    op.drop_column("ticker_analysis", "view")
