"""persist B3 price source code and session (#270)

The price succession resolver can recover a requested ticker from a proven
successor code.  Keep the exact B3 code and session that supplied the persisted
price so the read API and ``smaug doctor`` do not hide that substitution.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0028"
down_revision: str | None = "0027"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "ticker_analysis",
        sa.Column("price_source_code", sa.String(length=12), nullable=True),
    )
    op.add_column(
        "ticker_analysis",
        sa.Column("price_source_session", sa.Date(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("ticker_analysis", "price_source_session")
    op.drop_column("ticker_analysis", "price_source_code")
