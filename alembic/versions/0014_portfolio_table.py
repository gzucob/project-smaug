"""add the portfolio table, seeded with the current nine (#151)

Revision ID: 0014
Revises: 0013
Create Date: 2026-08-04

The portfolio stops being a hardcoded dict (``portfolio/domain/sectors.py``)
and becomes a row per favorited ticker — no history, ``ticker`` itself is the
primary key. Seeded here with the nine tickers that were the hardcoded default,
so the existing single user's portfolio does not silently empty out on deploy;
the CLI's default ticker set now reads this table instead of the dict.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

# The dict's previous keys (``portfolio/domain/sectors.py``, PORTFOLIO), in
# their original stable order.
_SEEDED_TICKERS = (
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
    op.create_table(
        "portfolio",
        sa.Column("ticker", sa.String(length=12), primary_key=True),
        sa.Column("added_at", sa.DateTime(timezone=True), nullable=False),
    )
    table = sa.table(
        "portfolio",
        sa.column("ticker", sa.String),
        # Must match the created column's type exactly: a plain ``sa.DateTime``
        # here (no ``timezone=True``) has SQLAlchemy bind an offset-aware Python
        # datetime against a ``TIMESTAMP WITHOUT TIME ZONE`` parameter, which
        # asyncpg refuses outright rather than silently dropping the offset.
        sa.column("added_at", sa.DateTime(timezone=True)),
    )
    # One instant for all nine — they were the default together, not favorited
    # one after another, so a shared timestamp is the honest one.
    seeded_at = datetime.now(UTC)
    op.bulk_insert(
        table,
        [{"ticker": ticker, "added_at": seeded_at} for ticker in _SEEDED_TICKERS],
    )


def downgrade() -> None:
    op.drop_table("portfolio")
