"""Domain events published by the ingestion context."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from smaug.shared.events import DomainEvent


@dataclass(frozen=True)
class RawIngestionStored(DomainEvent):
    """A raw snapshot was persisted. Phase 2 will subscribe to this."""

    ticker: str
    module: str
    fetched_at: datetime
    http_status: int
