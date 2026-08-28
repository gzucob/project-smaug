"""Migration contract for the dedicated analysis-outcome table."""

from __future__ import annotations

import importlib.util
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Protocol, cast

import sqlalchemy as sa
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext


class _Upgrade(Protocol):
    def __call__(self) -> None: ...


_MIGRATIONS = Path(__file__).resolve().parents[1] / "alembic" / "versions"


def _load_operation(filename: str, name: str) -> _Upgrade:
    spec = importlib.util.spec_from_file_location(
        f"migration_{filename.replace('.', '_')}", _MIGRATIONS / filename
    )
    if spec is None or spec.loader is None:
        raise AssertionError(f"could not load migration {filename}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return cast(_Upgrade, getattr(module, name))


@contextmanager
def _database() -> Iterator[sa.Connection]:
    engine = sa.create_engine("sqlite:///:memory:")
    try:
        with engine.begin() as connection:
            yield connection
    finally:
        engine.dispose()


def _apply(connection: sa.Connection, filename: str, operation: str) -> None:
    context = MigrationContext.configure(connection)
    with Operations.context(context):
        _load_operation(filename, operation)()


def test_analysis_outcomes_migration_creates_a_separate_indexable_table() -> None:
    with _database() as connection:
        _apply(connection, "0030_analysis_outcomes.py", "upgrade")

        inspector = sa.inspect(connection)
        assert "analysis_outcomes" in inspector.get_table_names()
        columns = {
            column["name"]: column
            for column in inspector.get_columns("analysis_outcomes")
        }
        assert set(columns) == {
            "id",
            "run_id",
            "ticker",
            "status",
            "no_analysis_reason",
            "detail",
            "recorded_at",
        }
        assert columns["run_id"]["nullable"] is False
        assert columns["no_analysis_reason"]["nullable"] is True
        assert columns["detail"]["nullable"] is False
        assert columns["recorded_at"]["nullable"] is False
        index_names = {
            index["name"] for index in inspector.get_indexes("analysis_outcomes")
        }
        assert "ix_analysis_outcomes_ticker_recorded_at" in index_names
