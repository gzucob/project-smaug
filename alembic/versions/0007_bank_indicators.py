"""add the bank-only ratios: interest margin, efficiency, cost of risk

Revision ID: 0007
Revises: 0006
Create Date: 2026-07-14

Null for every non-bank filer, and named inapplicable there rather than missing
(ADR 0021).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

_COLUMNS = ("net_interest_margin", "efficiency_ratio", "cost_of_risk")


def upgrade() -> None:
    for column in _COLUMNS:
        op.add_column("ticker_analysis", sa.Column(column, sa.Numeric(), nullable=True))


def downgrade() -> None:
    for column in _COLUMNS:
        op.drop_column("ticker_analysis", column)
