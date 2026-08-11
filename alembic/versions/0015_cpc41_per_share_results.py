"""add explicit CPC 41 basic and diluted per-security results (#233)

Revision ID: 0015
Revises: 0014
Create Date: 2026-08-11

The legacy ``eps`` column remains as the basic-result compatibility alias.
Existing rows keep NULL in the explicit columns until ``smaug analyze``
recomputes them from the issuer's consolidated 3.99 disclosure.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

_COLUMNS = ("eps_basic", "eps_diluted")


def upgrade() -> None:
    for column in _COLUMNS:
        op.add_column("ticker_analysis", sa.Column(column, sa.Numeric(), nullable=True))


def downgrade() -> None:
    for column in reversed(_COLUMNS):
        op.drop_column("ticker_analysis", column)
