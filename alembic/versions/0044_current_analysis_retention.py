"""Retain current analyses and track committed UI data changes."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0044"
down_revision: str | None = "0043"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    # Keep the same result the API selected before this migration. TTM has one
    # current window per ticker; every closed exercise keeps its own latest row.
    op.execute("""
        DELETE FROM ticker_analysis
        WHERE id IN (
            SELECT id FROM (
                SELECT id, row_number() OVER (
                    PARTITION BY ticker, view,
                        CASE WHEN view = 'closed_year' THEN reference_date END
                    ORDER BY computed_at DESC, id DESC
                ) AS position
                FROM ticker_analysis
            ) AS ranked WHERE position > 1
        )
    """)
    op.create_index(
        "uq_ticker_analysis_current_ttm",
        "ticker_analysis",
        ["ticker"],
        unique=True,
        postgresql_where=sa.text("view = 'ttm_live'"),
    )
    op.create_index(
        "uq_ticker_analysis_current_year",
        "ticker_analysis",
        ["ticker", "reference_date"],
        unique=True,
        postgresql_where=sa.text("view = 'closed_year'"),
    )
    op.create_table(
        "ui_data_revision",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("generation", sa.BigInteger(), nullable=False),
        sa.CheckConstraint("id = 1", name="ck_ui_data_revision_singleton"),
    )
    op.execute("INSERT INTO ui_data_revision (id, generation) VALUES (1, 1)")
    op.execute("""
        CREATE FUNCTION bump_ui_data_revision() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            UPDATE ui_data_revision SET generation = generation + 1 WHERE id = 1;
            RETURN NULL;
        END;
        $$
    """)
    for table in ("ticker_analysis", "price_histories", "portfolio"):
        op.execute(f"""
            CREATE TRIGGER {table}_ui_revision
            AFTER INSERT OR UPDATE OR DELETE ON {table}
            FOR EACH ROW EXECUTE FUNCTION bump_ui_data_revision()
        """)


def downgrade() -> None:
    # Deleted recalculations cannot be reconstructed. Downgrade restores the
    # former schema and allows future runs to append versions again.
    for table in ("ticker_analysis", "price_histories", "portfolio"):
        op.execute(f"DROP TRIGGER {table}_ui_revision ON {table}")
    op.execute("DROP FUNCTION bump_ui_data_revision()")
    op.drop_table("ui_data_revision")
    op.drop_index("uq_ticker_analysis_current_year", table_name="ticker_analysis")
    op.drop_index("uq_ticker_analysis_current_ttm", table_name="ticker_analysis")
