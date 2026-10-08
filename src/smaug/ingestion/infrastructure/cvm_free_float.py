"""Mirror filed CVM FRE capital-distribution rows and their class breakdowns."""

from __future__ import annotations

import asyncio
import csv
import io
import zipfile
from collections.abc import Mapping, Sequence
from pathlib import Path
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
from smaug.shared.errors import SourceNotFoundError

FREE_FLOAT_MODULE = "FREE_FLOAT"


class CvmFreeFloatSource:
    """Read the FRE distribution table without selecting or calculating values."""

    source = "cvm"
    parser_identity = ParserIdentity("cvm.free-float.csv", 1)

    def __init__(
        self,
        archive: CvmCapitalSource,
        ticker_to_cnpj: Mapping[str, str],
        ticker_to_code: Mapping[str, str],
        *,
        year: int,
        validation_reporter: BatchValidationReporter | None = None,
    ) -> None:
        self._archive = archive
        self._cnpj = dict(ticker_to_cnpj)
        self._codes = dict(ticker_to_code)
        self._year = year
        self._reporter = validation_reporter
        self._index: dict[str, list[dict[str, Any]]] | None = None
        self._lock = asyncio.Lock()

    async def artifact(self) -> SourceArtifact | None:
        """Return the immutable FRE archive used by this parser."""
        return await self._archive.artifact()

    async def fetch(self, ticker: str, module: str) -> Sequence[RawFetchResult]:
        if self._index is None:
            async with self._lock:
                if self._index is None:
                    path = await self._archive.archive_path()
                    member = f"fre_cia_aberta_distribuicao_capital_{self._year}.csv"
                    with zipfile.ZipFile(path) as archive:
                        if member not in archive.namelist():
                            raise SourceNotFoundError(
                                f"CVM FRE {self._year} has no distribution table"
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
                                        "Nome_Companhia",
                                        "Percentual_Total_Acoes_Circulacao",
                                        "Data_Ultima_Assembleia",
                                    }
                                ),
                                "CNPJ_Companhia",
                                "Data_Referencia",
                            ),
                        ),
                        require_member=True,
                    )
                    await record_or_quarantine(self._reporter, validation)
                    self._index = await asyncio.to_thread(self._read, path, member)
        rows = self._index.get(self._cnpj.get(ticker, ""), [])
        if not rows:
            raise SourceNotFoundError(f"no filed capital distribution for {ticker}")
        artifact = await self.artifact()
        return [
            RawFetchResult(
                module=module,
                source="cvm",
                http_status=200,
                cvm_code=self._codes.get(ticker),
                artifact_id=artifact.artifact_id if artifact is not None else None,
                request={
                    "source": "cvm",
                    "file": self._archive.archive_name,
                    "statement": module,
                    "cnpj": row["cnpj"],
                    "reference_date": row["reference_date"],
                    "version": row["version"],
                    "document_id": row["document_id"],
                },
                payload=row,
            )
            for row in rows
        ]

    def _read(self, path: Path, member: str) -> dict[str, list[dict[str, Any]]]:
        wanted = set(self._cnpj.values())
        result: dict[str, list[dict[str, Any]]] = {}
        with zipfile.ZipFile(path) as archive:
            classes: dict[tuple[str, str, str, str], list[dict[str, str]]] = {}
            class_member = (
                f"fre_cia_aberta_distribuicao_capital_classe_acao_{self._year}.csv"
            )
            if class_member in archive.namelist():
                with archive.open(class_member) as raw:
                    for row in csv.DictReader(
                        io.TextIOWrapper(raw, encoding="latin-1"), delimiter=";"
                    ):
                        if row["CNPJ_Companhia"] in wanted:
                            classes.setdefault(_key(row), []).append(dict(row))
            with archive.open(member) as raw:
                for row in csv.DictReader(
                    io.TextIOWrapper(raw, encoding="latin-1"), delimiter=";"
                ):
                    if row["CNPJ_Companhia"] not in wanted:
                        continue
                    result.setdefault(row["CNPJ_Companhia"], []).append(
                        {
                            "cnpj": row["CNPJ_Companhia"],
                            "company_name": row["Nome_Companhia"],
                            "reference_date": row["Data_Referencia"],
                            "version": int(row["Versao"]),
                            "document_id": row["ID_Documento"],
                            "assembly_date": row["Data_Ultima_Assembleia"],
                            "company_free_float_percent": row[
                                "Percentual_Total_Acoes_Circulacao"
                            ],
                            "filed_fields": dict(row),
                            "share_classes": classes.get(_key(row), []),
                        }
                    )
        return result


def _key(row: Mapping[str, str]) -> tuple[str, str, str, str]:
    return (
        row["CNPJ_Companhia"],
        row["Data_Referencia"],
        row["Versao"],
        row["ID_Documento"],
    )
