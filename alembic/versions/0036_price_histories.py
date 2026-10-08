"""Persist append-only daily B3 history snapshots."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0036"
down_revision: str | None = "0035"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "price_histories",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("ticker", sa.String(12), nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("start_year", sa.Integer(), nullable=False),
        sa.Column("end_year", sa.Integer(), nullable=False),
        sa.Column("contract_version", sa.String(48), nullable=False),
        sa.Column("price_basis", sa.String(32), nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("points", sa.JSON(), nullable=False),
        sa.Column("gaps", sa.JSON(), nullable=False),
    )
    op.create_index("ix_price_histories_latest", "price_histories", ["ticker", "id"])


def downgrade() -> None:
    op.drop_index("ix_price_histories_latest", table_name="price_histories")
    op.drop_table("price_histories")
