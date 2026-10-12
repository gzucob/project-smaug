"""Replace refresh classifications with the retained as-traded quote."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0043"
down_revision: str | None = "0042"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("ticker_analysis", sa.Column("price_as_traded", sa.Numeric()))
    # Retain already recorded tape observations before removing classifications.
    # The actual code and session must match; no adjusted price is substituted.
    op.execute("""
        UPDATE ticker_analysis AS analysis
        SET price_as_traded = (observation->>'last_price')::numeric
        FROM price_refresh_status AS status,
             jsonb_array_elements(COALESCE(status.securities::jsonb, '[]'::jsonb))
             AS observation
        WHERE analysis.ticker = observation->>'ticker'
          AND analysis.price_source_code = observation->>'source_code'
          AND analysis.price_source_session = (observation->>'last_session')::date
          AND observation->>'price_basis' = 'as_traded'
          AND observation->>'last_price' IS NOT NULL
    """)
    op.drop_column("price_refresh_status", "securities")


def downgrade() -> None:
    # Removed classifications are not reconstructed from market observations.
    op.add_column("price_refresh_status", sa.Column("securities", sa.JSON()))
    op.drop_column("ticker_analysis", "price_as_traded")
