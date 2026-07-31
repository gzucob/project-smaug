"""Stamp the registrant onto CVM documents mirrored before the key moved.

Every document already knows which company it belongs to — it was collected under
a ticker, and that ticker resolves to exactly one ``CD_CVM``. So moving the read
key (ADR 0030) is a relabelling, not a re-download: the ~10k documents in the
mirror keep their bytes and gain the field the readers now filter on.

Deliberate, like ``prune``: a maintenance command, never a side effect of
``ingest``. Idempotent — a document that already names its registrant is not
touched, so running it twice changes nothing, and running it after a partial run
finishes the job.
"""

from __future__ import annotations

from dataclasses import dataclass

from smaug.ingestion.domain.repositories import RawIngestionRepository
from smaug.portfolio.domain.company import RegistrantResolver
from smaug.shared.logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class RelinkReport:
    """What the relink touched, and what it could not name."""

    linked: dict[str, int]  # ticker -> documents stamped
    unresolved: tuple[str, ...]  # tickers no registry could resolve

    @property
    def documents(self) -> int:
        return sum(self.linked.values())


class RelinkMirrorUseCase:
    """Fill in the registrant of every CVM document that lacks one."""

    def __init__(
        self,
        repository: RawIngestionRepository,
        *,
        registrant_resolver: RegistrantResolver,
    ) -> None:
        self._repository = repository
        self._registrant = registrant_resolver

    async def execute(self) -> RelinkReport:
        linked: dict[str, int] = {}
        unresolved: list[str] = []
        for ticker in await self._repository.unlinked_tickers():
            cvm_code = self._registrant(ticker)
            if cvm_code is None:
                # Loud, not silent: a ticker nothing resolves keeps the ticker key
                # and stays readable, but it will not join its company's mirror.
                logger.warning("No CVM registrant resolves %s; left unlinked", ticker)
                unresolved.append(ticker)
                continue
            count = await self._repository.link_registrant(ticker, cvm_code)
            if count:
                linked[ticker] = count
        return RelinkReport(linked=linked, unresolved=tuple(unresolved))
