"""Domain ports for the ingestion context.

Interfaces the application depends on so it never imports infrastructure
directly (plan §3.1). CVM's archives and B3's endpoints implement
``RawDataSource``; tests can substitute a fake without touching the network.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any, Protocol, runtime_checkable

from smaug.ingestion.domain.runs import ParserIdentity
from smaug.shared.artifacts import SourceArtifact


@dataclass(frozen=True, slots=True)
class B3TapeObservation:
    """One COTAHIST session carrying complete security identity evidence."""

    session: date
    isin: str
    especi: str
    bdi: str
    name: str
    code: str = ""


class B3TapeEvidenceReader(Protocol):
    """Read identity-bearing sessions from B3's COTAHIST series."""

    async def at(self, ticker: str, session: date) -> B3TapeObservation | None:
        """Return the identity in force on or before an event session."""
        ...

    async def latest_before(
        self, ticker: str, session: date
    ) -> B3TapeObservation | None:
        """Return the last identity-bearing session before a boundary."""
        ...

    async def by_identity(
        self, session: date, *, isin: str, security_class: str
    ) -> B3TapeObservation | None:
        """Find a legacy code carrying one security identity on or before a date."""
        ...


@dataclass(frozen=True)
class RawFetchResult:
    """Raw, uninterpreted result of one source call (no infra types)."""

    module: str
    source: str
    request: Mapping[str, Any]
    http_status: int
    payload: Mapping[str, Any]
    # The CVM registrant this filing belongs to (``CD_CVM``). A filing is the
    # company's, not the ticker's — ELET3/5/6 are one filer — so it is the key the
    # mirror is read by (ADR 0030). ``None`` when the source names no filer.
    cvm_code: str | None = None
    # Identity of the immutable source archive, when this result came from one.
    artifact_id: str | None = None


class RawDataSource(Protocol):
    """A source that can fetch one module for one ticker.

    A single call may yield *several* filings: a CVM ITR file carries every
    quarter of the year (Q1/Q2/Q3) and B3's dividend table one row per payment,
    so the source returns one result per filing. The sequence is never empty —
    a source that finds nothing raises a ``SourceError`` subclass instead.
    """

    parser_identity: ParserIdentity

    async def fetch(self, ticker: str, module: str) -> Sequence[RawFetchResult]:
        """Return the raw results (one per period), or raise a ``SourceError``."""
        ...


@runtime_checkable
class CacheAwareRawDataSource(Protocol):
    """A source that exposes its in-memory archive-cache scope."""

    def cache_key(self, module: str) -> str | None:
        """Return a key for a reusable source index, if this module has one."""
        ...


@runtime_checkable
class ArtifactDataSource(Protocol):
    """A raw source backed by one immutable archive."""

    async def artifact(self) -> SourceArtifact | None:
        """Acquire or open the exact archive this source will parse."""
        ...
