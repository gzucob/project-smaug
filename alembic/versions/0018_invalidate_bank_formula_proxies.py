"""invalidate superseded bank formula proxies (#237)

Revision ID: 0018
Revises: 0017
Create Date: 2026-08-11

The derived rows are reproducible, but they remain readable until ``smaug
analyze`` creates a newer snapshot. Null the bank cells whose old values used
PBT-as-EBIT, generic CFO-minus-capex FCF or closing/partial ratio bases so an
upgrade cannot continue serving a known-wrong value. Reanalysis preserves the
same named-null contract until an explicit regulatory provider is available.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

_INAPPLICABLE = (
    "ebit_margin",
    "ebit_cagr_5y",
    "price_to_ebit",
    "fcf",
    "price_to_fcf",
    "fcf_yield",
)
_MISSING_REGULATORY = (
    "net_interest_margin",
    "efficiency_ratio",
    "cost_of_risk",
)
_ALL_INVALIDATED = _INAPPLICABLE + _MISSING_REGULATORY


def upgrade() -> None:
    assignments = ",\n                ".join(
        f"{column} = NULL" for column in _ALL_INVALIDATED
    )
    reasons = ", ".join(
        [
            *(f"'{column}', 'inapplicable_regime'" for column in _INAPPLICABLE),
            *(
                f"'{column}', 'missing_regulatory_disclosure'"
                for column in _MISSING_REGULATORY
            ),
        ]
    )
    op.execute(
        sa.text(
            f"""
            UPDATE ticker_analysis
            SET {assignments},
                null_reasons = (
                    COALESCE(null_reasons::jsonb, '{{}}'::jsonb)
                    || jsonb_build_object({reasons})
                )::json
            WHERE segmento = 'Bancos'
               OR setor IN ('bank', 'Bancos')
            """
        )
    )


def downgrade() -> None:
    """The superseded numeric values cannot be reconstructed faithfully.

    PostgreSQL is a reproducible derived store, and migration 0018 deliberately
    destroys known-wrong cells. Downgrade leaves those cells null; rerunning the
    older analyzer is the only way to recreate its former (incorrect) outputs.
    """
