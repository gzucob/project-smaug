"""Persist additional cash-flow, liquidity and filed free-float indicators."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0033"
down_revision: str | None = "0032"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

_COLUMNS = (
    "price_to_cfo",
    "ev_cfo",
    "ev_fcf",
    "cash_ratio",
    "quick_ratio",
    "ev_revenue",
    "free_float",
    "price_to_ebitda",
    "cfo_yield",
    "cfo_margin",
    "fcf_margin",
    "cash_conversion",
    "capex_to_cfo",
)


def upgrade() -> None:
    for name in _COLUMNS:
        op.add_column("ticker_analysis", sa.Column(name, sa.Numeric(), nullable=True))


def downgrade() -> None:
    for name in reversed(_COLUMNS):
        op.drop_column("ticker_analysis", name)
