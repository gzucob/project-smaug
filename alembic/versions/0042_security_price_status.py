"""Retain dated trading observations independently from refresh failures."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0042"
down_revision: str | None = "0041"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("price_refresh_status", sa.Column("securities", sa.JSON()))


def downgrade() -> None:
    op.drop_column("price_refresh_status", "securities")
