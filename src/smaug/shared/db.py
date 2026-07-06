"""MongoDB connection + Beanie initialization.

Thin infrastructure helper used by the composition root (entrypoints).
Keeps motor/Beanie wiring out of the application and domain layers.
"""

from __future__ import annotations

from typing import Any

from beanie import init_beanie
from pymongo import AsyncMongoClient

from smaug.ingestion.infrastructure.models import RawIngestionDocument
from smaug.shared.config import Settings


async def init_database(settings: Settings) -> AsyncMongoClient[dict[str, Any]]:
    """Connect to Mongo and register Beanie document models.

    Returns the pymongo async client so the caller can ``await client.close()``
    on shutdown. (Beanie 2.x uses pymongo's async driver, not motor.)
    """
    client: AsyncMongoClient[dict[str, Any]] = AsyncMongoClient(settings.mongo_uri)
    await init_beanie(
        database=client[settings.mongo_db],
        document_models=[RawIngestionDocument],
    )
    return client
