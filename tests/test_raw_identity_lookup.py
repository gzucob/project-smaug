"""MongoDB identity lookups remain indexed as an archive's mirror grows."""

import os
from dataclasses import replace
from uuid import uuid4

import pytest
from beanie import init_beanie
from pymongo import AsyncMongoClient
from pymongo.monitoring import (
    CommandFailedEvent,
    CommandListener,
    CommandStartedEvent,
    CommandSucceededEvent,
)

from smaug.ingestion.domain.identity import filing_identity
from smaug.ingestion.infrastructure.models import RawIngestionDocument
from smaug.ingestion.infrastructure.repositories import BeanieRawIngestionRepository
from tests.fakes import make_snapshot


class _IdentityQueryListener(CommandListener):
    def __init__(self) -> None:
        self.query: dict[str, object] | None = None

    def started(self, event: CommandStartedEvent) -> None:
        if (
            event.command_name == "find"
            and event.command.get("find") == "raw_ingestions"
        ):
            self.query = dict(event.command["filter"])

    def succeeded(self, event: CommandSucceededEvent) -> None:
        pass

    def failed(self, event: CommandFailedEvent) -> None:
        pass


@pytest.mark.skipif(
    not os.environ.get("SMAUG_TEST_MONGO_URI"),
    reason="Set SMAUG_TEST_MONGO_URI to test lookups in an isolated database",
)
@pytest.mark.parametrize("artifact_id", [None, "sha256:" + "a" * 64])
async def test_identity_lookup_reads_one_document_and_preserves_amendments(
    artifact_id: str | None,
) -> None:
    listener = _IdentityQueryListener()
    client = AsyncMongoClient(
        os.environ["SMAUG_TEST_MONGO_URI"], event_listeners=[listener]
    )
    database = client["identity_lookup_" + uuid4().hex]
    try:
        await init_beanie(database=database, document_models=[RawIngestionDocument])
        base = replace(
            make_snapshot("TEST3", "DRE", {"value": "100"}),
            source="cvm" if artifact_id else "b3",
            artifact_id=artifact_id,
            cvm_code="9512",
            run_id="initial",
        )
        filings = [replace(base, request={"record": number}) for number in range(256)]
        repository = BeanieRawIngestionRepository()
        await RawIngestionDocument.insert_many(
            [
                repository._to_document(filing, filing_identity(filing))
                for filing in filings
            ]
        )
        repeated = await repository.add(
            replace(filings[-1], ticker="TEST4", run_id="retry")
        )
        assert not repeated.created
        assert repeated.ingestion.ticker == "TEST3"
        amendment = await repository.add(
            replace(filings[-1], payload={"value": "200"}, run_id="amendment")
        )
        assert amendment.created
        assert amendment.ingestion.id != repeated.ingestion.id
        assert await database.raw_ingestions.count_documents({}) == 257
        assert listener.query is not None
        # Explain the actual repository query, rather than a test's reconstruction.
        explained = await database.command(
            "explain",
            {"find": "raw_ingestions", "filter": listener.query, "limit": 1},
            verbosity="executionStats",
        )
        stats = explained["executionStats"]
        assert stats["nReturned"] == 1
        assert stats["totalDocsExamined"] == 1
        assert stats["totalKeysExamined"] == 1
    finally:
        await client.drop_database(database.name)
        await client.close()
