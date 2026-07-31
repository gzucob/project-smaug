"""Routes each module to the source that can answer it.

CVM splits what we need across two datasets: the statements (BPA/BPP/DRE/DFC)
live in the DFP/ITR file, the share counts (CAPITAL) live in the FRE file. Both
implement ``RawDataSource``, so instead of teaching one class about two archives
this router dispatches by module and keeps the use case none the wiser.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from smaug.ingestion.domain.ports import RawDataSource, RawFetchResult


class RoutedDataSource:
    """Dispatch ``fetch`` to a per-module source, falling back to ``default``."""

    def __init__(
        self, routes: Mapping[str, RawDataSource], *, default: RawDataSource
    ) -> None:
        self._routes = dict(routes)
        self._default = default

    async def fetch(self, ticker: str, module: str) -> Sequence[RawFetchResult]:
        source = self._routes.get(module.upper(), self._default)
        return await source.fetch(ticker, module)

    @property
    def archive_name(self) -> str | None:
        """The statements archive this run reads — the batch's resume marker.

        The statements file, not the FRE, because it is the one every module but
        the share counts comes from: a company found in it has been collected for
        this year. A run that wants the FRE re-read regardless uses ``--force``.

        ``None`` when the routed default is not archive-backed (brapi), which is
        the honest answer to "which file is this run finished with".
        """
        archive = getattr(self._default, "archive_name", None)
        return archive if isinstance(archive, str) else None
