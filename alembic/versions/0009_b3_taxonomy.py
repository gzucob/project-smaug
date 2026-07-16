"""replace the five-value sector with the B3 taxonomy (setor/subsetor/segmento)

Revision ID: 0009
Revises: 0008
Create Date: 2026-07-15

The stored classification moves from the coarse five-value ``sector`` enum to
B3's three-level economic taxonomy (ADR 0024). ``setor`` is always present (the
B3 snapshot, or the CVM single-level fallback); ``subsetor``/``segmento`` are
NULL under that fallback. Existing rows are backfilled by copying the old
``sector`` into ``setor`` as a placeholder — a re-run of ``analyze`` replaces
them with the real taxonomy.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("ticker_analysis", sa.Column("setor", sa.String(64), nullable=True))
    op.add_column(
        "ticker_analysis", sa.Column("subsetor", sa.String(64), nullable=True)
    )
    op.add_column(
        "ticker_analysis", sa.Column("segmento", sa.String(64), nullable=True)
    )
    # Backfill: the old five-value label lands in ``setor`` until a re-analyze
    # overwrites it with the real B3 setor.
    op.execute("UPDATE ticker_analysis SET setor = sector WHERE setor IS NULL")
    op.alter_column("ticker_analysis", "setor", nullable=False)
    op.create_index("ix_ticker_analysis_setor", "ticker_analysis", ["setor"])
    op.drop_column("ticker_analysis", "sector")


def downgrade() -> None:
    op.add_column("ticker_analysis", sa.Column("sector", sa.String(20), nullable=True))
    op.execute("UPDATE ticker_analysis SET sector = setor WHERE sector IS NULL")
    op.alter_column("ticker_analysis", "sector", nullable=False)
    op.drop_index("ix_ticker_analysis_setor", "ticker_analysis")
    op.drop_column("ticker_analysis", "segmento")
    op.drop_column("ticker_analysis", "subsetor")
    op.drop_column("ticker_analysis", "setor")
