"""Portfolio SQL row <-> entity mapping (no database connection, #151).

Mirrors ``test_sql_repository_mapping.py``'s shape: ``_to_row``/``_to_entity``
are pure attribute mappers, exercised directly on a transient ORM instance.
"""

from datetime import UTC, datetime

from smaug.portfolio.domain.entities import PortfolioTicker
from smaug.portfolio.infrastructure.sql_repository import _to_entity, _to_row


def test_a_portfolio_ticker_round_trips_through_the_row() -> None:
    entity = PortfolioTicker(ticker="PETR4", added_at=datetime(2026, 8, 4, tzinfo=UTC))

    row = _to_row(entity)
    assert row.ticker == "PETR4"
    assert row.added_at == datetime(2026, 8, 4, tzinfo=UTC)

    assert _to_entity(row) == entity
