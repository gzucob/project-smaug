"""Domain entity for a single raw ingestion record.

Pure domain: a frozen snapshot of one source call. No Beanie, no motor,
no httpx here (plan §3.1). The schema mirrors the ``raw_ingestions``
collection defined in plan §4.1.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True, slots=True)
class RawIngestion:
    """One faithful, uninterpreted snapshot of a source's response."""

    ticker: str
    source: str
    module: str
    fetched_at: datetime
    request: Mapping[str, Any]
    http_status: int
    payload: Mapping[str, Any]
    # ``None`` only exists for documents written before ingestion runs were
    # introduced. Every new application write supplies a run id.
    run_id: str | None = None
    id: str | None = None
    # The registrant that filed it (ADR 0030). ``ticker`` records which code the
    # collection was requested under and stays informational; this is what the
    # readers key on, so a company's classes share one mirror instead of a copy
    # each. ``None`` for a source that names no registrant.
    cvm_code: str | None = None
