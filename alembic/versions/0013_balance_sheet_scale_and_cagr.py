"""add the balance-sheet scale figures and the compounded growth rates (#142, #144)

Revision ID: 0013
Revises: 0012
Create Date: 2026-07-30

Two additions that share a migration because both are plain nullable columns on
``ticker_analysis``.

The balance-sheet figures (#142) are the absolute reais the existing ratios
divide away: ``liabilities_to_assets`` cannot be turned back into what the
company owns against what it owes, so a chart of the two sides needs the sides
themselves. ``total_liabilities`` is assets less the *consolidated* equity —
minority interest is equity, not third-party capital (see #149, which asks the
same question of ``liabilities_to_assets``).

The compounded rates (#144) answer what the year-on-year growth figures cannot:
a profit that fell 40% and then grew 60% reads as a 60% grower. The window is in
the column name because the reference platforms publish different windows under
the same "CAGR 5A" heading — these span five years of variation, between
exercises six apart.

Existing rows keep NULL in every new column until ``smaug analyze`` recomputes
them; the read API already treats a null indicator as meaningful.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

_COLUMNS = (
    # Balance-sheet scale (#142)
    "total_assets",
    "total_liabilities",
    "equity",
    "equity_total",
    # Compounded annual growth, five years of variation (#144)
    "revenue_cagr_5y",
    "ebitda_cagr_5y",
    "ebit_cagr_5y",
    "net_income_cagr_5y",
)


def upgrade() -> None:
    for column in _COLUMNS:
        op.add_column("ticker_analysis", sa.Column(column, sa.Numeric(), nullable=True))


def downgrade() -> None:
    for column in _COLUMNS:
        op.drop_column("ticker_analysis", column)
