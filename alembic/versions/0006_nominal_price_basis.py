"""price the closed-year multiples on the nominal average, keep the adjusted one

Revision ID: 0006
Revises: 0005
Create Date: 2026-07-14

``price`` used to hold the year's dividend-adjusted average and ``price_nominal``
the nominal one. ADR 0018 swaps their roles: the multiples divide by what the
shares actually traded at, and the adjusted series is kept only as a total-return
reference. So the column is renamed and the two values are exchanged in place —
the stored rows are otherwise still correct, and re-running ``analyze`` would
overwrite them anyway.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "ticker_analysis", "price_nominal", new_column_name="price_adjusted"
    )
    # Postgres evaluates the whole SET list against the *old* row, so this is a true
    # swap, not two assignments in sequence.
    op.execute(
        sa.text(
            "UPDATE ticker_analysis "
            "SET price = price_adjusted, price_adjusted = price, "
            "    price_basis = 'nominal_year_avg' "
            "WHERE price_basis = 'adjusted_year_avg'"
        )
    )
    # The live view's quote is its own nominal price; there was never an adjusted
    # counterpart to keep, only a copy of the same number.
    op.execute(
        sa.text(
            "UPDATE ticker_analysis SET price_adjusted = NULL "
            "WHERE price_basis = 'ttm_current_nominal'"
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE ticker_analysis "
            "SET price = price_adjusted, price_adjusted = price, "
            "    price_basis = 'adjusted_year_avg' "
            "WHERE price_basis = 'nominal_year_avg'"
        )
    )
    op.execute(
        sa.text(
            "UPDATE ticker_analysis SET price_adjusted = price "
            "WHERE price_basis = 'ttm_current_nominal'"
        )
    )
    op.alter_column(
        "ticker_analysis", "price_adjusted", new_column_name="price_nominal"
    )
