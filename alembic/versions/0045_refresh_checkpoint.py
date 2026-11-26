"""Remember the source revisions published by the incremental worker."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0045"
down_revision: str | None = "0044"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "refresh_checkpoint",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("raw_id", sa.String(24), nullable=True),
        sa.Column("archives", sa.JSON(), nullable=False),
        sa.CheckConstraint("id = 1", name="ck_refresh_checkpoint_singleton"),
    )
    op.execute("""
        CREATE OR REPLACE FUNCTION bump_ui_data_revision() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF current_setting('smaug.incremental_refresh', true) IS DISTINCT FROM 'on'
            THEN
                UPDATE ui_data_revision SET generation = generation + 1 WHERE id = 1;
            END IF;
            RETURN NULL;
        END;
        $$
    """)


def downgrade() -> None:
    op.execute("""
        CREATE OR REPLACE FUNCTION bump_ui_data_revision() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            UPDATE ui_data_revision SET generation = generation + 1 WHERE id = 1;
            RETURN NULL;
        END;
        $$
    """)
    op.drop_table("refresh_checkpoint")
