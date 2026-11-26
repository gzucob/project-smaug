"""Remember issuer groups that could not publish during an incremental update."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0046"
down_revision: str | None = "0045"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "refresh_checkpoint",
        sa.Column("retry_tickers", sa.JSON(), server_default="[]", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("refresh_checkpoint", "retry_tickers")
