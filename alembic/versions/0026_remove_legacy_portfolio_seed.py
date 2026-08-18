"""remove the legacy nine-ticker portfolio seed (#231)

Revision ID: 0026
Revises: 0025
Create Date: 2026-08-17

Migration 0014 created the portfolio table by inserting the nine tickers that
used to be hardcoded as the default portfolio. Membership must be user data, so
new installations need to finish with an empty table. Existing installations
must lose only those seed rows, not favorites added after 0014.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0026"
down_revision: str | None = "0025"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

_LEGACY_TICKERS = (
    "PETR4",
    "VALE3",
    "SAPR11",
    "TAEE11",
    "WEGE3",
    "BBAS3",
    "BBDC4",
    "BBSE3",
    "CXSE3",
)


def upgrade() -> None:
    portfolio = sa.table(
        "portfolio",
        sa.column("ticker", sa.String(length=12)),
        sa.column("added_at", sa.DateTime(timezone=True)),
    )
    # Revision 0014 gave every seed row one shared timestamp. A favorite added
    # later has a newer timestamp, so it is retained — including a legacy ticker
    # that was removed and explicitly favorited again. This timestamp is the only
    # provenance available because the original schema recorded no source.
    seed_timestamp = (
        sa.select(sa.func.min(portfolio.c.added_at))
        .where(portfolio.c.ticker.in_(_LEGACY_TICKERS))
        .scalar_subquery()
    )
    op.execute(
        sa.delete(portfolio).where(
            portfolio.c.ticker.in_(_LEGACY_TICKERS),
            portfolio.c.added_at == seed_timestamp,
        )
    )


def downgrade() -> None:
    """Do not recreate favorites that the user never explicitly chose."""

    # The upgrade intentionally discards provenance-free seed rows; reinserting
    # them on downgrade would recreate the legacy default portfolio.
    pass
