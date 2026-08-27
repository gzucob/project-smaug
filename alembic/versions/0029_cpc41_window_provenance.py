"""persist CPC 41 selected-window provenance (#275)

Revision ID: 0029
Revises: 0028
Create Date: 2026-08-27

The strict CPC 41 denominator is reconstructed only from filed per-security
results. Store the selected annual/YTD/TTM periods, basic/diluted statuses,
blockers, and raw account references as JSON so an EPS null can be audited
without substituting a closing share count.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0029"
down_revision: str | None = "0028"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "ticker_analysis",
        sa.Column("cpc41_window_provenance", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("ticker_analysis", "cpc41_window_provenance")
