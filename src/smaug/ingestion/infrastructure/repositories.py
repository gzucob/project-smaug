"""Beanie-backed implementation of ``RawIngestionRepository``.

The document model never leaks: ``_to_entity`` / ``_to_document`` do the
translation inside the repository (plan §3.1). Append-only — ``add`` never
overwrites; an identical source fact returns its existing document instead.
"""

from __future__ import annotations

from datetime import datetime

from pymongo.errors import DuplicateKeyError

from smaug.ingestion.domain.entities import RawIngestion, RawIngestionWrite
from smaug.ingestion.domain.failures import (
    FailureAttempt,
    IngestionFailure,
    IngestionFailureClass,
    IngestionFailureStatus,
)
from smaug.ingestion.domain.identity import FilingIdentity, filing_identity
from smaug.ingestion.domain.runs import (
    IngestionRun,
    IngestionRunCounts,
    IngestionRunParameters,
    IngestionRunStatus,
    ParserIdentity,
    TickerScope,
)
from smaug.ingestion.domain.validation import (
    BatchValidationStatus,
    IngestionValidationReport,
    SourceBatchValidation,
    ValidationFinding,
    ValidationRule,
)
from smaug.ingestion.infrastructure.models import (
    IngestionFailureDocument,
    IngestionRunDocument,
    IngestionValidationDocument,
    RawIngestionDocument,
)


class BeanieRawIngestionRepository:
    """Concrete repository over the ``raw_ingestions`` collection."""

    async def add(self, ingestion: RawIngestion) -> RawIngestionWrite:
        if ingestion.run_id is None or not ingestion.run_id.strip():
            raise ValueError("new raw ingestions require a run_id")
        identity = filing_identity(ingestion)
        existing = await self._find_by_identity(identity)
        if existing is not None:
            return RawIngestionWrite(self._to_entity(existing), created=False)
        document = self._to_document(ingestion, identity)
        try:
            await document.insert()
        except DuplicateKeyError:
            # Another worker can insert between our lookup and insert. The unique
            # index is the authority; read its winner so the attempt stays a no-op.
            existing = await self._find_by_identity(identity)
            if existing is None:
                raise
            return RawIngestionWrite(self._to_entity(existing), created=False)
        return RawIngestionWrite(self._to_entity(document), created=True)

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
    def _to_document(
        ingestion: RawIngestion, identity: FilingIdentity
    ) -> RawIngestionDocument:
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
            registrant_key=identity.registrant_key,
            filing_discriminator=identity.filing_discriminator,
            content_hash=identity.content_hash,
        )

    @staticmethod
    async def _find_by_identity(
        identity: FilingIdentity,
    ) -> RawIngestionDocument | None:
        return await RawIngestionDocument.find_one(
            RawIngestionDocument.source == identity.source,
            RawIngestionDocument.artifact_id == identity.artifact_id,
            RawIngestionDocument.registrant_key == identity.registrant_key,
            RawIngestionDocument.module == identity.module,
            RawIngestionDocument.filing_discriminator == identity.filing_discriminator,
            RawIngestionDocument.content_hash == identity.content_hash,
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
                "unchanged": run.counts.unchanged,
                "skipped": run.counts.skipped,
                "error": run.counts.error,
                "quarantined": run.counts.quarantined,
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
                unchanged=counts.get("unchanged", 0),
                skipped=counts["skipped"],
                error=counts["error"],
                quarantined=counts.get("quarantined", 0),
                aborted=counts["aborted"],
            ),
            failure=document.failure,
        )


