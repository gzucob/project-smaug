"""Exercise the published reversible PostgreSQL migration chain."""

import os
from uuid import uuid4

import pytest
from alembic.config import Config
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Connection, inspect, text
from sqlalchemy.ext.asyncio import create_async_engine


@pytest.mark.skipif(
    not os.environ.get("SMAUG_TEST_POSTGRES_URI"),
    reason="Set SMAUG_TEST_POSTGRES_URI to test migrations in an isolated schema",
)
async def test_published_migrations_rebuild_and_remove_their_postgres_schema() -> None:
    schema = f"migration_history_{uuid4().hex}"
    # Future migrations can explicitly be irreversible. This test exercises the
    # published history whose downgrades still declare a schema change.
    revisions = list(
        ScriptDirectory.from_config(Config("alembic.ini")).iterate_revisions(
            "0046", "base"
        )
    )
    engine = create_async_engine(os.environ["SMAUG_TEST_POSTGRES_URI"])

    def migrate(connection: Connection) -> None:
        assert inspect(connection).get_table_names(schema=schema) == []
        with Operations.context(MigrationContext.configure(connection)):
            for revision in reversed(revisions):
                revision.module.upgrade()
            tables = inspect(connection).get_table_names(schema=schema)
            assert {"ticker_analysis", "price_histories", "portfolio"} <= set(tables)
            assert (
                connection.scalar(text("SELECT generation FROM ui_data_revision")) == 1
            )
            assert connection.scalar(text("SELECT count(*) FROM portfolio")) == 0
            for revision in revisions:
                revision.module.downgrade()
        assert inspect(connection).get_table_names(schema=schema) == []
        assert (
            connection.scalar(
                text(
                    "SELECT count(*) FROM pg_proc WHERE pronamespace = "
                    "(SELECT oid FROM pg_namespace WHERE nspname = :schema)"
                ),
                {"schema": schema},
            )
            == 0
        )

    try:
        async with engine.begin() as connection:
            await connection.execute(text(f"CREATE SCHEMA {schema}"))
            await connection.execute(text(f"SET LOCAL search_path TO {schema}"))
            await connection.run_sync(migrate)
            await connection.execute(text(f"DROP SCHEMA {schema}"))
    finally:
        await engine.dispose()
