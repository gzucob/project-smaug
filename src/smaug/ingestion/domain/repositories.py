"""Repository interface for raw ingestions (defined in the domain).

The application layer depends only on this Protocol; the concrete Beanie
implementation lives in infrastructure and never leaks its document model
into the domain (plan §3.1).
"""

from __future__ import annotations

from typing import Protocol

from smaug.ingestion.domain.entities import RawIngestion, RawIngestionWrite
from smaug.ingestion.domain.failures import IngestionFailure
from smaug.ingestion.domain.runs import IngestionRun
from smaug.ingestion.domain.validation import IngestionValidationReport


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


class IngestionFailureRepository(Protocol):
    """Durable inventory of failed calls and their retry history."""

    async def add(self, failure: IngestionFailure) -> IngestionFailure:
        """Persist one newly failed call."""
        ...

    async def update(self, failure: IngestionFailure) -> IngestionFailure:
        """Persist a new attempt or resolution without discarding history."""
        ...

    async def get(self, failure_id: str) -> IngestionFailure | None:
        """Find one failure by its public id."""
        ...

    async def open_for_run(self, run_id: str) -> tuple[IngestionFailure, ...]:
        """Return unresolved calls originally recorded against one run."""
        ...

    async def recent(self, limit: int) -> tuple[IngestionFailure, ...]:
        """Return recent failures, resolved or open, newest first."""
        ...


class IngestionValidationRepository(Protocol):
    """Durable validation and quarantine facts for ingestion runs."""

    async def add(self, report: IngestionValidationReport) -> IngestionValidationReport:
        """Persist one batch admission decision."""
        ...

    async def get(self, report_id: str) -> IngestionValidationReport | None:
        """Find one validation report by its public id."""
        ...

    async def recent(
        self, limit: int, *, run_id: str | None = None
    ) -> tuple[IngestionValidationReport, ...]:
        """Return reports newest first, optionally scoped to one run."""
        ...

    async def update(
        self, report: IngestionValidationReport
    ) -> IngestionValidationReport:
        """Persist an operator approval without changing the original evidence."""
        ...
