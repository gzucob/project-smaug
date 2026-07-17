"""add the net-debt leverage indicator columns (#26 / #103)

Revision ID: 0011
Revises: 0010
Create Date: 2026-07-16

Four ratios both reference platforms show: EV/EBIT, net debt/EBIT, net
debt/equity, and equity/assets (the complement of liabilities_to_assets, kept
so the UI never does arithmetic). The net-debt family also starts computing
for insurance-regime filers — their net debt is the cash, negated (#103) —
which is a calculator change, not a schema one.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

_COLUMNS = ("ev_ebit", "net_debt_to_ebit", "net_debt_to_equity", "equity_to_assets")


def upgrade() -> None:
    for column in _COLUMNS:
        op.add_column("ticker_analysis", sa.Column(column, sa.Numeric(), nullable=True))


def downgrade() -> None:
    for column in _COLUMNS:
        op.drop_column("ticker_analysis", column)
