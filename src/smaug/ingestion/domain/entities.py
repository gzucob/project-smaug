"""Domain entity for a single raw ingestion record.

Pure domain: a frozen snapshot of one brapi API call. No Beanie, no motor,
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
    """One faithful, uninterpreted snapshot of a brapi module response."""

    ticker: str
    source: str
    module: str
    fetched_at: datetime
    request: Mapping[str, Any]
    http_status: int
    payload: Mapping[str, Any]
    id: str | None = None
