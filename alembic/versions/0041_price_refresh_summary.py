"""Persist partial price refresh counts and named ticker issues."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0041"
down_revision: str | None = "0040"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("price_refresh_status", sa.Column("updated_count", sa.Integer()))
    op.add_column("price_refresh_status", sa.Column("unchanged_count", sa.Integer()))
    op.add_column("price_refresh_status", sa.Column("issues", sa.JSON()))


def downgrade() -> None:
    op.drop_column("price_refresh_status", "issues")
    op.drop_column("price_refresh_status", "unchanged_count")
    op.drop_column("price_refresh_status", "updated_count")
