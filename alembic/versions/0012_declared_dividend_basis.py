"""add the declared-dividend basis columns (#104)

Revision ID: 0012
Revises: 0011
Create Date: 2026-07-17

Payout and dividend yield on what the parent DECLARED against equity in the
period (the DMPL charge), alongside the existing cash-paid basis (the DFC
outflow). The two answer different questions — the cash paid in a year was
often declared on the prior year's profit — and the declared one is the basis
companies and the reference platforms report payout on. Dual columns, the same
pattern ADR 0026 set for the statement slices.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

_COLUMNS = ("payout_declared", "dividend_yield_declared", "dividends_declared")


def upgrade() -> None:
    for column in _COLUMNS:
        op.add_column("ticker_analysis", sa.Column(column, sa.Numeric(), nullable=True))


def downgrade() -> None:
    for column in _COLUMNS:
        op.drop_column("ticker_analysis", column)
