"""persist share-class identity and capital evidence (#259)

Revision ID: 0027
Revises: 0026
Create Date: 2026-08-25

The analysis row already stores indicator provenance as JSON. These nullable
columns extend that boundary with the FCA class mapping, the class-by-class
market-cap ledger, and the CVM issued/treasury/restatement evidence. Existing
rows remain valid and are enriched by the next ``smaug analyze`` run.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0027"
down_revision: str | None = "0026"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "ticker_analysis",
        sa.Column("share_class_mappings", sa.JSON(), nullable=True),
    )
    op.add_column(
        "ticker_analysis",
        sa.Column("class_market_values", sa.JSON(), nullable=True),
    )
    op.add_column(
        "ticker_analysis",
        sa.Column("capital_provenance", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("ticker_analysis", "capital_provenance")
    op.drop_column("ticker_analysis", "class_market_values")
    op.drop_column("ticker_analysis", "share_class_mappings")
