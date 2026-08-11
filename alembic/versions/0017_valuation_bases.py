"""align point-in-time valuation, liquidity and ROIC bases (#236)

Revision ID: 0017
Revises: 0016
Create Date: 2026-08-11

The derived store will be recomputed after the remaining formula corrections.
New provenance and headline columns therefore remain NULL on existing rows; the
ROIC rename preserves the old statutory-proxy value and its null cause until then.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

_PROVENANCE_COLUMNS = (
    ("share_count_basis", sa.String(length=48)),
    ("liquidity_basis", sa.String(length=48)),
    ("roic_tax_basis", sa.String(length=32)),
)
_HEADLINE_COLUMNS = (
    "cash_equivalents",
    "current_financial_investments",
    "non_controlling_interests",
)


def _rename_null_reason(old: str, new: str) -> None:
    op.execute(
        sa.text(
            f"""
            UPDATE ticker_analysis
            SET null_reasons = (
                (null_reasons::jsonb - '{old}')
                || jsonb_strip_nulls(
                    jsonb_build_object('{new}', null_reasons::jsonb -> '{old}')
                )
            )::json
            WHERE null_reasons IS NOT NULL
            """
        )
    )


def _drop_new_null_reasons() -> None:
    removed = " ".join(f"- '{column}'" for column in _HEADLINE_COLUMNS)
    op.execute(
        sa.text(
            f"""
            UPDATE ticker_analysis
            SET null_reasons = (null_reasons::jsonb {removed})::json
            WHERE null_reasons IS NOT NULL
            """
        )
    )


def upgrade() -> None:
    op.alter_column("ticker_analysis", "roic", new_column_name="roic_statutory")
    for name, column_type in _PROVENANCE_COLUMNS:
        op.add_column("ticker_analysis", sa.Column(name, column_type, nullable=True))
    for name in _HEADLINE_COLUMNS:
        op.add_column("ticker_analysis", sa.Column(name, sa.Numeric(), nullable=True))
    _rename_null_reason("roic", "roic_statutory")


def downgrade() -> None:
    _drop_new_null_reasons()
    _rename_null_reason("roic_statutory", "roic")
    for name in reversed(_HEADLINE_COLUMNS):
        op.drop_column("ticker_analysis", name)
    for name, _column_type in reversed(_PROVENANCE_COLUMNS):
        op.drop_column("ticker_analysis", name)
    op.alter_column("ticker_analysis", "roic_statutory", new_column_name="roic")
