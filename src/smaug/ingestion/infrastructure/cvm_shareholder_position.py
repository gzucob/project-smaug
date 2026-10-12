"""Mirror direct and related shareholder positions filed in the CVM FRE."""

from __future__ import annotations

import asyncio
import csv
import io
import zipfile
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import replace
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

SHAREHOLDER_POSITION_MODULE = "SHAREHOLDER_POSITION"

_MAIN_COLUMNS = frozenset(
    {
        "CNPJ_Companhia",
        "Nome_Companhia",
        "Data_Referencia",
        "Versao",
        "ID_Documento",
        "ID_Acionista",
        "Acionista",
        "Tipo_Pessoa_Acionista",
        "ID_Acionista_Relacionado",
        "Acionista_Relacionado",
        "Tipo_Pessoa_Acionista_Relacionado",
        "Quantidade_Acao_Ordinaria_Circulacao",
        "Percentual_Acao_Ordinaria_Circulacao",
        "Quantidade_Acao_Preferencial_Circulacao",
        "Percentual_Acao_Preferencial_Circulacao",
        "Quantidade_Total_Acoes_Circulacao",
        "Percentual_Total_Acoes_Circulacao",
        "Acionista_Controlador",
        "Data_Ultima_Alteracao",
    }
)
_CLASS_COLUMNS = frozenset(
    {
        "CNPJ_Companhia",
        "Data_Referencia",
        "Versao",
        "ID_Documento",
        "ID_Acionista",
        "Tipo_Classe_Acao_Preferencial",
        "Quantidade_Acoes",
        "Percentual_Acoes",
    }
)


class CvmShareholderPositionSource:
    """Read the CVM FRE position tables and preserve each filed shareholder row."""

    source = "cvm"
    parser_identity = ParserIdentity("cvm.shareholder-position.csv", 1)

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
                    await self._load()
        cnpj = self._cnpj.get(ticker)
        if cnpj is None:
            raise SourceNotFoundError(f"no CNPJ mapped for {ticker}")
        rows = self._index.get(cnpj, []) if self._index is not None else []
        if not rows:
            raise SourceNotFoundError(
                f"no CVM {self._year} FRE shareholder position for {ticker} ({cnpj})"
            )

        artifact = await self.artifact()
        results: list[RawFetchResult] = []
        for row in rows:
            filed = row["filed_fields"]
            shareholder_id = str(filed.get("ID_Acionista", "")).strip()
            request: dict[str, Any] = {
                "source": "cvm",
                "file": self._archive.archive_name,
                "statement": module,
                "cnpj": cnpj,
                "reference_date": row["reference_date"],
                "version": row["version"],
                "document_id": row["document_id"],
                "shareholder_id": shareholder_id or None,
                "related_shareholder_id": row["related_shareholder_id"],
                # A filing can contain multiple rows for one shareholder, such
                # as separate links in a control chain. Keep each source row's
                # identity so the append-only mirror does not collapse them.
                "source_row": row["source_row"],
            }
            results.append(
                RawFetchResult(
                    module=module,
                    source=self.source,
                    request=request,
                    http_status=200,
                    payload=row,
                    cvm_code=self._codes.get(ticker),
                    artifact_id=(
                        artifact.artifact_id if artifact is not None else None
                    ),
                )
            )
        return results

    async def _load(self) -> None:
        path = await self._archive.archive_path()
        validation = await asyncio.to_thread(
            validate_csv_archive,
            path,
            source=self.source,
            batch=self._archive.archive_name,
            parser=self.parser_identity,
            artifact=await self.artifact(),
            expected_year=self._year,
            members=(
                CsvMemberSpec(
                    self._member_name,
                    _MAIN_COLUMNS,
                    "CNPJ_Companhia",
                    "Data_Referencia",
                ),
                CsvMemberSpec(
                    self._class_member_name,
                    _CLASS_COLUMNS,
                    "CNPJ_Companhia",
                    "Data_Referencia",
                ),
            ),
            require_member=True,
        )
        await record_or_quarantine(
            self._reporter,
            replace(validation, module=SHAREHOLDER_POSITION_MODULE),
        )
        self._index = await asyncio.to_thread(self._read, path)

    @property
    def _member_name(self) -> str:
        return f"fre_cia_aberta_posicao_acionaria_{self._year}.csv"

    @property
    def _class_member_name(self) -> str:
        return f"fre_cia_aberta_posicao_acionaria_classe_acao_{self._year}.csv"

    def _read(self, path: Path) -> dict[str, list[dict[str, Any]]]:
        wanted = set(self._cnpj.values())
        result: dict[str, list[dict[str, Any]]] = defaultdict(list)
        class_rows: dict[tuple[str, str, str, str, str], list[dict[str, str]]] = (
            defaultdict(list)
        )
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
            if self._class_member_name in names:
                with archive.open(self._class_member_name) as raw:
                    reader = csv.DictReader(
                        io.TextIOWrapper(raw, encoding="latin-1"), delimiter=";"
                    )
                    for row in reader:
                        if row["CNPJ_Companhia"] in wanted:
                            class_rows[_position_key(row)].append(dict(row))

            if self._member_name not in names:
                return {}
            with archive.open(self._member_name) as raw:
                reader = csv.DictReader(
                    io.TextIOWrapper(raw, encoding="latin-1"), delimiter=";"
                )
                for source_row, row in enumerate(reader, start=1):
                    cnpj = row["CNPJ_Companhia"]
                    if cnpj not in wanted:
                        continue
                    key = _position_key(row)
                    result[cnpj].append(
                        {
                            "cnpj": cnpj,
                            "company_name": row["Nome_Companhia"],
                            "reference_date": row["Data_Referencia"],
                            "version": int(row["Versao"]),
                            "document_id": row["ID_Documento"],
                            "archive_year": self._year,
                            "shareholder_id": row["ID_Acionista"],
                            "related_shareholder_id": (
                                row["ID_Acionista_Relacionado"] or None
                            ),
                            "source_row": source_row,
                            "filed_fields": dict(row),
                            "share_class_positions": class_rows.get(key, []),
                        }
                    )
        return dict(result)


def _position_key(row: Mapping[str, str]) -> tuple[str, str, str, str, str]:
    return (
        row["CNPJ_Companhia"],
        row["Data_Referencia"],
        row["Versao"],
        row["ID_Documento"],
        row["ID_Acionista"],
    )
