"""Resolve FCA identities exclusively from pinned, admitted MongoDB revisions."""

from smaug.portfolio.domain.company import CompanyIdentity
from smaug.portfolio.domain.fca_placeholders import (
    FcaPlaceholderReport,
    FcaRecoveryResult,
)
from smaug.portfolio.domain.provenance import FCA_SOURCE, FcaSnapshotProvenance
from smaug.portfolio.domain.securities import FIRST_FCA_YEAR
from smaug.portfolio.infrastructure.fca_registry import (
    FcaCompanyRegistry,
    _unresolved_finding,
)
from smaug.portfolio.infrastructure.fca_securities import (
    FcaSecurityHistory,
    _build_index,
    _Index,
)
from smaug.shared.errors import SourceNotFoundError
from smaug.shared.source_inputs import MongoSourceInputs


class MongoCompanyRegistry(FcaCompanyRegistry):
    def __init__(self, inputs: MongoSourceInputs, *, year: int) -> None:
        super().__init__(
            year=year,
            source_url=f"https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/FCA/DADOS/fca_cia_aberta_{year}.zip",
        )
        self._inputs = inputs

    async def provenance(self) -> FcaSnapshotProvenance:
        revision = await self._inputs.revision("FCA", self._year)
        return FcaSnapshotProvenance(
            self._year, FCA_SOURCE, revision.source_url, revision.artifact_id
        )

    async def _ensure_loaded(self) -> dict[str, CompanyIdentity]:
        async with self._lock:
            if self._index is not None:
                return self._index
            revision = await self._inputs.revision("FCA", self._year)
            rows = await self._inputs.fca(revision)
            if self._geral_member not in rows or self._securities_member not in rows:
                raise SourceNotFoundError(f"missing_mirrored_identity:FCA:{self._year}")
            index = self._build_rows_index(rows)
            placeholders = self._read_placeholder_rows(rows)
            result = (
                await self._placeholder_recoverer(placeholders)
                if self._placeholder_recoverer is not None
                else FcaRecoveryResult(
                    report=FcaPlaceholderReport(
                        snapshot_year=self._year,
                        findings=tuple(
                            _unresolved_finding(row, "recovery-not-configured")
                            for row in placeholders
                        ),
                    )
                )
            )
            self._index, self._placeholder_report = self._merge_placeholder_result(
                index, result, placeholder_rows=placeholders
            )
            return self._index


class MongoSecurityHistory(FcaSecurityHistory):
    def __init__(
        self, inputs: MongoSourceInputs, *, through: int, since: int = FIRST_FCA_YEAR
    ) -> None:
        super().__init__()
        self._inputs = inputs
        self._years = range(since, through + 1)

    async def _ensure_loaded(self) -> _Index:
        async with self._lock:
            if self._index is None:
                revisions = await self._inputs.pin()
                archives = []
                for year in self._years:
                    revision = revisions.get(("FCA", year))
                    if revision is None:
                        raise SourceNotFoundError(
                            f"missing_mirrored_security_history:FCA:{year}"
                        )
                    rows = await self._inputs.fca(revision)
                    required = [f"fca_cia_aberta_geral_{year}.csv"]
                    if year >= 2018:
                        required.append(f"fca_cia_aberta_valor_mobiliario_{year}.csv")
                    if any(member not in rows for member in required):
                        raise SourceNotFoundError(
                            f"missing_mirrored_security_history_member:FCA:{year}"
                        )
                    archives.append((year, rows))
                if not archives:
                    raise SourceNotFoundError("missing_mirrored_security_history:FCA")
                self._index = _build_index(archives)
            return self._index
