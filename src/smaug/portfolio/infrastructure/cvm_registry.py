"""Ticker -> CVM registrant resolution from CVM's FCA archive.

The statements (``CD_CVM``) and the FRE (``CNPJ``) are both keyed by registrant,
never by the B3 ticker — so before either source can run for an arbitrary ticker,
the ticker has to be resolved to those keys. That link lives in the CVM's
*Formulário Cadastral* (FCA), one yearly ZIP with two members that join on CNPJ:

  * ``fca_cia_aberta_valor_mobiliario`` — the securities each company has listed,
    carrying ``Codigo_Negociacao`` (the B3 ticker) and ``CNPJ_Companhia``.
  * ``fca_cia_aberta_geral`` — the general cadastre, carrying ``Codigo_CVM``,
    ``Setor_Atividade`` and ``Situacao_Registro_CVM``, also keyed by CNPJ.

So ``ticker -> CNPJ`` (securities) joined with ``CNPJ -> CD_CVM`` (general) gives
the full identity — resolved this way for every ticker, no hand-picked shortcut
(#212), and it scales to the whole exchange (the batch-ingestion slice of M2
reuses the same index).

This registry is a current-identity snapshot. Its year is deliberately separate
from the accounting year passed to DFP/ITR/FRE readers; a historical filing run
may therefore keep its verified accounting archive while selecting a newer FCA
universe.

Follows the same download-once / cache / read-in-a-thread shape as
``CvmDataSource``; the FCA CSVs are latin-1, semicolon-separated like every CVM
open dataset.
"""

from __future__ import annotations

import asyncio
import csv
import io
import zipfile
from collections.abc import Awaitable, Callable
from pathlib import Path

import httpx

from smaug.portfolio.domain.company import (
    CompanyIdentity,
)
from smaug.portfolio.domain.fca_placeholders import (
    FcaPlaceholderReport,
    FcaPlaceholderRow,
    FcaRecoveryResult,
)
from smaug.portfolio.domain.provenance import FCA_SOURCE, FcaSnapshotProvenance
from smaug.portfolio.infrastructure.fca_registry import (
    FcaCompanyRegistry,
    _unresolved_finding,
)
from smaug.shared.artifacts import SourceArtifact, SourceArtifactStore
from smaug.shared.download import Sleeper, download_zip
from smaug.shared.logging import get_logger

logger = get_logger(__name__)

PlaceholderRecoverer = Callable[
    [tuple[FcaPlaceholderRow, ...]], Awaitable[FcaRecoveryResult]
]

CVM_FCA_BASE_URL = "https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/FCA/DADOS"

_ENCODING = "latin-1"
_DELIMITER = ";"


class CvmCompanyRegistry(FcaCompanyRegistry):
    def __init__(
        self,
        http_client: httpx.AsyncClient,
        *,
        year: int,
        cache_dir: str,
        base_url: str | None = None,
        sleep: Sleeper = asyncio.sleep,
        artifact_store: SourceArtifactStore | None = None,
        artifact_id: str | None = None,
        placeholder_recoverer: PlaceholderRecoverer | None = None,
    ) -> None:
        self._http = http_client
        self._year = year
        self._cache_dir = Path(cache_dir)
        self._base_url = (base_url or CVM_FCA_BASE_URL).rstrip("/")
        self._sleep = sleep
        self._artifact_store = artifact_store
        self._replay_artifact_id = artifact_id
        self._artifact: SourceArtifact | None = None
        self._index: dict[str, CompanyIdentity] | None = None
        self._placeholder_recoverer = placeholder_recoverer
        self._placeholder_report: FcaPlaceholderReport | None = None
        self._lock = asyncio.Lock()

    async def provenance(self) -> FcaSnapshotProvenance:
        """Return the selected FCA snapshot and its immutable artifact, when any."""
        artifact = await self._ensure_artifact()
        return FcaSnapshotProvenance(
            year=self.snapshot_year,
            source=FCA_SOURCE,
            source_url=self.source_url,
            artifact_id=artifact.artifact_id if artifact is not None else None,
        )

    async def artifact(self) -> SourceArtifact | None:
        """Acquire the FCA identity without parsing its members."""
        return await self._ensure_artifact()

    async def _ensure_loaded(self) -> dict[str, CompanyIdentity]:
        cached = self._index
        if cached is not None:
            return cached
        async with self._lock:
            cached = self._index
            if cached is not None:
                return cached
            raw = await self._archive_path()
            index = await asyncio.to_thread(self._build_index, raw)
            with zipfile.ZipFile(raw) as archive:
                placeholder_rows = self._read_placeholder_rows(self._rows(archive))
            if self._placeholder_recoverer is None:
                result = FcaRecoveryResult(
                    report=FcaPlaceholderReport(
                        snapshot_year=self._year,
                        findings=tuple(
                            _unresolved_finding(row, "recovery-not-configured")
                            for row in placeholder_rows
                        ),
                    )
                )
            else:
                result = await self._placeholder_recoverer(placeholder_rows)
            index, report = self._merge_placeholder_result(
                index, result, placeholder_rows=placeholder_rows
            )
            self._placeholder_report = report
            self._index = index
            logger.info(
                "Loaded CVM FCA %s registry: %d coded securities",
                self._year,
                len(index),
            )
            return index

    async def _archive_path(self) -> Path:
        artifact = await self._ensure_artifact()
        if artifact is not None:
            return artifact.path
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        raw = self._cache_dir / self._zip_name
        if not raw.exists():
            await self._download(raw)
        return raw

    async def _ensure_artifact(self) -> SourceArtifact | None:
        if self._artifact_store is None:
            return None
        if self._artifact is None:
            if self._replay_artifact_id is not None:
                self._artifact = await self._artifact_store.open(
                    self._replay_artifact_id
                )
            else:
                self._artifact = await self._artifact_store.acquire(
                    f"{self._base_url}/{self._zip_name}", follow_redirects=True
                )
        return self._artifact

    async def _download(self, dst: Path) -> None:
        url = f"{self._base_url}/{self._zip_name}"
        logger.info("Downloading CVM FCA %s from %s", self._year, url)
        await download_zip(
            self._http, url, dst, follow_redirects=True, sleep=self._sleep
        )

    def _rows(self, archive: zipfile.ZipFile) -> dict[str, list[dict[str, str]]]:
        members = {}
        for name in (self._geral_member, self._securities_member):
            reader = csv.DictReader(
                io.StringIO(archive.read(name).decode(_ENCODING)), delimiter=_DELIMITER
            )
            members[name] = [
                {**row, "__source_line_number__": str(reader.line_num)}
                for row in reader
            ]
        return members

    def _build_index(self, path: Path) -> dict[str, CompanyIdentity]:
        with zipfile.ZipFile(path) as archive:
            return self._build_rows_index(self._rows(archive))
