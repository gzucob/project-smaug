"""Read the CVM archive catalog before acquiring changed financial statements."""

import asyncio
import csv
import io
import zipfile
from collections.abc import Awaitable, Callable
from datetime import date

import httpx

from smaug.ingestion.domain.disclosures import (
    ArchiveRevision,
    DisclosureArchive,
    DisclosureBatch,
    DisclosureEdition,
    DisclosureIssuer,
    DisclosurePeriod,
    response_version,
)
from smaug.ingestion.domain.ports import RawDataSource, RawFetchResult
from smaug.ingestion.domain.source_inputs import SourceInputReader
from smaug.ingestion.domain.validation import BatchValidationReporter
from smaug.ingestion.infrastructure.archive_catalog import CVM_DOCUMENT_ROOT
from smaug.ingestion.infrastructure.cvm_capital import (
    CAPITAL_EVENT_MODULE,
    CAPITAL_MODULE,
    TREASURY_MODULE,
    CvmCapitalEventSource,
    CvmCapitalSource,
    CvmTreasurySource,
)
from smaug.ingestion.infrastructure.cvm_free_float import (
    FREE_FLOAT_MODULE,
    CvmFreeFloatSource,
)
from smaug.ingestion.infrastructure.cvm_shareholder_position import (
    SHAREHOLDER_POSITION_MODULE,
    CvmShareholderPositionSource,
)
from smaug.ingestion.infrastructure.cvm_source import STATEMENT_MODULES, CvmDataSource
from smaug.ingestion.infrastructure.cvm_tag_along import (
    TAG_ALONG_MODULE,
    CvmTagAlongSource,
)
from smaug.ingestion.infrastructure.source_inputs import LocalSourceInputReader
from smaug.shared.artifacts import SourceArtifact, SourceArtifactStore
from smaug.shared.errors import SourceMalformedError, SourceNotFoundError
from smaug.shared.source_inputs import MongoSourceInputs

_RANGE_BLOCK = 128 * 1024


class _ArchiveRanges(io.RawIOBase):
    """Provide the ZIP directory and catalog to zipfile using bounded range reads."""

    def __init__(
        self, http: httpx.Client, url: str, length: int, revision: ArchiveRevision
    ) -> None:
        self._http = http
        self._url = url
        self._length = length
        self._revision = revision
        self._offset = 0
        self._block_start = -1
        self._block = b""

    def seekable(self) -> bool:
        return True

    def readable(self) -> bool:
        return True

    def tell(self) -> int:
        return self._offset

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        base = {io.SEEK_SET: 0, io.SEEK_CUR: self._offset, io.SEEK_END: self._length}
        self._offset = base[whence] + offset
        if self._offset < 0:
            raise OSError("negative ZIP offset")
        return self._offset

    def read(self, size: int = -1) -> bytes:
        remaining = max(0, self._length - self._offset)
        end = self._offset + (remaining if size < 0 else min(size, remaining))
        result = bytearray()
        while self._offset < end:
            start = self._offset // _RANGE_BLOCK * _RANGE_BLOCK
            if start != self._block_start:
                self._block = self._fetch(start)
                self._block_start = start
            offset = self._offset - start
            chunk = self._block[offset : offset + end - self._offset]
            result.extend(chunk)
            self._offset += len(chunk)
        return bytes(result)

    def _fetch(self, start: int) -> bytes:
        end = min(start + _RANGE_BLOCK, self._length) - 1
        headers = {"Range": f"bytes={start}-{end}", "Accept-Encoding": "identity"}
        if self._revision.etag and not self._revision.etag.startswith("W/"):
            headers["If-Match"] = self._revision.etag
        elif self._revision.last_modified:
            headers["If-Unmodified-Since"] = self._revision.last_modified
        with self._http.stream("GET", self._url, headers=headers) as response:
            if (
                response.status_code != httpx.codes.PARTIAL_CONTENT
                or response.headers.get("Content-Range")
                != f"bytes {start}-{end}/{self._length}"
            ):
                raise SourceMalformedError("CVM ZIP changed or range was not honored")
            data = response.read()
        if len(data) != end - start + 1:
            raise SourceMalformedError("CVM ZIP range was truncated")
        return data


def _revision(headers: httpx.Headers) -> ArchiveRevision:
    return ArchiveRevision(headers.get("ETag"), headers.get("Last-Modified"))


def _archive_url(archive: DisclosureArchive) -> str:
    return f"{CVM_DOCUMENT_ROOT}/{archive.kind}/DADOS/{archive.name}.zip"


def _require_revision(
    artifact: SourceArtifact | None, revision: ArchiveRevision | None
) -> SourceArtifact:
    if (
        artifact is None
        or ArchiveRevision(artifact.etag, artifact.last_modified) != revision
    ):
        raise SourceMalformedError("CVM archive changed after reading its catalog")
    return artifact


