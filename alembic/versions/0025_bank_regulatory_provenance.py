"""persist the bank regulatory-input source contract (#261)"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0025"
down_revision: str | None = "0024"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "ticker_analysis",
        sa.Column("bank_regulatory_provenance", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("ticker_analysis", "bank_regulatory_provenance")