class BeanieIngestionFailureRepository:
    """Concrete retry inventory over the ``ingestion_failures`` collection."""

    async def add(self, failure: IngestionFailure) -> IngestionFailure:
        document = self._to_document(failure)
        await document.insert()
        return self._to_entity(document)

    async def update(self, failure: IngestionFailure) -> IngestionFailure:
        document = await IngestionFailureDocument.find_one(
            IngestionFailureDocument.failure_id == failure.failure_id
        )
        if document is None:
            raise LookupError(f"ingestion failure not found: {failure.failure_id}")
        replacement = self._to_document(failure)
        replacement.id = document.id
        await replacement.replace()
        return self._to_entity(replacement)

    async def get(self, failure_id: str) -> IngestionFailure | None:
        document = await IngestionFailureDocument.find_one(
            IngestionFailureDocument.failure_id == failure_id
        )
        return self._to_entity(document) if document is not None else None

    async def open_for_run(self, run_id: str) -> tuple[IngestionFailure, ...]:
        documents = (
            await IngestionFailureDocument.find(
                IngestionFailureDocument.origin_run_id == run_id,
                IngestionFailureDocument.status == IngestionFailureStatus.OPEN.value,
            )
            .sort("last_failed_at")
            .to_list()
        )
        return tuple(self._to_entity(document) for document in documents)

    async def recent(self, limit: int) -> tuple[IngestionFailure, ...]:
        documents = (
            await IngestionFailureDocument.find_all()
            .sort("-last_failed_at")
            .limit(limit)
            .to_list()
        )
        return tuple(self._to_entity(document) for document in documents)

    @staticmethod
    def _to_document(failure: IngestionFailure) -> IngestionFailureDocument:
        return IngestionFailureDocument(
            failure_id=failure.failure_id,
            origin_run_id=failure.origin_run_id,
            ticker=failure.ticker,
            registrant=failure.registrant,
            source=failure.source,
            module=failure.module,
            year=failure.year,
            artifact_id=failure.artifact_id,
            parser=_parser_to_document(failure.parser),
            failure_class=failure.failure_class.value,
            attempt_count=failure.attempt_count,
            first_failed_at=failure.first_failed_at,
            last_failed_at=failure.last_failed_at,
            detail=failure.detail,
            attempts=[
                {
                    "run_id": attempt.run_id,
                    "first_failed_at": attempt.first_failed_at,
                    "last_failed_at": attempt.last_failed_at,
                    "attempt_count": attempt.attempt_count,
                    "failure_class": attempt.failure_class.value,
                    "detail": attempt.detail,
                    "artifact_id": attempt.artifact_id,
                    "parser": _parser_to_document(attempt.parser),
                }
                for attempt in failure.attempts
            ],
            status=failure.status.value,
            resolved_at=failure.resolved_at,
            resolution_run_id=failure.resolution_run_id,
        )

    @staticmethod
    def _to_entity(document: IngestionFailureDocument) -> IngestionFailure:
        attempts = tuple(_attempt_from_document(value) for value in document.attempts)
        return IngestionFailure(
            failure_id=document.failure_id,
            origin_run_id=document.origin_run_id,
            ticker=document.ticker,
            registrant=document.registrant,
            source=document.source,
            module=document.module,
            year=document.year,
            artifact_id=document.artifact_id,
            parser=_parser_from_document(document.parser),
            failure_class=IngestionFailureClass(document.failure_class),
            attempt_count=document.attempt_count,
            first_failed_at=document.first_failed_at,
            last_failed_at=document.last_failed_at,
            detail=document.detail,
            attempts=attempts,
            status=IngestionFailureStatus(document.status),
            resolved_at=document.resolved_at,
            resolution_run_id=document.resolution_run_id,
        )


def _parser_to_document(parser: ParserIdentity) -> dict[str, object]:
    return {"name": parser.name, "version": parser.version}


def _parser_from_document(value: dict[str, object]) -> ParserIdentity:
    name = value.get("name")
    version = value.get("version")
    if not isinstance(name, str) or not isinstance(version, int):
        raise ValueError("stored ingestion failure has an invalid parser identity")
    return ParserIdentity(name, version)


def _attempt_from_document(value: dict[str, object]) -> FailureAttempt:
    run_id = value.get("run_id")
    first_failed_at = value.get("first_failed_at")
    last_failed_at = value.get("last_failed_at")
    attempt_count = value.get("attempt_count")
    failure_class = value.get("failure_class")
    detail = value.get("detail")
    artifact_id = value.get("artifact_id")
    parser = value.get("parser")
    if (
        not isinstance(run_id, str)
        or not isinstance(first_failed_at, datetime)
        or not isinstance(last_failed_at, datetime)
        or not isinstance(attempt_count, int)
        or not isinstance(failure_class, str)
        or not isinstance(detail, str)
        or artifact_id is not None
        and not isinstance(artifact_id, str)
        or not isinstance(parser, dict)
    ):
        raise ValueError("stored ingestion failure has an invalid attempt")
    return FailureAttempt(
        run_id=run_id,
        first_failed_at=first_failed_at,
        last_failed_at=last_failed_at,
        attempt_count=attempt_count,
        failure_class=IngestionFailureClass(failure_class),
        detail=detail,
        artifact_id=artifact_id,
        parser=_parser_from_document(parser),
    )


