"""Persist security tag along and dated B3 listing classification."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0035"
down_revision: str | None = "0034"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "ticker_analysis", sa.Column("tag_along", sa.Numeric(), nullable=True)
    )
    op.add_column("ticker_analysis", sa.Column("governance", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("ticker_analysis", "governance")
    op.drop_column("ticker_analysis", "tag_along")
