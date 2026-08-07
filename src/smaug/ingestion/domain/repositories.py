"""Repository interface for raw ingestions (defined in the domain).

The application layer depends only on this Protocol; the concrete Beanie
implementation lives in infrastructure and never leaks its document model
into the domain (plan §3.1).
"""

from __future__ import annotations

from typing import Protocol

from smaug.ingestion.domain.entities import RawIngestion, RawIngestionWrite
from smaug.ingestion.domain.runs import IngestionRun


class RawIngestionRepository(Protocol):
    """Append-only store of raw ingestion snapshots."""

    async def add(self, ingestion: RawIngestion) -> RawIngestionWrite:
        """Persist one unique snapshot, reporting whether this attempt created it."""
        ...

    async def find_latest(
        self, ticker: str, module: str, *, cvm_code: str | None = None
    ) -> RawIngestion | None:
        """The most recent snapshot for a module, by registrant when given."""
        ...

    async def unlinked_tickers(self) -> tuple[str, ...]:
        """Tickers whose CVM documents do not yet name their registrant."""
        ...

    async def link_registrant(self, ticker: str, cvm_code: str) -> int:
        """Stamp ``cvm_code`` on ``ticker``'s unlinked CVM documents; count them."""
        ...

    async def mirrored_for(
        self, module: str, *, artifact_id: str | None = None
    ) -> set[str]:
        """Registrants holding ``module``, scoped to immutable source content."""
        ...


class IngestionRunRepository(Protocol):
    """Store and query ingestion command provenance."""

    async def add(self, run: IngestionRun) -> IngestionRun:
        """Persist a newly started run."""
        ...

    async def update(self, run: IngestionRun) -> IngestionRun:
        """Persist the latest lifecycle state for an existing run."""
        ...

    async def get(self, run_id: str) -> IngestionRun | None:
        """Find one run by its public id."""
        ...

    async def recent(self, limit: int) -> tuple[IngestionRun, ...]:
        """Return the most recently started runs, newest first."""
        ...
