"""Persist current and non-current balance-sheet components."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0037"
down_revision: str | None = "0036"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "ticker_analysis", sa.Column("current_assets", sa.Numeric(), nullable=True)
    )
    op.add_column(
        "ticker_analysis",
        sa.Column("noncurrent_assets", sa.Numeric(), nullable=True),
    )
    op.add_column(
        "ticker_analysis",
        sa.Column("current_liabilities", sa.Numeric(), nullable=True),
    )
    op.add_column(
        "ticker_analysis",
        sa.Column("noncurrent_liabilities", sa.Numeric(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("ticker_analysis", "noncurrent_liabilities")
    op.drop_column("ticker_analysis", "current_liabilities")
    op.drop_column("ticker_analysis", "noncurrent_assets")
    op.drop_column("ticker_analysis", "current_assets")
