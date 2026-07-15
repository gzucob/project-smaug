"""add the scale figures: market cap, enterprise value, share count

Revision ID: 0008
Revises: 0007
Create Date: 2026-07-15

The market-side inputs the calculator already builds its multiples from, now
persisted so the read API and the front-end can surface them at the top of a
ticker page (#25 / ANL-02). Absolute reais / a share count, not ratios.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

_COLUMNS = ("market_cap", "enterprise_value", "shares")


def upgrade() -> None:
    for column in _COLUMNS:
        op.add_column("ticker_analysis", sa.Column(column, sa.Numeric(), nullable=True))


def downgrade() -> None:
    for column in _COLUMNS:
        op.drop_column("ticker_analysis", column)
