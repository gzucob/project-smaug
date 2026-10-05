"""Mirror the FRE's historical rights table without interpreting percentages."""

from __future__ import annotations

import asyncio
import csv
import io
import zipfile
from collections.abc import Mapping, Sequence
from typing import Any

from smaug.ingestion.domain.ports import RawFetchResult
from smaug.ingestion.domain.runs import ParserIdentity
from smaug.ingestion.domain.validation import BatchValidationReporter
from smaug.ingestion.infrastructure.batch_validation import (
    CsvMemberSpec,
    record_or_quarantine,
    validate_csv_archive,
)
from smaug.ingestion.infrastructure.cvm_capital import CvmCapitalSource
from smaug.shared.artifacts import SourceArtifact
from smaug.shared.errors import SourceMalformedError, SourceNotFoundError

TAG_ALONG_MODULE = "TAG_ALONG"


class CvmTagAlongSource:
    """Read each species/class's declared tag along from the source archive."""

    source = "cvm"
    parser_identity = ParserIdentity("cvm.tag-along.csv", 1)

    def __init__(
        self,
        archive: CvmCapitalSource,
        ticker_to_cnpj: Mapping[str, str],
        ticker_to_code: Mapping[str, str],
        *,
        year: int,
        validation_reporter: BatchValidationReporter | None = None,
    ) -> None:
        self._reporter = validation_reporter
        self._archive = archive
        self._cnpj = ticker_to_cnpj
        self._codes = ticker_to_code
        self._year = year
        self._rows: dict[str, list[dict[str, Any]]] | None = None
        self._lock = asyncio.Lock()

    async def artifact(self) -> SourceArtifact | None:
        """Return the immutable FRE archive behind these rights."""
        return await self._archive.artifact()

    async def fetch(self, ticker: str, module: str) -> Sequence[RawFetchResult]:
        if self._rows is None:
            async with self._lock:
                if self._rows is None:
                    path = await self._archive.archive_path()
                    member = f"fre_cia_aberta_direito_acao_{self._year}.csv"
                    with zipfile.ZipFile(path) as archive:
                        if member not in archive.namelist():
                            raise SourceNotFoundError(
                                f"CVM FRE {self._year} has no rights table"
                            )
                    validation = await asyncio.to_thread(
                        validate_csv_archive,
                        path,
                        source="cvm",
                        batch=self._archive.archive_name,
                        parser=self.parser_identity,
                        artifact=await self.artifact(),
                        expected_year=self._year,
                        members=(
                            CsvMemberSpec(
                                member,
                                frozenset(
                                    {
                                        "CNPJ_Companhia",
                                        "Data_Referencia",
                                        "Versao",
                                        "ID_Documento",
                                        "Especie_Acao",
                                        "Classe_Acao_Preferencial",
                                        "Percentual_Tag_Along",
                                    }
                                ),
                                "CNPJ_Companhia",
                                "Data_Referencia",
                            ),
                        ),
                    )
                    await record_or_quarantine(self._reporter, validation)
                    with zipfile.ZipFile(path) as archive:
                        filing_member = f"fre_cia_aberta_{self._year}.csv"
                        if filing_member not in archive.namelist():
                            raise SourceNotFoundError(
                                f"CVM FRE {self._year} lacks filing dates"
                            )
                        with archive.open(filing_member) as raw:
                            reader = csv.DictReader(
                                io.TextIOWrapper(raw, encoding="latin-1"), delimiter=";"
                            )
                            required = {
                                "CNPJ_CIA",
                                "DT_REFER",
                                "VERSAO",
                                "ID_DOC",
                                "DT_RECEB",
                            }
                            if not required.issubset(reader.fieldnames or []):
                                raise SourceMalformedError(
                                    "FRE filing metadata lacks required columns"
                                )
                            filings = {
                                (
                                    row["CNPJ_CIA"],
                                    row["DT_REFER"],
                                    row["VERSAO"],
                                    row["ID_DOC"],
                                ): dict(row)
                                for row in reader
                            }
                        with archive.open(member) as raw:
                            reader = csv.DictReader(
                                io.TextIOWrapper(raw, encoding="latin-1"), delimiter=";"
                            )
                            rows: dict[str, list[dict[str, Any]]] = {}
                            for row in reader:
                                key = (
                                    row["CNPJ_Companhia"],
                                    row["Data_Referencia"],
                                    row["Versao"],
                                    row["ID_Documento"],
                                )
                                rows.setdefault(row["CNPJ_Companhia"], []).append(
                                    {**row, "filing": filings.get(key)}
                                )
                            self._rows = rows
        company_rows = self._rows.get(self._cnpj.get(ticker, ""), [])
        if not company_rows:
            raise SourceNotFoundError(f"no filed share rights for {ticker}")
        artifact = await self.artifact()
        return [
            RawFetchResult(
                module=module,
                source=self.source,
                http_status=200,
                cvm_code=self._codes.get(ticker),
                artifact_id=artifact.artifact_id if artifact else None,
                request={"file": self._archive.archive_name, **row},
                payload=row,
            )
            for row in company_rows
        ]
