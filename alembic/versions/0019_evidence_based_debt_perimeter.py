"""require an evidenced debt perimeter for leverage and EV (#235)

Revision ID: 0019
Revises: 0018
Create Date: 2026-08-11

The old rows do not record whether all interest-bearing liabilities were read.
Null every value that depends on that unproved perimeter before adding the new
provenance marker. ``smaug analyze`` rebuilds them from the reproducible CVM
mirror; an absent or ambiguous perimeter remains a named null.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

_INVALIDATED = (
    "net_debt",
    "net_debt_to_ebitda",
    "net_debt_to_ebit",
    "net_debt_to_equity",
    "debt_to_equity",
    "enterprise_value",
    "ev_ebitda",
    "ev_ebit",
    "roic_statutory",
)


def upgrade() -> None:
    op.add_column(
        "ticker_analysis", sa.Column("debt_basis", sa.String(length=48), nullable=True)
    )
    assignments = ",\n                ".join(
        f"{column} = NULL" for column in _INVALIDATED
    )
    reasons = ", ".join(
        f"'{column}', 'incomplete_debt_coverage'" for column in _INVALIDATED
    )
    affected = " OR ".join(f"{column} IS NOT NULL" for column in _INVALIDATED)
    op.execute(
        sa.text(
            f"""
            UPDATE ticker_analysis
            SET {assignments},
                null_reasons = (
                    COALESCE(null_reasons::jsonb, '{{}}'::jsonb)
                    || jsonb_build_object({reasons})
                )::json
            WHERE {affected}
            """
        )
    )


def downgrade() -> None:
    """The superseded derived values are intentionally not reconstructed."""
    op.drop_column("ticker_analysis", "debt_basis")
