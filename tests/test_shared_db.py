"""Mongo composition root registers the raw mirror document models."""

import pytest

from smaug.shared import db
from smaug.shared.config import Settings


async def test_database_initialization_registers_models_and_returns_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(mongo_uri="mongodb://example.test:27017", mongo_db="test_db")
    client_database = object()

    class Client:
        def __init__(self, uri: str) -> None:
            assert uri == settings.mongo_uri

        def __getitem__(self, name: str) -> object:
            assert name == settings.mongo_db
            return client_database

    registered: list[str] = []

    async def initialize(
        *, database: object, document_models: list[type[object]]
    ) -> None:
        assert database is client_database
        registered.extend(model.__name__ for model in document_models)

    monkeypatch.setattr(db, "AsyncMongoClient", Client)
    monkeypatch.setattr(db, "init_beanie", initialize)

    assert isinstance(await db.init_database(settings), Client)
    assert registered == [
        "RawIngestionDocument",
        "IngestionRunDocument",
        "IngestionFailureDocument",
        "IngestionValidationDocument",
        "SourceAcquisitionDocument",
        "SourceFileSessionDocument",
        "SourceInputArchiveDocument",
        "SourceInputRowDocument",
    ]
