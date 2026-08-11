"""separate per-security and company valuation ratios (#234)

Revision ID: 0016
Revises: 0015
Create Date: 2026-08-11

The derived store is recomputed by ``smaug analyze``. Existing company-level
values are preserved under explicit scope/timing names; new per-security fields
remain NULL until that recomputation reads B3 cash events and CPC 41 results.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

_RENAMES = (
    ("pe", "company_pe"),
    ("pb", "company_pb"),
    ("payout", "payout_cash_paid_in_period"),
    ("payout_declared", "payout_declared_in_period"),
    ("dividend_yield", "company_cash_yield_paid_in_period"),
    ("dividend_yield_declared", "company_yield_declared_in_period"),
    ("dividends", "company_distributions_paid_in_period"),
    ("dividends_declared", "company_distributions_declared_in_period"),
)

_NEW_COLUMNS = (
    "pe_basic",
    "pe_diluted",
    "pb",
    "dividend_yield",
    "distributions_per_security",
)


def _rename_null_reasons(pairs: tuple[tuple[str, str], ...]) -> None:
    """Keep the JSON cause map aligned with columns renamed in the same revision."""
    removed = " ".join(f"- '{old}'" for old, _new in pairs)
    entries = ", ".join(
        f"'{new}', null_reasons::jsonb -> '{old}'" for old, new in pairs
    )
    op.execute(
        sa.text(
            f"""
            UPDATE ticker_analysis
            SET null_reasons = (
                (null_reasons::jsonb {removed})
                || jsonb_strip_nulls(jsonb_build_object({entries}))
            )::json
            WHERE null_reasons IS NOT NULL
            """
        )
    )


def _drop_new_null_reasons() -> None:
    removed = " ".join(f"- '{column}'" for column in _NEW_COLUMNS)
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
    for old, new in _RENAMES:
        op.alter_column("ticker_analysis", old, new_column_name=new)
    for column in _NEW_COLUMNS:
        op.add_column("ticker_analysis", sa.Column(column, sa.Numeric(), nullable=True))
    _rename_null_reasons(_RENAMES)


def downgrade() -> None:
    _drop_new_null_reasons()
    _rename_null_reasons(tuple((new, old) for old, new in _RENAMES))
    for column in reversed(_NEW_COLUMNS):
        op.drop_column("ticker_analysis", column)
    for old, new in reversed(_RENAMES):
        op.alter_column("ticker_analysis", new, new_column_name=old)
