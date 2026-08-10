"""Beanie document model for the ``raw_ingestions`` collection (plan §4.1).

Append-only mirror: one document per distinct source filing. The compound index
(ticker, module, fetched_at desc) makes "latest snapshot" lookups cheap for the
completeness report.

A CVM document also carries the registrant that filed it, indexed the same way:
that is the key its readers use (ADR 0030), because a filing belongs to a company
and not to one of the codes it trades under. The field is nullable — a
document from a source that names no filer has no registrant to record.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from beanie import Document
from pydantic import Field
from pymongo import ASCENDING, DESCENDING, IndexModel


class RawIngestionDocument(Document):
    """Stored shape of a raw ingestion snapshot."""

    ticker: str
    source: str
    module: str
    fetched_at: datetime
    request: dict[str, Any]
    http_status: int
    payload: dict[str, Any]
    run_id: str | None = None
    artifact_id: str | None = None
    cvm_code: str | None = None
    # Legacy mirror documents predate content identity and intentionally remain
    # outside the partial unique index below. Their old audit history is neither
    # rewritten nor allowed to prevent new semantically-idempotent writes.
    registrant_key: str | None = None
    filing_discriminator: str | None = None
    content_hash: str | None = None

    class Settings:
        name = "raw_ingestions"
        indexes = [
            IndexModel(
                [
                    ("ticker", ASCENDING),
                    ("module", ASCENDING),
                    ("fetched_at", DESCENDING),
                ],
                name="ticker_module_fetched_at",
            ),
            IndexModel(
                [
                    ("cvm_code", ASCENDING),
                    ("module", ASCENDING),
                    ("fetched_at", DESCENDING),
                ],
                name="cvm_code_module_fetched_at",
            ),
            IndexModel([("run_id", ASCENDING)], name="run_id"),
            IndexModel([("artifact_id", ASCENDING)], name="artifact_id"),
            IndexModel(
                [
                    ("source", ASCENDING),
                    ("artifact_id", ASCENDING),
                    ("registrant_key", ASCENDING),
                    ("module", ASCENDING),
                    ("filing_discriminator", ASCENDING),
                    ("content_hash", ASCENDING),
                ],
                name="source_artifact_registrant_filing_content_unique",
                unique=True,
                partialFilterExpression={
                    "registrant_key": {"$type": "string"},
                    "filing_discriminator": {"$type": "string"},
                    "content_hash": {"$type": "string"},
                },
            ),
        ]


class IngestionRunDocument(Document):
    """Stored lifecycle and provenance of one ingestion command."""

    run_id: str
    started_at: datetime
    ended_at: datetime | None = None
    status: str
    parameters: dict[str, Any]
    application_commit: str
    parsers: list[dict[str, Any]]
    counts: dict[str, int]
    artifact_ids: list[str] = Field(default_factory=list)
    failure: str | None = None

    class Settings:
        name = "ingestion_runs"
        indexes = [
            IndexModel([("run_id", ASCENDING)], name="run_id_unique", unique=True),
            IndexModel([("started_at", DESCENDING)], name="started_at"),
            IndexModel(
                [("status", ASCENDING), ("started_at", DESCENDING)],
                name="status_started_at",
            ),
        ]


class IngestionValidationDocument(Document):
    """One versioned source-batch validation report and its raw evidence."""

    report_id: str
    run_id: str
    recorded_at: datetime
    status: str
    source: str
    batch: str
    module: str | None = None
    artifact_id: str | None = None
    parser: dict[str, object]
    rules: list[dict[str, object]]
    observations: dict[str, object]
    findings: list[dict[str, str]]
    evidence: dict[str, object]
    approved_at: datetime | None = None
    approval_note: str | None = None

    class Settings:
        name = "ingestion_validations"
        indexes = [
            IndexModel(
                [("report_id", ASCENDING)], name="report_id_unique", unique=True
            ),
            IndexModel([("run_id", ASCENDING), ("recorded_at", DESCENDING)]),
            IndexModel([("status", ASCENDING), ("recorded_at", DESCENDING)]),
            IndexModel([("artifact_id", ASCENDING)], name="artifact_id"),
        ]
