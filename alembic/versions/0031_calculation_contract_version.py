"""Version persisted calculation contracts without rewriting financial history."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0031"
down_revision: str | None = "0030"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "ticker_analysis",
        sa.Column(
            "calculation_contract_version",
            sa.String(48),
            nullable=False,
            server_default="legacy_unversioned",
        ),
    )


def downgrade() -> None:
    op.drop_column("ticker_analysis", "calculation_contract_version")
