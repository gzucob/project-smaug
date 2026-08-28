"""persist one ticker outcome for every analysis run (#294)

Analysis can legitimately produce no ``ticker_analysis`` row for a ticker. A
separate append-only table preserves that execution result, including the
named reason, without manufacturing an indicator row. ``doctor`` reads the
latest row per ticker; a later analyzed or error outcome therefore supersedes
an older skipped reason.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0030"
down_revision: str | None = "0029"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "analysis_outcomes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("ticker", sa.String(length=12), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("no_analysis_reason", sa.String(length=64), nullable=True),
        sa.Column("detail", sa.Text(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('analyzed', 'skipped', 'error')",
            name="ck_analysis_outcomes_status",
        ),
    )
    op.create_index(
        op.f("ix_analysis_outcomes_run_id"),
        "analysis_outcomes",
        ["run_id"],
    )
    op.create_index(
        op.f("ix_analysis_outcomes_ticker"),
        "analysis_outcomes",
        ["ticker"],
    )
    op.create_index(
        "ix_analysis_outcomes_ticker_recorded_at",
        "analysis_outcomes",
        ["ticker", "recorded_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_analysis_outcomes_ticker_recorded_at", table_name="analysis_outcomes"
    )
    op.drop_index(op.f("ix_analysis_outcomes_ticker"), table_name="analysis_outcomes")
    op.drop_index(op.f("ix_analysis_outcomes_run_id"), table_name="analysis_outcomes")
    op.drop_table("analysis_outcomes")
