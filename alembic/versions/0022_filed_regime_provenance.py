"""persist filed accounting-regime provenance (#256)

Existing rows remain NULL because their source filing is not reconstructible from
the derived store alone. The next ``smaug analyze`` run records the detected
filed regime and whether sector fallback was used.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0022"
down_revision: str | None = "0021"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "ticker_analysis",
        sa.Column("filed_regime", sa.String(length=16), nullable=True),
    )
    op.add_column(
        "ticker_analysis",
        sa.Column("regime_source", sa.String(length=24), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("ticker_analysis", "regime_source")
    op.drop_column("ticker_analysis", "filed_regime")
