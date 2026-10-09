"""Persist the filed DRE 3.02 amount for the historical income chart."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0038"
down_revision: str | None = "0037"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("ticker_analysis", sa.Column("costs", sa.Numeric(), nullable=True))


def downgrade() -> None:
    op.drop_column("ticker_analysis", "costs")
