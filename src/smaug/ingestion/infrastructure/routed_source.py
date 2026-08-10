"""Routes each module to the source that can answer it.

CVM splits what we need across two datasets: the statements (BPA/BPP/DRE/DFC)
live in the DFP/ITR file, the share counts (CAPITAL) live in the FRE file. Both
implement ``RawDataSource``, so instead of teaching one class about two archives
this router dispatches by module and keeps the use case none the wiser.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from smaug.ingestion.domain.ports import (
    ArtifactDataSource,
    RawDataSource,
    RawFetchResult,
)
from smaug.ingestion.domain.runs import ParserIdentity
from smaug.shared.artifacts import SourceArtifact


class RoutedDataSource:
    """Dispatch ``fetch`` to a per-module source, falling back to ``default``."""

    parser_identity = ParserIdentity("smaug.routed-source", 1)

    def __init__(
        self, routes: Mapping[str, RawDataSource], *, default: RawDataSource
    ) -> None:
        self._routes = dict(routes)
        self._default = default

    async def fetch(self, ticker: str, module: str) -> Sequence[RawFetchResult]:
        source = self._routes.get(module.upper(), self._default)
        return await source.fetch(ticker, module)

    def cache_key(self, module: str) -> str | None:
        """Name the source-local index shared by calls for this module."""
        source = self._routes.get(module.upper(), self._default)
        if not isinstance(source, ArtifactDataSource):
            return None
        return f"{source.parser_identity.name}:{id(source)}"

    async def artifact_for(self, module: str) -> SourceArtifact | None:
        """Acquire the exact archive used by the source answering ``module``."""
        source = self._routes.get(module.upper(), self._default)
        if not isinstance(source, ArtifactDataSource):
            return None
        return await source.artifact()

    def parser_identities(self, modules: Sequence[str]) -> tuple[ParserIdentity, ...]:
        """Stable identities of the parsers selected for these modules."""
        identities = (
            self._routes.get(module.upper(), self._default).parser_identity
            for module in modules
        )
        return tuple(dict.fromkeys(identities))
