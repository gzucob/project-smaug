"""persist raw-BPP debt coverage evidence (#258)

The derived numeric result remains unchanged. These nullable columns make the
source decision auditable and let old rows be identified as legacy until they
are recomputed by ``smaug analyze``.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0023"
down_revision: str | None = "0022"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "ticker_analysis",
        sa.Column("issuer_name", sa.String(length=256), nullable=True),
    )
    op.add_column(
        "ticker_analysis",
        sa.Column("issuer_cd_cvm", sa.String(length=16), nullable=True),
    )
    op.add_column(
        "ticker_analysis",
        sa.Column("issuer_cnpj", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "ticker_analysis",
        sa.Column("debt_evidence_snapshot", sa.String(length=16), nullable=True),
    )
    op.add_column(
        "ticker_analysis",
        sa.Column("debt_evidence", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("ticker_analysis", "debt_evidence")
    op.drop_column("ticker_analysis", "debt_evidence_snapshot")
    op.drop_column("ticker_analysis", "issuer_cnpj")
    op.drop_column("ticker_analysis", "issuer_cd_cvm")
    op.drop_column("ticker_analysis", "issuer_name")
