"""add the total-basis indicator columns (ADR 0026)

Revision ID: 0010
Revises: 0009
Create Date: 2026-07-16

The whole-firm ratios published on the consolidated total — minority interest
included — alongside their controllers'-slice siblings (#116 / ANL-38). The
bare columns keep the controllers' basis; ``_total`` pairs the consolidated
result with the consolidated denominator, which is the basis the reference
platforms publish for margins and ROE.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

_COLUMNS = ("roe_total", "roa_total", "net_margin_total", "net_income_total")


def upgrade() -> None:
    for column in _COLUMNS:
        op.add_column("ticker_analysis", sa.Column(column, sa.Numeric(), nullable=True))


def downgrade() -> None:
    for column in _COLUMNS:
        op.drop_column("ticker_analysis", column)
