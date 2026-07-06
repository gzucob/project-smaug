"""Beanie repository document -> entity mapping (no database connection).

Beanie 2.x needs an initialized collection just to *construct* a Document, so
``_to_document`` is exercised at runtime, not here. ``_to_entity`` only reads
attributes, so a lightweight stand-in covers the id/None conversion logic.
"""

from datetime import UTC, datetime
from types import SimpleNamespace

from smaug.ingestion.infrastructure.repositories import BeanieRawIngestionRepository


def _fake_document(**overrides: object) -> SimpleNamespace:
    base: dict[str, object] = {
        "id": "abc123",
        "ticker": "PETR4",
        "source": "brapi",
        "module": "financialData",
        "fetched_at": datetime(2026, 7, 2, tzinfo=UTC),
        "request": {"url": "https://brapi.dev/api/quote/PETR4"},
        "http_status": 200,
        "payload": {"results": [{"symbol": "PETR4"}]},
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_should_map_document_to_entity_and_stringify_id() -> None:
    entity = BeanieRawIngestionRepository._to_entity(_fake_document())  # type: ignore[arg-type]

    assert entity.id == "abc123"
    assert entity.ticker == "PETR4"
    assert entity.module == "financialData"
    assert entity.payload == {"results": [{"symbol": "PETR4"}]}


def test_should_map_document_with_none_id_to_none() -> None:
    entity = BeanieRawIngestionRepository._to_entity(_fake_document(id=None))  # type: ignore[arg-type]

    assert entity.id is None
