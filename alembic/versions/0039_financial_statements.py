"""Persist selected income and cash-flow statement lines."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0039"
down_revision: str | None = "0038"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "ticker_analysis", sa.Column("income_statement", sa.JSON(), nullable=True)
    )
    op.add_column(
        "ticker_analysis", sa.Column("cash_flow_statement", sa.JSON(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("ticker_analysis", "cash_flow_statement")
    op.drop_column("ticker_analysis", "income_statement")
