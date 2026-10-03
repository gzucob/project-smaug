"""Calculation-version migration preserves stored financial history."""

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


def test_version_migration_preserves_values_and_downgrades_explicitly() -> None:
    with _database() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE ticker_analysis (id INTEGER PRIMARY KEY, "
            "eps_basic NUMERIC, eps_basic_market NUMERIC, null_reasons TEXT)"
        )
        connection.exec_driver_sql(
            "INSERT INTO ticker_analysis VALUES (1, NULL, 2, "
            '\'{"eps_basic":"missing_weighted_average_shares"}\')'
        )
        before = connection.exec_driver_sql("SELECT * FROM ticker_analysis").one()
        _apply(connection, "0031_calculation_contract_version.py", "upgrade")
        after = connection.exec_driver_sql("SELECT * FROM ticker_analysis").one()
        assert tuple(after[:-1]) == tuple(before)
        assert after[-1] == "legacy_unversioned"
        connection.exec_driver_sql(
            "INSERT INTO ticker_analysis (id, eps_basic, calculation_contract_version) "
            "VALUES (2, 3, 'equivalent_evidence_v1')"
        )
        assert (
            connection.exec_driver_sql(
                "SELECT COUNT(*) FROM ticker_analysis"
            ).scalar_one()
            == 2
        )
        _apply(connection, "0031_calculation_contract_version.py", "downgrade")
        assert (
            connection.exec_driver_sql(
                "SELECT * FROM ticker_analysis WHERE id = 1"
            ).one()
            == before
        )
        assert "calculation_contract_version" not in {
            column["name"]
            for column in sa.inspect(connection).get_columns("ticker_analysis")
        }
