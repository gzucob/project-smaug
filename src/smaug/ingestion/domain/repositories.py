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

    async def mirrored_for(self, module: str, *, file: str | None = None) -> set[str]:
        """Registrants the mirror already holds ``module`` for, scoped to ``file``."""
        ...
