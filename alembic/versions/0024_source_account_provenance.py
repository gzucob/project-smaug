"""persist source-account provenance for indicator roots (#260)

The JSON payload is nullable so rows computed before this evidence existed remain
identifiable as legacy until the analysis command recomputes them.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0024"
down_revision: str | None = "0023"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "ticker_analysis",
        sa.Column("source_account_evidence", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("ticker_analysis", "source_account_evidence")
