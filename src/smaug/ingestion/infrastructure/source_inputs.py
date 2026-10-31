"""Stream immutable CVM CSV and B3 COTAHIST inputs into MongoDB."""

from __future__ import annotations

import asyncio
import csv
import hashlib
import io
import re
import shutil
import tempfile
import zipfile
from collections.abc import Iterable, Iterator, Mapping
from datetime import UTC, date, datetime
from pathlib import Path
from typing import BinaryIO

from pymongo import UpdateOne

from smaug.ingestion.domain.source_inputs import SourceInputArchive, SourceInputRow
from smaug.ingestion.infrastructure.models import (
    SourceInputArchiveDocument,
    SourceInputRowDocument,
)
from smaug.shared.artifacts import SourceArtifact

_CVM = re.compile(r"(fca|fre|dfp|itr)_cia_aberta(?:_.*)?_(\d{4})\.csv", re.I)
_B3 = re.compile(r"COTAHIST[._]A(\d{4})(?:\.TXT)?", re.I)
_TAPE_FIELDS = {
    "TIPREG": (0, 2),
    "DATA": (2, 10),
    "CODBDI": (10, 12),
    "CODNEG": (12, 24),
    "TPMERC": (24, 27),
    "NOMRES": (27, 39),
    "ESPECI": (39, 49),
    "PREULT": (108, 121),
    "FATCOT": (210, 217),
    "CODISI": (230, 242),
    "DISMES": (242, 245),
}


class LocalSourceInputReader:
    """Read source facts losslessly; never read a derived JSON price cache."""

    def __init__(self, path: Path, *, artifact: SourceArtifact | None = None) -> None:
        self._path = path
        self._artifact = artifact
        self._counts: dict[str, int] = {}
        self._identity: SourceInputArchive | None = None
        self._snapshot: BinaryIO | None = None

    async def inspect(self) -> SourceInputArchive:
        try:
            self._identity = await asyncio.to_thread(self._inspect)
            return self._identity
        except BaseException:
            self.close()
            raise

    def _inspect(self) -> SourceInputArchive:
        self.close()
        # Hash and parse one private disk snapshot. A mutable development ZIP
        # must never leave rows from changed bytes under an earlier artifact id.
        snapshot = tempfile.TemporaryFile(mode="w+b")
        self._snapshot = snapshot
        with self._path.open("rb") as raw:
            shutil.copyfileobj(raw, snapshot, length=1024 * 1024)
        byte_size = snapshot.tell()
        snapshot.seek(0)
        digest = hashlib.file_digest(snapshot, "sha256").hexdigest()
        if self._artifact is not None and digest != self._artifact.sha256:
            raise ValueError("source bytes do not match the acquisition identity")
        snapshot.seek(0)
        with zipfile.ZipFile(snapshot) as archive:
            names = archive.namelist()
            if len(names) != len(set(names)):
                raise ValueError("duplicate source archive members")
            identities = {
                (match[1].upper(), int(match[2]))
                for name in names
                if (match := _CVM.fullmatch(name))
            }
            tape = [(name, _B3.fullmatch(name)) for name in names]
            identities.update(("COTAHIST", int(match[1])) for _, match in tape if match)
            if len(identities) != 1:
                raise ValueError(f"unsupported or mixed source archive: {self._path}")
            dataset, year = identities.pop()
            if dataset == "COTAHIST" and sum(bool(m) for _, m in tape) != 1:
                raise ValueError("ambiguous COTAHIST members")
            if corrupt := archive.testzip():
                raise ValueError(f"corrupt source member: {corrupt}")
        source_url = (
            f"https://bvmf.bmfbovespa.com.br/InstDados/SerHist/COTAHIST_A{year}.ZIP"
            if dataset == "COTAHIST"
            else f"https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/{dataset}/DADOS/"
            f"{dataset.lower()}_cia_aberta_{year}.zip"
        )
        return SourceInputArchive(
            artifact_id=f"sha256:{digest}",
            sha256=digest,
            byte_size=byte_size,
            source_url=(self._artifact.source_url or source_url)
            if self._artifact
            else source_url,
            dataset=dataset,
            year=year,
            observed_at=datetime.now(UTC),
            acquisition="artifact_store" if self._artifact else "local_import",
            downloaded_at=self._artifact.downloaded_at if self._artifact else None,
            etag=self._artifact.etag if self._artifact else None,
            last_modified=self._artifact.last_modified if self._artifact else None,
        )

    def rows(self) -> Iterator[SourceInputRow]:
        if self._identity is None or self._snapshot is None:
            raise ValueError("inspect the source before reading rows")
        self._counts.clear()
        self._snapshot.seek(0)
        with self._snapshot as snapshot, zipfile.ZipFile(snapshot) as archive:
            for member in archive.namelist():
                if not (_CVM.fullmatch(member) or _B3.fullmatch(member)):
                    continue
                self._counts[member] = 0
                with archive.open(member) as raw:
                    stream = io.TextIOWrapper(raw, encoding="latin-1", newline="")
                    if _CVM.fullmatch(member):
                        values = self._csv_rows(stream)
                    else:
                        values = (
                            (fields, (), ())
                            for fields in self._tape_rows(stream, self._identity.year)
                        )
                    for ordinal, (fields, columns, cells) in enumerate(values, 1):
                        self._counts[member] = ordinal
                        yield SourceInputRow(member, ordinal, fields, columns, cells)

    def close(self) -> None:
        if self._snapshot is not None:
            self._snapshot.close()
            self._snapshot = None

    @staticmethod
    def _csv_rows(
        stream: io.TextIOBase,
    ) -> Iterator[tuple[dict[str, str], tuple[str, ...], tuple[str, ...]]]:
        reader = csv.reader(stream, delimiter=";")
        headers = next(reader, [])
        if not headers or len(headers) != len(set(headers)):
            raise ValueError("missing or duplicate CVM columns")
        for row in reader:
            # CVM publishes ragged rows too. Keep their exact cells, distinguishing
            # absent columns from declared empty strings and retaining extra cells.
            yield dict(zip(headers, row, strict=False)), tuple(headers), tuple(row)

    @staticmethod
    def _tape_rows(stream: io.TextIOBase, year: int) -> Iterator[dict[str, str]]:
        header_seen = False
        trailer_seen = False
        for count, line in enumerate(stream, 1):
            record = line.rstrip("\r\n")
            if len(record) != 245 or record[:2] not in {"00", "01", "99"}:
                raise ValueError("invalid COTAHIST record")
            kind = record[:2]
            if trailer_seen or (not header_seen and kind != "00"):
                raise ValueError("invalid COTAHIST record order")
            if kind == "00":
                if header_seen or not record.startswith(f"00COTAHIST.{year}"):
                    raise ValueError("invalid COTAHIST header")
                header_seen = True
            if kind == "99":
                if not record.startswith(f"99COTAHIST.{year}"):
                    raise ValueError("invalid COTAHIST trailer")
                if not record[31:42].isdigit() or int(record[31:42]) != count:
                    raise ValueError("invalid COTAHIST record count")
                trailer_seen = True
            fields = {
                key: record[start:end] for key, (start, end) in _TAPE_FIELDS.items()
            }
            if fields["TIPREG"] == "01":
                if date.fromisoformat(fields["DATA"]).year != year:
                    raise ValueError("invalid COTAHIST session year")
                if not fields["PREULT"].isdigit() or not fields["FATCOT"].isdigit():
                    raise ValueError("invalid COTAHIST price or quote factor")
                if int(fields["FATCOT"]) == 0:
                    raise ValueError("zero COTAHIST quote factor")
            # Preserve all 245 columns, including header/trailer and non-spot rows.
            yield {**fields, "record": record}
        if not header_seen or not trailer_seen:
            raise ValueError("incomplete COTAHIST archive")

    def member_counts(self) -> Mapping[str, int]:
        return dict(self._counts)


