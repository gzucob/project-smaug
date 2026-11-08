"""Acquire initial public source archives for explicit preparation ingestion."""

from typing import Literal

from smaug.ingestion.domain.source_inputs import SourceInputReader
from smaug.ingestion.infrastructure.source_inputs import LocalSourceInputReader
from smaug.shared.artifacts import SourceArtifactStore


class PublicSourceInputAcquirer:
    def __init__(
        self, store: SourceArtifactStore, *, fca_base_url: str, b3_base_url: str
    ) -> None:
        self._store = store
        self._fca_url = fca_base_url.rstrip("/")
        self._b3_url = b3_base_url.rstrip("/")

    async def acquire(
        self, dataset: Literal["FCA", "COTAHIST"], year: int
    ) -> SourceInputReader:
        url = (
            f"{self._fca_url}/fca_cia_aberta_{year}.zip"
            if dataset == "FCA"
            else f"{self._b3_url}/COTAHIST_A{year}.ZIP"
        )
        artifact = await self._store.acquire(url, follow_redirects=dataset == "FCA")
        return LocalSourceInputReader(artifact.path, artifact=artifact)
