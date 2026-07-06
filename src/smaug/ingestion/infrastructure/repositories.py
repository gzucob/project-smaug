"""Beanie-backed implementation of ``RawIngestionRepository``.

The document model never leaks: ``_to_entity`` / ``_to_document`` do the
translation inside the repository (plan §3.1). Append-only — ``add`` always
inserts a new document, never overwrites.
"""

from __future__ import annotations

from smaug.ingestion.domain.entities import RawIngestion
from smaug.ingestion.infrastructure.models import RawIngestionDocument


class BeanieRawIngestionRepository:
    """Concrete repository over the ``raw_ingestions`` collection."""

    async def add(self, ingestion: RawIngestion) -> RawIngestion:
        document = self._to_document(ingestion)
        await document.insert()
        return self._to_entity(document)

    async def find_latest(self, ticker: str, module: str) -> RawIngestion | None:
        document = (
            await RawIngestionDocument.find(
                RawIngestionDocument.ticker == ticker,
                RawIngestionDocument.module == module,
            )
            .sort("-fetched_at")
            .first_or_none()
        )
        return self._to_entity(document) if document is not None else None

    @staticmethod
    def _to_document(ingestion: RawIngestion) -> RawIngestionDocument:
        return RawIngestionDocument(
            ticker=ingestion.ticker,
            source=ingestion.source,
            module=ingestion.module,
            fetched_at=ingestion.fetched_at,
            request=dict(ingestion.request),
            http_status=ingestion.http_status,
            payload=dict(ingestion.payload),
        )

    @staticmethod
    def _to_entity(document: RawIngestionDocument) -> RawIngestion:
        return RawIngestion(
            id=str(document.id) if document.id is not None else None,
            ticker=document.ticker,
            source=document.source,
            module=document.module,
            fetched_at=document.fetched_at,
            request=document.request,
            http_status=document.http_status,
            payload=document.payload,
        )
