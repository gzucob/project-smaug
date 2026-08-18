"""Portfolio seed migration regressions for issue #231."""

from __future__ import annotations

import importlib.util
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol, cast

import sqlalchemy as sa
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext


class _Upgrade(Protocol):
    def __call__(self) -> None: ...


_MIGRATIONS = Path(__file__).resolve().parents[1] / "alembic" / "versions"


def _load_upgrade(filename: str) -> _Upgrade:
    spec = importlib.util.spec_from_file_location(
        f"migration_{filename.replace('.', '_')}", _MIGRATIONS / filename
    )
    if spec is None or spec.loader is None:
        raise AssertionError(f"could not load migration {filename}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return cast(_Upgrade, module.upgrade)


@contextmanager
def _database() -> Iterator[sa.Connection]:
    engine = sa.create_engine("sqlite:///:memory:")
    try:
        with engine.begin() as connection:
            yield connection
    finally:
        engine.dispose()


def _apply(connection: sa.Connection, *filenames: str) -> None:
    context = MigrationContext.configure(connection)
    with Operations.context(context):
        for filename in filenames:
            _load_upgrade(filename)()


def _tickers(connection: sa.Connection) -> list[str]:
    result = connection.execute(sa.text("SELECT ticker FROM portfolio ORDER BY ticker"))
    return list(result.scalars())


def test_fresh_upgrade_does_not_leave_the_legacy_seed() -> None:
    with _database() as connection:
        _apply(
            connection,
            "0014_portfolio_table.py",
            "0026_remove_legacy_portfolio_seed.py",
        )

        assert _tickers(connection) == []


def test_existing_database_with_only_the_seed_is_cleaned() -> None:
    with _database() as connection:
        _apply(connection, "0014_portfolio_table.py")
        assert len(_tickers(connection)) == 9

        _apply(connection, "0026_remove_legacy_portfolio_seed.py")

        assert _tickers(connection) == []


def test_favorites_added_after_the_seed_are_preserved() -> None:
    with _database() as connection:
        _apply(connection, "0014_portfolio_table.py")
        favorite_time = datetime.now(UTC) + timedelta(days=1)
        connection.execute(sa.text("DELETE FROM portfolio WHERE ticker = 'PETR4'"))
        connection.execute(
            sa.text(
                "INSERT INTO portfolio (ticker, added_at) VALUES (:ticker, :added_at)"
            ),
            {
                "ticker": "PETR4",
                "added_at": favorite_time,
            },
        )
        connection.execute(
            sa.text(
                "INSERT INTO portfolio (ticker, added_at) VALUES (:ticker, :added_at)"
            ),
            {
                "ticker": "LREN3",
                "added_at": favorite_time + timedelta(microseconds=1),
            },
        )

        _apply(connection, "0026_remove_legacy_portfolio_seed.py")

        assert _tickers(connection) == ["LREN3", "PETR4"]
