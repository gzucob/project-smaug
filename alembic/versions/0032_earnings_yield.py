"""Persist earnings yield without rewriting or deleting previous analysis runs."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0032"
down_revision: str | None = "0031"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "ticker_analysis", sa.Column("earnings_yield", sa.Numeric(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("ticker_analysis", "earnings_yield")
