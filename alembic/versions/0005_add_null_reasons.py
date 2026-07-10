"""add null_reasons to ticker_analysis

One JSON map per row: the cause of each null indicator, keyed by column name,
holding the #30 NullReason vocabulary. NULL on rows computed before the
vocabulary existed — those nulls stay unclassified until the next analyze run.

Revision ID: 0005
Revises: 0004
Create Date: 2026-07-10
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "ticker_analysis",
        sa.Column("null_reasons", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("ticker_analysis", "null_reasons")
