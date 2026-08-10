"""Source-batch validation reports are durable, versioned, and reviewable."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from itertools import chain, repeat

import pytest

from smaug.ingestion.application.validation import IngestionValidationService
from smaug.ingestion.domain.runs import ParserIdentity
from smaug.ingestion.domain.validation import (
    BatchValidationStatus,
    SourceBatchValidation,
    ValidationFinding,
    ValidationRule,
)
from tests.fakes import FakeIngestionValidationRepository

NOW = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)


def _validation(*, rejected: bool = False) -> SourceBatchValidation:
    return SourceBatchValidation(
        source="cvm",
        batch="dfp_cia_aberta_2024.zip",
        module="DRE",
        artifact_id="sha256:" + "a" * 64,
        parser=ParserIdentity("cvm.statements.csv", 1),
        rules=(ValidationRule("csv-schema", 1),),
        observations={"rows": 123, "expected_period_seen": True},
        findings=(ValidationFinding("csv-schema", "DRE lacks VL_CONTA"),)
        if rejected
        else (),
    )


async def test_validation_records_rules_and_approval_without_releasing_data() -> None:
    repository = FakeIngestionValidationRepository()
    times = chain((NOW,), repeat(NOW + timedelta(minutes=1)))
    service = IngestionValidationService(
        repository,
        clock=lambda: next(times),
        id_factory=lambda: "validation-123",
    )

    report = await service.record("run-123", _validation(rejected=True))
    approved = await service.approve(report.report_id, "parser fixed in v2")

    assert report.status is BatchValidationStatus.QUARANTINED
    assert approved.status is BatchValidationStatus.APPROVED
    assert approved.approval_note == "parser fixed in v2"
    assert approved.validation.rules == (ValidationRule("csv-schema", 1),)
    assert approved.validation.findings == (
        ValidationFinding("csv-schema", "DRE lacks VL_CONTA"),
    )
    assert await service.recent(run_id="run-123") == (approved,)


async def test_accepted_batch_cannot_be_approved() -> None:
    service = IngestionValidationService(
        FakeIngestionValidationRepository(), id_factory=lambda: "validation-123"
    )
    report = await service.record("run-123", _validation())

    with pytest.raises(
        ValueError, match="only quarantined validation reports can be approved"
    ):
        await service.approve(report.report_id, "not needed")