class MongoSourceInputRepository:
    """Version rows by source bytes, parser, member and physical ordinal."""

    @staticmethod
    def _key(archive: SourceInputArchive) -> dict[str, str]:
        return {
            "artifact_id": archive.artifact_id,
            "parser_version": archive.parser_version,
        }

    async def completed(self, archive: SourceInputArchive) -> bool:
        return await SourceInputArchiveDocument.find_one(self._key(archive)) is not None

    async def write_rows(
        self, archive: SourceInputArchive, rows: Iterable[SourceInputRow]
    ) -> None:
        collection = SourceInputRowDocument.get_pymongo_collection()
        pending = []
        for row in rows:
            key = {**self._key(archive), "member": row.member, "ordinal": row.ordinal}
            pending.append(
                UpdateOne(
                    key,
                    {
                        "$setOnInsert": {
                            **key,
                            "dataset": archive.dataset,
                            "year": archive.year,
                            "fields": dict(row.fields),
                            "columns": list(row.columns),
                            "cells": list(row.cells),
                            "column_count_mismatch": len(row.columns) != len(row.cells),
                            "code": row.fields.get(
                                "CODNEG", row.fields.get("Codigo_Negociacao", "")
                            ).strip(),
                            "session": row.fields.get("DATA"),
                            "cnpj": row.fields.get(
                                "CNPJ_Companhia", row.fields.get("CNPJ_CIA")
                            ),
                        }
                    },
                    upsert=True,
                )
            )
            if len(pending) == 1000:
                await collection.bulk_write(pending, ordered=False)
                pending.clear()
        if pending:
            await collection.bulk_write(pending, ordered=False)

    async def complete(
        self, archive: SourceInputArchive, member_counts: Mapping[str, int]
    ) -> None:
        await SourceInputArchiveDocument.get_pymongo_collection().update_one(
            self._key(archive),
            {
                "$setOnInsert": {
                    **self._key(archive),
                    "sha256": archive.sha256,
                    "byte_size": archive.byte_size,
                    "source_url": archive.source_url,
                    "dataset": archive.dataset,
                    "year": archive.year,
                    "observed_at": archive.observed_at,
                    "acquisition": archive.acquisition,
                    "downloaded_at": archive.downloaded_at,
                    "etag": archive.etag,
                    "last_modified": archive.last_modified,
                    "member_counts": dict(member_counts),
                    "validation": "zip-crc-and-lossless-row-layout-v1",
                    "price_basis": "as_traded"
                    if archive.dataset == "COTAHIST"
                    else None,
                }
            },
            upsert=True,
        )
