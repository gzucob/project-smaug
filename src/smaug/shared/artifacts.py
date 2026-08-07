"""Source-archive identities shared by readers and acquisition adapters."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class SourceArtifact:
    """A validated local copy of immutable source bytes."""

    artifact_id: str
    sha256: str
    byte_size: int
    path: Path
    source_url: str | None = None
    downloaded_at: datetime | None = None
    etag: str | None = None
    last_modified: str | None = None


class SourceArtifactStore(Protocol):
    """Acquire source bytes or materialize a previously stored artifact."""

    async def acquire(
        self, source_url: str, *, follow_redirects: bool = False
    ) -> SourceArtifact:
        """Return the current validated content published at ``source_url``."""
        ...

    async def open(self, artifact_id: str) -> SourceArtifact:
        """Materialize a stored artifact without using the network."""
        ...


ArtifactObserver = Callable[[SourceArtifact], Awaitable[None]]
