"""Beanie document model for the ``raw_ingestions`` collection (plan §4.1).

Append-only mirror: one document per brapi module call. The compound index
(ticker, module, fetched_at desc) makes "latest snapshot" lookups cheap for
the completeness report.

A CVM document also carries the registrant that filed it, indexed the same way:
that is the key its readers use (ADR 0030), because a filing belongs to a company
and not to one of the codes it trades under. The field is nullable — a brapi
document has no registrant to name.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from beanie import Document
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
    cvm_code: str | None = None

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
        ]
