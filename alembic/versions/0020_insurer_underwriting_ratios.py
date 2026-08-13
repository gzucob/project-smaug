"""add insurer underwriting ratios (#98)

Revision ID: 0020
Revises: 0019
Create Date: 2026-08-13

The columns are new derived outputs. Existing rows remain null until
``smaug analyze`` rebuilds them from the preserved CVM statement mirror.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "ticker_analysis", sa.Column("loss_ratio", sa.Numeric(), nullable=True)
    )
    op.add_column(
        "ticker_analysis", sa.Column("combined_ratio", sa.Numeric(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("ticker_analysis", "combined_ratio")
    op.drop_column("ticker_analysis", "loss_ratio")
