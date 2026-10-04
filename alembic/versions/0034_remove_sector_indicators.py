"""Remove retired sector indicators and their dedicated bank provenance."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0034"
down_revision: str | None = "0033"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

_COLUMNS = (
    "net_interest_margin",
    "efficiency_ratio",
    "cost_of_risk",
    "loss_ratio",
    "combined_ratio",
)


def upgrade() -> None:
    for name in _COLUMNS:
        op.drop_column("ticker_analysis", name)
    op.drop_column("ticker_analysis", "bank_regulatory_provenance")
    # Historical null/evidence JSON remains append-only. Readers filter retired
    # keys and roots; unrelated results and historical contract versions survive.


def downgrade() -> None:
    # Restores schema only: previously dropped values cannot be reconstructed.
    op.add_column(
        "ticker_analysis",
        sa.Column("bank_regulatory_provenance", sa.JSON(), nullable=True),
    )
    for name in reversed(_COLUMNS):
        op.add_column("ticker_analysis", sa.Column(name, sa.Numeric(), nullable=True))
