"""Beanie repository document -> entity mapping (no database connection).

Beanie 2.x needs an initialized collection just to *construct* a Document, so
``_to_document`` is exercised at runtime, not here. ``_to_entity`` only reads
attributes, so a lightweight stand-in covers the id/None conversion logic.
"""

from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from smaug.ingestion.domain.runs import IngestionRunStatus, TickerScope
from smaug.ingestion.infrastructure.models import RawIngestionDocument
from smaug.ingestion.infrastructure.repositories import (
    BeanieIngestionRunRepository,
    BeanieRawIngestionRepository,
)
from tests.fakes import make_snapshot


def _fake_document(**overrides: object) -> SimpleNamespace:
    base: dict[str, object] = {
        "id": "abc123",
        "ticker": "PETR4",
        "source": "cvm",
        "module": "financialData",
        "fetched_at": datetime(2026, 7, 2, tzinfo=UTC),
        "request": {"file": "dfp_cia_aberta_2024.zip", "statement": "DRE"},
        "http_status": 200,
        "payload": {"results": [{"symbol": "PETR4"}]},
        "cvm_code": None,  # a document filed before the key moved (ADR 0030)
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_should_map_document_to_entity_and_stringify_id() -> None:
    artifact_id = "sha256:" + "a" * 64
    entity = BeanieRawIngestionRepository._to_entity(  # type: ignore[arg-type]
        _fake_document(artifact_id=artifact_id)
    )

    assert entity.id == "abc123"
    assert entity.ticker == "PETR4"
    assert entity.module == "financialData"
    assert entity.payload == {"results": [{"symbol": "PETR4"}]}
    assert entity.artifact_id == artifact_id


def test_should_map_document_with_none_id_to_none() -> None:
    entity = BeanieRawIngestionRepository._to_entity(_fake_document(id=None))  # type: ignore[arg-type]

    assert entity.id is None


def test_should_read_a_legacy_raw_document_without_run_id() -> None:
    document = _fake_document()

    entity = BeanieRawIngestionRepository._to_entity(document)  # type: ignore[arg-type]

    assert entity.run_id is None
    assert entity.artifact_id is None


def test_should_map_ingestion_run_document_to_domain() -> None:
    document = SimpleNamespace(
        run_id="run-123",
        started_at=datetime(2026, 8, 6, 12, 0, tzinfo=UTC),
        ended_at=datetime(2026, 8, 6, 12, 5, tzinfo=UTC),
        status="completed_with_errors",
        parameters={
            "ticker_scope": "all",
            "tickers": ["PETR4", "VALE3"],
            "years": [2023, 2024],
            "document": "DFP",
            "modules": ["DRE", "BPA"],
            "force": False,
            "verbose": True,
        },
        application_commit="abc123",
        parsers=[{"name": "cvm.statements.csv", "version": 2}],
        artifact_ids=["sha256:" + "a" * 64],
        counts={
            "planned": 7,
            "stored": 3,
            "unchanged": 1,
            "skipped": 1,
            "error": 1,
            "aborted": 0,
        },
        failure=None,
    )

    run = BeanieIngestionRunRepository._to_entity(document)  # type: ignore[arg-type]

    assert run.status is IngestionRunStatus.COMPLETED_WITH_ERRORS
    assert run.parameters.ticker_scope is TickerScope.ALL
    assert run.parameters.tickers == ("PETR4", "VALE3")
    assert run.parsers[0].name == "cvm.statements.csv"
    assert run.artifact_ids == ("sha256:" + "a" * 64,)
    assert run.counts.error == 1
    assert run.counts.unchanged == 1
    assert run.counts.remaining == 1


async def test_should_reject_a_new_raw_document_without_run_id() -> None:
    repository = BeanieRawIngestionRepository()

    with pytest.raises(ValueError, match="require a run_id"):
        await repository.add(make_snapshot("PETR4", "DRE", {}))

    with pytest.raises(ValueError, match="require a run_id"):
        await repository.add(replace(make_snapshot("PETR4", "DRE", {}), run_id=""))

    with pytest.raises(ValueError, match="require a run_id"):
        await repository.add(replace(make_snapshot("PETR4", "DRE", {}), run_id=" "))


def test_content_identity_unique_index_excludes_legacy_documents() -> None:
    index = next(
        index
        for index in RawIngestionDocument.Settings.indexes
        if index.document["name"] == "source_artifact_registrant_filing_content_unique"
    )

    assert index.document["unique"] is True
    assert index.document["partialFilterExpression"] == {
        "registrant_key": {"$type": "string"},
        "filing_discriminator": {"$type": "string"},
        "content_hash": {"$type": "string"},
    }
