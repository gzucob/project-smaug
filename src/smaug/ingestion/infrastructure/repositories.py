"""Beanie-backed implementation of ``RawIngestionRepository``.

The document model never leaks: ``_to_entity`` / ``_to_document`` do the
translation inside the repository (plan §3.1). Append-only — ``add`` always
inserts a new document, never overwrites.
"""

from __future__ import annotations

from smaug.ingestion.domain.entities import RawIngestion
from smaug.ingestion.domain.runs import (
    IngestionRun,
    IngestionRunCounts,
    IngestionRunParameters,
    IngestionRunStatus,
    ParserIdentity,
    TickerScope,
)
from smaug.ingestion.infrastructure.models import (
    IngestionRunDocument,
    RawIngestionDocument,
)


class BeanieRawIngestionRepository:
    """Concrete repository over the ``raw_ingestions`` collection."""

    async def add(self, ingestion: RawIngestion) -> RawIngestion:
        if ingestion.run_id is None or not ingestion.run_id.strip():
            raise ValueError("new raw ingestions require a run_id")
        document = self._to_document(ingestion)
        await document.insert()
        return self._to_entity(document)

    async def find_latest(
        self, ticker: str, module: str, *, cvm_code: str | None = None
    ) -> RawIngestion | None:
        """The newest snapshot for a module, keyed by registrant when one is given.

        A ticker outside the company it was collected under finds nothing on the
        ticker key (ADR 0030): the mirror stores ELET3/5/6 once, under the filer.
        So a caller that can name the registrant passes it, and only a caller
        that cannot falls back to the ticker.
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

    async def mirrored_for(
        self, module: str, *, artifact_id: str | None = None
    ) -> set[str]:
        """Which registrants this module has already been mirrored for.

        The module is half of the predicate and immutable archive content is the
        other half. A CVM republication keeps its filename but gets a new artifact
        id, so it is collected again; identical bytes retain the same id.

        ``artifact_id`` is ``None`` for a module that comes from no archive — B3 returns
        the whole history in one call, so holding the module at all is what marks
        it done. Lets a whole-exchange run resume where it stopped instead of
        appending a second copy of everything it already holds.
        """
        collection = RawIngestionDocument.get_pymongo_collection()
        query: dict[str, object] = {"module": module}
        if artifact_id is not None:
            query["artifact_id"] = artifact_id
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
            run_id=ingestion.run_id,
            artifact_id=ingestion.artifact_id,
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
            run_id=getattr(document, "run_id", None),
            artifact_id=getattr(document, "artifact_id", None),
            cvm_code=document.cvm_code,
        )


class BeanieIngestionRunRepository:
    """Concrete repository over the ``ingestion_runs`` collection."""

    async def add(self, run: IngestionRun) -> IngestionRun:
        document = self._to_document(run)
        await document.insert()
        return self._to_entity(document)

    async def update(self, run: IngestionRun) -> IngestionRun:
        document = await IngestionRunDocument.find_one(
            IngestionRunDocument.run_id == run.run_id
        )
        if document is None:
            raise LookupError(f"ingestion run not found: {run.run_id}")
        replacement = self._to_document(run)
        replacement.id = document.id
        await replacement.replace()
        return self._to_entity(replacement)

    async def get(self, run_id: str) -> IngestionRun | None:
        document = await IngestionRunDocument.find_one(
            IngestionRunDocument.run_id == run_id
        )
        return self._to_entity(document) if document is not None else None

    async def recent(self, limit: int) -> tuple[IngestionRun, ...]:
        documents = (
            await IngestionRunDocument.find_all()
            .sort("-started_at")
            .limit(limit)
            .to_list()
        )
        return tuple(self._to_entity(document) for document in documents)

    @staticmethod
    def _to_document(run: IngestionRun) -> IngestionRunDocument:
        parameters = run.parameters
        return IngestionRunDocument(
            run_id=run.run_id,
            started_at=run.started_at,
            ended_at=run.ended_at,
            status=run.status.value,
            parameters={
                "ticker_scope": parameters.ticker_scope.value,
                "tickers": list(parameters.tickers),
                "years": list(parameters.years),
                "document": parameters.document,
                "modules": list(parameters.modules),
                "force": parameters.force,
                "verbose": parameters.verbose,
            },
            application_commit=run.application_commit,
            parsers=[
                {"name": parser.name, "version": parser.version}
                for parser in run.parsers
            ],
            artifact_ids=list(run.artifact_ids),
            counts={
                "planned": run.counts.planned,
                "excluded": run.counts.excluded,
                "stored": run.counts.stored,
                "skipped": run.counts.skipped,
                "error": run.counts.error,
                "aborted": run.counts.aborted,
            },
            failure=run.failure,
        )

    @staticmethod
    def _to_entity(document: IngestionRunDocument) -> IngestionRun:
        parameters = document.parameters
        counts = document.counts
        return IngestionRun(
            run_id=document.run_id,
            started_at=document.started_at,
            ended_at=document.ended_at,
            status=IngestionRunStatus(document.status),
            parameters=IngestionRunParameters(
                ticker_scope=TickerScope(str(parameters["ticker_scope"])),
                tickers=tuple(str(value) for value in parameters["tickers"]),
                years=tuple(int(value) for value in parameters["years"]),
                document=str(parameters["document"]),
                modules=tuple(str(value) for value in parameters["modules"]),
                force=bool(parameters["force"]),
                verbose=bool(parameters["verbose"]),
            ),
            application_commit=document.application_commit,
            parsers=tuple(
                ParserIdentity(str(parser["name"]), int(parser["version"]))
                for parser in document.parsers
            ),
            artifact_ids=tuple(getattr(document, "artifact_ids", ())),
            counts=IngestionRunCounts(
                planned=counts.get("planned", 0),
                excluded=counts.get("excluded", 0),
                stored=counts["stored"],
                skipped=counts["skipped"],
                error=counts["error"],
                aborted=counts["aborted"],
            ),
            failure=document.failure,
        )