class CvmDisclosureSource:
    """Use published reference dates and versions to select actual statement reads."""

    def __init__(
        self,
        catalog_http: httpx.Client,
        http: httpx.AsyncClient,
        store: SourceArtifactStore,
        issuers: tuple[DisclosureIssuer, ...],
        *,
        cache_dir: str,
        validation_reporter: BatchValidationReporter | None = None,
    ) -> None:
        self._catalog_http = catalog_http
        self._http = http
        self._store = store
        self._issuers = {issuer.code: issuer for issuer in issuers}
        self._cache_dir = cache_dir
        self._validation = validation_reporter

    async def inspect(
        self, archive: DisclosureArchive, revision: ArchiveRevision | None
    ) -> DisclosureEdition:
        try:
            return await asyncio.to_thread(self._inspect, archive, revision)
        except (
            httpx.HTTPError,
            KeyError,
            OSError,
            ValueError,
            zipfile.BadZipFile,
        ) as exc:
            raise SourceMalformedError(
                f"cannot read CVM disclosure catalog: {exc}"
            ) from exc

    def _inspect(
        self, archive: DisclosureArchive, revision: ArchiveRevision | None
    ) -> DisclosureEdition:
        url = _archive_url(archive)
        headers = {"Accept-Encoding": "identity"}
        if revision is not None:
            if revision.etag:
                headers["If-None-Match"] = revision.etag
            elif revision.last_modified:
                headers["If-Modified-Since"] = revision.last_modified
        response = self._catalog_http.head(url, headers=headers)
        if response.status_code == httpx.codes.NOT_FOUND:
            return DisclosureEdition(None, {})
        if response.status_code == httpx.codes.NOT_MODIFIED:
            return DisclosureEdition(revision, {})
        response.raise_for_status()
        current = _revision(response.headers)
        length = int(response.headers["Content-Length"])
        if length <= 0 or not (current.etag or current.last_modified):
            raise SourceMalformedError("CVM ZIP lacks a length or revision validator")
        versions: dict[DisclosurePeriod, set[int]] = {}
        with (
            _ArchiveRanges(self._catalog_http, url, length, current) as ranges,
            zipfile.ZipFile(ranges) as bundle,
            bundle.open(f"{archive.name}.csv") as member,
            io.TextIOWrapper(member, encoding="latin-1") as source,
        ):
            reader = csv.DictReader(source, delimiter=";")
            if not {"CD_CVM", "DT_REFER", "VERSAO"}.issubset(reader.fieldnames or ()):
                raise SourceMalformedError("CVM disclosure catalog lacks filing keys")
            for row in reader:
                code = row["CD_CVM"].strip().lstrip("0")
                if not code or (archive.kind != "FCA" and code not in self._issuers):
                    continue
                reference = date.fromisoformat(row["DT_REFER"])
                version = int(row["VERSAO"])
                if version <= 0 or reference.year != archive.year:
                    raise SourceMalformedError("invalid CVM disclosure key")
                period = DisclosurePeriod(code, archive.kind, reference)
                versions.setdefault(period, set()).add(version)
        return DisclosureEdition(
            current,
            {period: tuple(sorted(values)) for period, values in versions.items()},
        )

    async def read(
        self,
        archive: DisclosureArchive,
        edition: DisclosureEdition,
        targets: dict[DisclosurePeriod, tuple[int, ...]],
    ) -> DisclosureBatch:
        issuers = [
            self._issuers[code] for code in sorted({key.code for key in targets})
        ]
        readers = await self._readers(archive, edition, issuers)
        results: dict[DisclosurePeriod, list[RawFetchResult]] = {
            key: [] for key in targets
        }
        errors: dict[str, str] = {}
        for issuer in issuers:
            try:
                await self._read_issuer(archive, issuer, readers, targets, results)
            except (ValueError, KeyError, TypeError) as error:
                errors[issuer.code] = f"{type(error).__name__}: {error}"[:500]
                for key in results:
                    if key.code == issuer.code:
                        results[key].clear()
        return DisclosureBatch(results, errors)

    async def _read_issuer(
        self,
        archive: DisclosureArchive,
        issuer: DisclosureIssuer,
        readers: dict[str, RawDataSource],
        targets: dict[DisclosurePeriod, tuple[int, ...]],
        results: dict[DisclosurePeriod, list[RawFetchResult]],
    ) -> None:
        for module, reader in readers.items():
            try:
                responses = await reader.fetch(issuer.ticker, module)
            except SourceNotFoundError:
                continue
            for response in responses:
                request = response.request
                reference = request.get("reference_date") or request["Data_Referencia"]
                key = DisclosurePeriod(
                    issuer.code, archive.kind, date.fromisoformat(str(reference))
                )
                if key in targets and response_version(response) in targets[key]:
                    results[key].append(response)

    async def _readers(
        self,
        archive: DisclosureArchive,
        edition: DisclosureEdition,
        issuers: list[DisclosureIssuer],
    ) -> dict[str, RawDataSource]:
        codes = {issuer.ticker: issuer.code for issuer in issuers}
        cnpjs = {issuer.ticker: issuer.cnpj for issuer in issuers}
        if archive.kind == "FRE":
            return await self._reference_readers(archive, edition, codes, cnpjs)
        if archive.kind == "FCA":
            raise ValueError("FCA uses the lossless registry mirror")
        statements = CvmDataSource(
            self._http,
            codes,
            year=archive.year,
            document=archive.kind,
            cache_dir=self._cache_dir,
            artifact_store=self._store,
            validation_reporter=self._validation,
        )
        _require_revision(await statements.artifact(), edition.revision)
        treasury = CvmTreasurySource(
            self._http,
            cnpjs,
            ticker_to_code=codes,
            year=archive.year,
            document=archive.kind,
            cache_dir=self._cache_dir,
            artifact_store=self._store,
            validation_reporter=self._validation,
        )
        financials: RawDataSource = statements
        readers = dict.fromkeys(STATEMENT_MODULES, financials)
        readers[TREASURY_MODULE] = treasury
        return readers

    async def _reference_readers(
        self,
        archive: DisclosureArchive,
        edition: DisclosureEdition,
        codes: dict[str, str],
        cnpjs: dict[str, str],
    ) -> dict[str, RawDataSource]:
        capital = CvmCapitalSource(
            self._http,
            cnpjs,
            year=archive.year,
            ticker_to_code=codes,
            cache_dir=self._cache_dir,
            artifact_store=self._store,
            validation_reporter=self._validation,
        )
        _require_revision(await capital.artifact(), edition.revision)
        return {
            CAPITAL_MODULE: capital,
            CAPITAL_EVENT_MODULE: CvmCapitalEventSource(
                self._http,
                cnpjs,
                year=archive.year,
                ticker_to_code=codes,
                cache_dir=self._cache_dir,
                artifact_store=self._store,
                validation_reporter=self._validation,
            ),
            FREE_FLOAT_MODULE: CvmFreeFloatSource(
                capital,
                cnpjs,
                codes,
                year=archive.year,
                validation_reporter=self._validation,
            ),
            SHAREHOLDER_POSITION_MODULE: CvmShareholderPositionSource(
                capital,
                cnpjs,
                codes,
                year=archive.year,
                validation_reporter=self._validation,
            ),
            TAG_ALONG_MODULE: CvmTagAlongSource(
                capital,
                cnpjs,
                codes,
                year=archive.year,
                validation_reporter=self._validation,
            ),
        }


