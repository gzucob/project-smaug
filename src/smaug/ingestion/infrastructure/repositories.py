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

    async def find_latest(
        self, ticker: str, module: str, *, cvm_code: str | None = None
    ) -> RawIngestion | None:
        """The newest snapshot for a module, keyed by registrant when one is given.

        A ticker outside the company it was collected under finds nothing on the
        ticker key (ADR 0030): the mirror stores ELET3/5/6 once, under the filer.
        So a caller that can name the registrant passes it, and only a brapi
        caller — which has none — falls back to the ticker.
        """
        key = (
            RawIngestionDocument.cvm_code == cvm_code
            if cvm_code is not None
            else RawIngestionDocument.ticker == ticker
        )
        document = (
            await RawIngestionDocument.find(key, RawIngestionDocument.module == module)
            .sort("-fetched_at")
            .first_or_none()
        )
        return self._to_entity(document) if document is not None else None

    async def unlinked_tickers(self) -> tuple[str, ...]:
        """Tickers with CVM documents mirrored before the key moved (ADR 0030)."""
        collection = RawIngestionDocument.get_pymongo_collection()
        tickers = await collection.distinct("ticker", self._unlinked())
        return tuple(sorted(str(ticker) for ticker in tickers))

    async def link_registrant(self, ticker: str, cvm_code: str) -> int:
        """Stamp the registrant on ``ticker``'s unlinked CVM documents.

        Scoped to the documents that lack one, so a re-run is a no-op and a
        document that already names a *different* filer is never overwritten —
        this fills a gap, it does not restate a fact.
        """
        collection = RawIngestionDocument.get_pymongo_collection()
        result = await collection.update_many(
            {"ticker": ticker, **self._unlinked()}, {"$set": {"cvm_code": cvm_code}}
        )
        return int(result.modified_count)

    async def mirrored_for(self, module: str, *, file: str | None = None) -> set[str]:
        """Which registrants this module has already been mirrored for.

        The module is half of the predicate and the archive is the other half: a
        company is done for ``CAPITAL`` in ``fre_cia_aberta_2019.zip`` when a
        document says that module was read from exactly that file. Asking by
        registrant alone answered for a module nobody had ever collected (#178).

        ``file`` is ``None`` for a module that comes from no archive — B3 returns
        the whole history in one call, so holding the module at all is what marks
        it done. Lets a whole-exchange run resume where it stopped instead of
        appending a second copy of everything it already holds.
        """
        collection = RawIngestionDocument.get_pymongo_collection()
        query: dict[str, object] = {"module": module}
        if file is not None:
            query["request.file"] = file
        codes = await collection.distinct("cvm_code", query)
        return {str(code) for code in codes if code is not None}

    @staticmethod
    def _unlinked() -> dict[str, object]:
        # Documents written before the field existed have no key at all; ones
        # written by a source that names no registrant have it explicitly null.
        return {"source": "cvm", "cvm_code": None}

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
            cvm_code=ingestion.cvm_code,
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
            cvm_code=document.cvm_code,
        )