class BeanieIngestionValidationRepository:
    """Concrete repository over the ``ingestion_validations`` collection."""

    async def add(self, report: IngestionValidationReport) -> IngestionValidationReport:
        document = self._to_document(report)
        await document.insert()
        return self._to_entity(document)

    async def get(self, report_id: str) -> IngestionValidationReport | None:
        document = await IngestionValidationDocument.find_one(
            IngestionValidationDocument.report_id == report_id
        )
        return self._to_entity(document) if document is not None else None

    async def recent(
        self, limit: int, *, run_id: str | None = None
    ) -> tuple[IngestionValidationReport, ...]:
        query = IngestionValidationDocument.find_all()
        if run_id is not None:
            query = IngestionValidationDocument.find(
                IngestionValidationDocument.run_id == run_id
            )
        documents = await query.sort("-recorded_at").limit(limit).to_list()
        return tuple(self._to_entity(document) for document in documents)

    async def update(
        self, report: IngestionValidationReport
    ) -> IngestionValidationReport:
        document = await IngestionValidationDocument.find_one(
            IngestionValidationDocument.report_id == report.report_id
        )
        if document is None:
            raise LookupError(
                f"ingestion validation report not found: {report.report_id}"
            )
        replacement = self._to_document(report)
        replacement.id = document.id
        await replacement.replace()
        return self._to_entity(replacement)

    @staticmethod
    def _to_document(report: IngestionValidationReport) -> IngestionValidationDocument:
        validation = report.validation
        return IngestionValidationDocument(
            report_id=report.report_id,
            run_id=report.run_id,
            recorded_at=report.recorded_at,
            status=report.status.value,
            source=validation.source,
            batch=validation.batch,
            module=validation.module,
            artifact_id=validation.artifact_id,
            parser={
                "name": validation.parser.name,
                "version": validation.parser.version,
            },
            rules=[
                {"name": rule.name, "version": rule.version}
                for rule in validation.rules
            ],
            observations=dict(validation.observations),
            findings=[
                {"code": item.code, "detail": item.detail}
                for item in validation.findings
            ],
            evidence=dict(validation.evidence),
            approved_at=report.approved_at,
            approval_note=report.approval_note,
        )

    @staticmethod
    def _to_entity(document: IngestionValidationDocument) -> IngestionValidationReport:
        parser = document.parser
        parser_name = parser.get("name")
        parser_version = parser.get("version")
        if not isinstance(parser_name, str) or not isinstance(parser_version, int):
            raise ValueError("stored validation report has an invalid parser identity")
        rules: list[ValidationRule] = []
        for rule in document.rules:
            name = rule.get("name")
            version = rule.get("version")
            if not isinstance(name, str) or not isinstance(version, int):
                raise ValueError("stored validation report has an invalid rule")
            rules.append(ValidationRule(name, version))
        observations: dict[str, str | int | bool] = {}
        for key, value in document.observations.items():
            if not isinstance(value, (str, int, bool)):
                raise ValueError("stored validation report has an invalid observation")
            observations[key] = value
        validation = SourceBatchValidation(
            source=document.source,
            batch=document.batch,
            module=document.module,
            artifact_id=document.artifact_id,
            parser=ParserIdentity(parser_name, parser_version),
            rules=tuple(rules),
            observations=observations,
            findings=tuple(
                ValidationFinding(item["code"], item["detail"])
                for item in document.findings
            ),
            evidence=document.evidence,
        )
        return IngestionValidationReport(
            report_id=document.report_id,
            run_id=document.run_id,
            recorded_at=document.recorded_at,
            validation=validation,
            status=BatchValidationStatus(document.status),
            approved_at=document.approved_at,
            approval_note=document.approval_note,
        )
