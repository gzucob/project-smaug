"""Repository interface for raw ingestions (defined in the domain).

The application layer depends only on this Protocol; the concrete Beanie
implementation lives in infrastructure and never leaks its document model
into the domain (plan §3.1).
"""

from __future__ import annotations

from typing import Protocol

from smaug.ingestion.domain.entities import RawIngestion


class RawIngestionRepository(Protocol):
    """Append-only store of raw ingestion snapshots."""

    async def add(self, ingestion: RawIngestion) -> RawIngestion:
        """Persist a new snapshot (never overwrites) and return it with its id."""
        ...

    async def find_latest(self, ticker: str, module: str) -> RawIngestion | None:
        """Return the most recent snapshot for ``ticker``/``module``, if any."""
        ...