class CvmIdentitySource:
    """Compare the public FCA catalog to the latest admitted MongoDB revision."""

    def __init__(
        self,
        catalog: CvmDisclosureSource,
        inputs: MongoSourceInputs,
        store: SourceArtifactStore,
        admit: Callable[[SourceInputReader], Awaitable[bool]],
        *,
        begin_materialization: Callable[[DisclosureArchive], Awaitable[None]]
        | None = None,
    ) -> None:
        self._catalog = catalog
        self._inputs = inputs
        self._store = store
        self._admit = admit
        self._begin_materialization = begin_materialization

    async def refresh_identity(
        self, archive: DisclosureArchive, revision: ArchiveRevision | None
    ) -> ArchiveRevision | None:
        edition = await self._catalog.inspect(archive, revision)
        if not edition.versions:
            return edition.revision
        member = f"{archive.name}.csv"
        published = {
            (period.code, period.reference.isoformat(), version)
            for period, versions in edition.versions.items()
            for version in versions
        }
        current = (await self._inputs.pin()).get(("FCA", archive.year))
        if current is not None and member in current.member_counts:
            mirrored = {
                (
                    row["CD_CVM"].strip().lstrip("0"),
                    row["DT_REFER"],
                    int(row["VERSAO"]),
                )
                async for row in self._inputs.rows(current, member)
                if row.get("CD_CVM", "").strip().lstrip("0")
            }
            if mirrored == published:
                return edition.revision
        artifact = _require_revision(
            await self._store.acquire(_archive_url(archive)), edition.revision
        )
        required = {
            f"fca_cia_aberta_{kind}_{archive.year}.csv"
            for kind in ("geral", "valor_mobiliario")
        }
        with zipfile.ZipFile(artifact.path) as bundle:
            if not required.issubset(bundle.namelist()):
                raise SourceMalformedError(
                    f"FCA identity members missing for {archive.year}"
                )
        try:
            if self._begin_materialization is not None:
                await self._begin_materialization(archive)
            await self._admit(LocalSourceInputReader(artifact.path, artifact=artifact))
        except ValueError as exc:
            raise SourceMalformedError(f"invalid FCA archive: {exc}") from exc
        return edition.revision
