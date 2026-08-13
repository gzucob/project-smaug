"""CVM capital composition (share counts) — the FRE side of the raw mirror.

The share count is the one number the statements never carry: BPA/BPP/DRE/DFC
say nothing about how many shares exist. CVM publishes it in the FRE
(*Formulário de Referência*), a yearly ZIP keyed by **CNPJ** rather than by the
``CD_CVM`` the statements use. Inside it, ``fre_cia_aberta_capital_social_*``
lists each company's capital, so this source mirrors the paid-in row as filed —
ordinary, preferred and total share counts, no arithmetic (that is Phase 2).

Two real-world quirks are handled here:
  * pycvm's ``FREFile`` rejects the modern files (``BadDocument: unknown
    document type 'FRE WEB'``), so the CSV member is read directly.
  * A company files the same reference date several times (``Versao``); the
    highest version is the amendment that supersedes the rest.

And one that is *not* handled here, deliberately: within a single version, the
member is a **history of capital events**, one row per approval date, several of
them paid-in (SANEPAR's 2021 FRE files the 2020 split alongside two 2016
approvals). Every row is mirrored with the approval date that identifies it (#86);
which one is the company's capital *today* is the reader's judgement, not the
mirror's (ADR 0016).
"""

from __future__ import annotations

import asyncio
import csv
import io
import zipfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import httpx

from smaug.ingestion.domain.ports import RawFetchResult
from smaug.ingestion.domain.runs import ParserIdentity
from smaug.ingestion.domain.validation import (
    BatchValidationReporter,
    SourceBatchValidation,
)
from smaug.ingestion.infrastructure.batch_validation import (
    CsvMemberSpec,
    quarantined_archive_validation,
    record_or_quarantine,
    validate_csv_archive,
)
from smaug.ingestion.infrastructure.cvm_source import (
    _DOCUMENT_BASE_URL,
    _DOCUMENT_PREFIX,
    CvmDocument,
)
from smaug.shared.artifacts import SourceArtifact, SourceArtifactStore
from smaug.shared.download import Sleeper, download_zip
from smaug.shared.errors import CvmDownloadError, SourceNotFoundError
from smaug.shared.logging import get_logger

logger = get_logger(__name__)

CVM_FRE_BASE_URL = "https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/FRE/DADOS"

# The module names these sources answer to, alongside the statement modules.
# CAPITAL is the FRE's share count (the primary one, ADR 0004); CAPITAL_DFP is the
# statements ZIP's own composition, which is what carries treasury shares.
CAPITAL_MODULE = "CAPITAL"
TREASURY_MODULE = "CAPITAL_DFP"
# The FRE's *declared* corporate actions — split, grupamento, bonificação — with
# the approval date and the share count on both sides of the event.
CAPITAL_EVENT_MODULE = "CAPITAL_EVENT"

# Of the three capital rows a company files (issued / subscribed / paid-in),
# paid-in is the one that reflects shares actually in existence.
_PAID_IN_CAPITAL = "Capital Integralizado"

# The FRE CSVs are latin-1 and semicolon-separated, like every CVM open dataset.
_ENCODING = "latin-1"
_DELIMITER = ";"


def _int(value: str | None) -> int:
    return int(value) if value else 0


def _member_validation(
    archive: Path,
    *,
    source: str,
    batch: str,
    parser: ParserIdentity,
    artifact: SourceArtifact | None,
    year: int,
    member: str,
    columns: frozenset[str],
    registrant_column: str,
    period_column: str,
    require_member: bool,
) -> SourceBatchValidation:
    return validate_csv_archive(
        archive,
        source=source,
        batch=batch,
        parser=parser,
        artifact=artifact,
        expected_year=year,
        members=(CsvMemberSpec(member, columns, registrant_column, period_column),),
        require_member=require_member,
    )


class CvmCapitalSource:
    """Fetch the capital composition for one ticker from CVM's yearly FRE file."""

    source = "cvm"
    parser_identity = ParserIdentity("cvm.capital.csv", 2)

    def __init__(
        self,
        http_client: httpx.AsyncClient,
        ticker_to_cnpj: Mapping[str, str],
        *,
        year: int,
        cache_dir: str,
        ticker_to_code: Mapping[str, str] | None = None,
        base_url: str | None = None,
        sleep: Sleeper = asyncio.sleep,
        artifact_store: SourceArtifactStore | None = None,
        artifact_id: str | None = None,
        validation_reporter: BatchValidationReporter | None = None,
    ) -> None:
        self._http = http_client
        self._ticker_to_cnpj = dict(ticker_to_cnpj)
        # The FRE is keyed by CNPJ, but the mirror is read by ``CD_CVM`` (ADR 0030),
        # and the two name the same registrant. The composition root resolves both,
        # so the second map only travels here to be stamped onto what is stored.
        self._ticker_to_code = dict(ticker_to_code or {})
        self._year = year
        self._cache_dir = Path(cache_dir)
        self._base_url = (base_url or CVM_FRE_BASE_URL).rstrip("/")
        self._sleep = sleep
        self._artifact_store = artifact_store
        self._replay_artifact_id = artifact_id
        self._artifact: SourceArtifact | None = None
        self._validation_reporter = validation_reporter
        self._validated = False
        self._index: dict[str, list[dict[str, Any]]] | None = None
        self._lock = asyncio.Lock()

    @property
    def _zip_name(self) -> str:
        return f"fre_cia_aberta_{self._year}.zip"

    @property
    def archive_name(self) -> str:
        """The yearly archive this source reads — what a mirrored document names."""
        return self._zip_name

    async def artifact(self) -> SourceArtifact | None:
        """Acquire the FRE identity without parsing its members."""
        return await self._ensure_artifact()

    @property
    def _member_name(self) -> str:
        return f"fre_cia_aberta_capital_social_{self._year}.csv"

    @property
    def _class_member_name(self) -> str:
        return f"fre_cia_aberta_capital_social_classe_acao_{self._year}.csv"

    async def fetch(self, ticker: str, module: str) -> Sequence[RawFetchResult]:
        """Return every paid-in capital row ``ticker`` filed — one per amendment,
        and one per approval date within an amendment.

        The mirror keeps all of them and picks none (ADR 0016); the reader takes
        the highest ``version``, and within it the latest ``approval_date``. The FRE
        is heavily amended (BBDC4 is on v30) and each amendment restates the whole
        capital history, so which row supersedes which is exactly the kind of
        judgement that does not belong in an append-only mirror.
        """
        index = await self._ensure_loaded()

        cnpj = self._ticker_to_cnpj.get(ticker)
        if cnpj is None:
            raise SourceNotFoundError(f"no CNPJ mapped for {ticker}")
        rows = index.get(cnpj)
        if not rows:
            raise SourceNotFoundError(
                f"no CVM {self._year} FRE capital for {ticker} ({cnpj})"
            )

        return [
            RawFetchResult(
                module=module,
                source="cvm",
                request={
                    "source": "cvm",
                    "file": self._zip_name,
                    "cnpj": cnpj,
                    "statement": module,
                    "reference_date": row["reference_date"],
                    "version": row["version"],
                    # Two paid-in rows of the same version differ only by the capital
                    # event they record — so the event identifies the request (#86).
                    "capital_id": row["capital_id"],
                    "approval_date": row["approval_date"],
                },
                http_status=200,
                payload=row,
                cvm_code=self._ticker_to_code.get(ticker),
                artifact_id=(
                    self._artifact.artifact_id if self._artifact is not None else None
                ),
            )
            for row in rows
        ]

    async def _ensure_loaded(self) -> dict[str, list[dict[str, Any]]]:
        cached = self._index
        if cached is not None:
            return cached
        async with self._lock:
            cached = self._index
            if cached is not None:
                return cached
            raw = await self._archive_path()
            if not self._validated:
                validation = await asyncio.to_thread(
                    validate_csv_archive,
                    raw,
                    source="cvm",
                    batch=self._zip_name,
                    parser=self.parser_identity,
                    artifact=self._artifact,
                    expected_year=self._year,
                    members=(
                        CsvMemberSpec(
                            self._member_name,
                            frozenset(
                                {
                                    "CNPJ_Companhia",
                                    "Nome_Companhia",
                                    "Data_Referencia",
                                    "Versao",
                                    "ID_Capital_Social",
                                    "Tipo_Capital",
                                    "Data_Autorizacao_Aprovacao",
                                    "Valor_Capital",
                                    "Prazo_Integralizacao",
                                    "Quantidade_Acoes_Ordinarias",
                                    "Quantidade_Acoes_Preferenciais",
                                    "Quantidade_Total_Acoes",
                                }
                            ),
                            "CNPJ_Companhia",
                            "Data_Referencia",
                        ),
                        CsvMemberSpec(
                            self._class_member_name,
                            frozenset(
                                {
                                    "CNPJ_Companhia",
                                    "Nome_Companhia",
                                    "Data_Referencia",
                                    "Versao",
                                    "ID_Capital_Social",
                                    "Tipo_Classe_Acao_Preferencial",
                                    "Quantidade_Acoes",
                                }
                            ),
                            "CNPJ_Companhia",
                            "Data_Referencia",
                        ),
                    ),
                    require_member=True,
                    require_all_members=True,
                )
                await record_or_quarantine(self._validation_reporter, validation)
                self._validated = True
            index = await asyncio.to_thread(self._build_index, raw)
            self._index = index
            logger.info(
                "Loaded CVM FRE %s: %d of %d portfolio companies found",
                self._year,
                len(index),
                len(set(self._ticker_to_cnpj.values())),
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
                try:
                    self._artifact = await self._artifact_store.acquire(
                        f"{self._base_url}/{self._zip_name}", follow_redirects=True
                    )
                except CvmDownloadError as exc:
                    if exc.quarantined_artifact_id is None:
                        raise
                    await record_or_quarantine(
                        self._validation_reporter,
                        quarantined_archive_validation(
                            batch=self._zip_name,
                            parser=self.parser_identity,
                            artifact_id=exc.quarantined_artifact_id,
                            detail=str(exc),
                        ),
                    )
                    raise AssertionError(
                        "quarantined archive returned unexpectedly"
                    ) from exc
        return self._artifact

    async def _download(self, dst: Path) -> None:
        # Same shared-file reasoning as the statements ZIP: retry + atomic
        # write, and a definitive failure is fatal for the run (#16).
        url = f"{self._base_url}/{self._zip_name}"
        logger.info("Downloading CVM FRE %s from %s", self._year, url)
        await download_zip(
            self._http, url, dst, follow_redirects=True, sleep=self._sleep
        )

    def _build_index(self, archive: Path) -> dict[str, list[dict[str, Any]]]:
        """Index every paid-in capital row per wanted CNPJ (sync; runs in a thread).

        Every amendment is kept, not just the latest (ADR 0016) — the reader picks.
        """
        wanted = set(self._ticker_to_cnpj.values())
        index: dict[str, list[dict[str, Any]]] = {}
        with zipfile.ZipFile(archive) as archive_file:
            class_counts: dict[tuple[str, str, str, str], list[dict[str, Any]]] = {}
            with archive_file.open(self._class_member_name) as member:
                reader = csv.DictReader(
                    io.TextIOWrapper(member, encoding=_ENCODING),
                    delimiter=_DELIMITER,
                )
                for row in reader:
                    if row["CNPJ_Companhia"] not in wanted:
                        continue
                    key = _capital_key(row)
                    class_counts.setdefault(key, []).append(
                        {
                            "share_class": row["Tipo_Classe_Acao_Preferencial"],
                            "shares": _int(row["Quantidade_Acoes"]),
                        }
                    )
            with archive_file.open(self._member_name) as member:
                reader = csv.DictReader(
                    io.TextIOWrapper(member, encoding=_ENCODING),
                    delimiter=_DELIMITER,
                )
                for row in reader:
                    cnpj = row["CNPJ_Companhia"]
                    if cnpj not in wanted or row["Tipo_Capital"] != _PAID_IN_CAPITAL:
                        continue
                    index.setdefault(cnpj, []).append(
                        _to_payload(row, class_counts.get(_capital_key(row), []))
                    )
        return index


def _capital_key(row: Mapping[str, str]) -> tuple[str, str, str, str]:
    """The parent key shared by FRE's capital and capital-by-class members."""
    return (
        row["CNPJ_Companhia"],
        row["Data_Referencia"],
        row["Versao"],
        row["ID_Capital_Social"],
    )


def _to_payload(
    row: Mapping[str, str], class_counts: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    """Mirror the filed row — share counts as filed, no derivation.

    ``approval_date`` is what tells one paid-in row from another within the same
    version: the same filing carries the capital as approved on several dates, and
    without it the reader is choosing by cursor order (#86).
    """
    return {
        "cnpj": row["CNPJ_Companhia"],
        "company_name": row["Nome_Companhia"],
        "reference_date": row["Data_Referencia"],
        "version": _int(row["Versao"]),
        "capital_id": row["ID_Capital_Social"],
        "capital_type": row["Tipo_Capital"],
        "approval_date": row["Data_Autorizacao_Aprovacao"],
        "capital_value": row["Valor_Capital"],
        "payment_term": row["Prazo_Integralizacao"],
        "common_shares": _int(row["Quantidade_Acoes_Ordinarias"]),
        "preferred_shares": _int(row["Quantidade_Acoes_Preferenciais"]),
        "total_shares": _int(row["Quantidade_Total_Acoes"]),
        # Child rows are mirrored with their filed labels. Interpreting which
        # labels price PNA/PNB belongs to the analysis reader, not ingestion.
        "share_class_counts": [dict(item) for item in class_counts],
    }


class CvmCapitalEventSource:
    """Fetch the corporate actions CVM **declares**, from the same yearly FRE ZIP.

    ADR 0027 infers splits from the ratio between consecutive years' share counts,
    on the stated premise that "the FRE never labels a split". It does. The
    archive carries 43 members and this project opened one of them; another is
    named after the event:

        fre_cia_aberta_capital_social_desdobramento_{year}.csv

    It files ``Tipo_Evento`` (Grupamento | Desdobramento | Bonificação), the
    ``Data_Aprovacao``, and the total **before and after** the approval — which is
    the ratio, stated rather than deduced. That distinction is not academic: a
    count ratio also moves on issuances and cancellations, so inferring from it
    conflated Ampla's 1:40,000 grupamento with the share issue that followed and
    produced a factor (1/23,539) matching neither.

    **Coverage stops after the 2023 FRE**, where CVM restructured the form and
    dropped the member. The recent events (BBAS3's 2024 split, HAPV3's 2025
    grupamento) come from B3 instead, and the two sources are complementary
    rather than redundant: B3 lists one Bradesco bonus where this file lists its
    10% bonus in 2012, 2013, 2014, 2015, 2016 and 2018.

    A year whose archive has no such member yields ``SourceNotFoundError`` per
    ticker, like any other absent filing — the mirror records the absence rather
    than inventing an empty event list.
    """

    source = "cvm"
    parser_identity = ParserIdentity("cvm.capital-events.csv", 1)

    def __init__(
        self,
        http_client: httpx.AsyncClient,
        ticker_to_cnpj: Mapping[str, str],
        *,
        year: int,
        cache_dir: str,
        ticker_to_code: Mapping[str, str] | None = None,
        base_url: str | None = None,
        sleep: Sleeper = asyncio.sleep,
        artifact_store: SourceArtifactStore | None = None,
        artifact_id: str | None = None,
        validation_reporter: BatchValidationReporter | None = None,
    ) -> None:
        self._http = http_client
        self._ticker_to_cnpj = dict(ticker_to_cnpj)
        self._ticker_to_code = dict(ticker_to_code or {})
        self._year = year
        self._cache_dir = Path(cache_dir)
        self._base_url = (base_url or CVM_FRE_BASE_URL).rstrip("/")
        self._sleep = sleep
        self._artifact_store = artifact_store
        self._replay_artifact_id = artifact_id
        self._artifact: SourceArtifact | None = None
        self._validation_reporter = validation_reporter
        self._validated = False
        self._index: dict[str, list[dict[str, Any]]] | None = None
        self._lock = asyncio.Lock()

    @property
    def _zip_name(self) -> str:
        return f"fre_cia_aberta_{self._year}.zip"

    @property
    def archive_name(self) -> str:
        """The yearly archive this source reads — what a mirrored document names."""
        return self._zip_name

    async def artifact(self) -> SourceArtifact | None:
        """Acquire the FRE identity without parsing its members."""
        return await self._ensure_artifact()

    @property
    def _member_name(self) -> str:
        return f"fre_cia_aberta_capital_social_desdobramento_{self._year}.csv"

    async def fetch(self, ticker: str, module: str) -> Sequence[RawFetchResult]:
        """Every corporate action ``ticker``'s company declared in this FRE year.

        The same event is restated in every later FRE, so the mirror will hold it
        once per year it was filed in — kept, not deduplicated (ADR 0016). The
        reader identifies an event by company + approval date + type.
        """
        index = await self._ensure_loaded()

        cnpj = self._ticker_to_cnpj.get(ticker)
        if cnpj is None:
            raise SourceNotFoundError(f"no CNPJ mapped for {ticker}")
        rows = index.get(cnpj)
        if not rows:
            raise SourceNotFoundError(
                f"no CVM {self._year} FRE capital event for {ticker} ({cnpj})"
            )

        return [
            RawFetchResult(
                module=module,
                source="cvm",
                request={
                    "source": "cvm",
                    "file": self._zip_name,
                    "cnpj": cnpj,
                    "statement": module,
                    "reference_date": row["reference_date"],
                    "version": row["version"],
                    "event_id": row["event_id"],
                    "approval_date": row["approval_date"],
                },
                http_status=200,
                payload=row,
                cvm_code=self._ticker_to_code.get(ticker),
                artifact_id=(
                    self._artifact.artifact_id if self._artifact is not None else None
                ),
            )
            for row in rows
        ]

    async def _ensure_loaded(self) -> dict[str, list[dict[str, Any]]]:
        cached = self._index
        if cached is not None:
            return cached
        async with self._lock:
            cached = self._index
            if cached is not None:
                return cached
            raw = await self._archive_path()
            if not self._validated:
                validation = await asyncio.to_thread(
                    _member_validation,
                    raw,
                    source="cvm",
                    batch=self._zip_name,
                    parser=self.parser_identity,
                    artifact=self._artifact,
                    year=self._year,
                    member=self._member_name,
                    columns=frozenset(
                        {
                            "CNPJ_Companhia",
                            "Nome_Companhia",
                            "Data_Referencia",
                            "Versao",
                            "ID_Capital_Social_Desdobramento",
                            "Data_Aprovacao",
                            "Tipo_Evento",
                            "Quantidade_Acoes_Ordinarias_Antes_Aprovacao",
                            "Quantidade_Acoes_Preferenciais_Antes_Aprovacao",
                            "Quantidade_Total_Acoes_Antes_Aprovacao",
                            "Quantidade_Acoes_Ordinarias_Depois_Aprovacao",
                            "Quantidade_Acoes_Preferenciais_Depois_Aprovacao",
                            "Quantidade_Total_Acoes_Depois_Aprovacao",
                        }
                    ),
                    registrant_column="CNPJ_Companhia",
                    period_column="Data_Referencia",
                    require_member=False,
                )
                await record_or_quarantine(self._validation_reporter, validation)
                self._validated = True
            index = await asyncio.to_thread(self._build_index, raw)
            self._index = index
            logger.info(
                "Loaded CVM FRE %s corporate actions: %d companies declared one",
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
            logger.info("Downloading CVM FRE %s from %s", self._year, self._base_url)
            await download_zip(
                self._http,
                f"{self._base_url}/{self._zip_name}",
                raw,
                follow_redirects=True,
                sleep=self._sleep,
            )
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
                try:
                    self._artifact = await self._artifact_store.acquire(
                        f"{self._base_url}/{self._zip_name}", follow_redirects=True
                    )
                except CvmDownloadError as exc:
                    if exc.quarantined_artifact_id is None:
                        raise
                    await record_or_quarantine(
                        self._validation_reporter,
                        quarantined_archive_validation(
                            batch=self._zip_name,
                            parser=self.parser_identity,
                            artifact_id=exc.quarantined_artifact_id,
                            detail=str(exc),
                        ),
                    )
                    raise AssertionError(
                        "quarantined archive returned unexpectedly"
                    ) from exc
        return self._artifact

    def _build_index(self, archive: Path) -> dict[str, list[dict[str, Any]]]:
        index: dict[str, list[dict[str, Any]]] = {}
        wanted = set(self._ticker_to_cnpj.values())
        with zipfile.ZipFile(archive) as archive_file:
            if self._member_name not in archive_file.namelist():
                # CVM restructured the FRE for 2024 onward and the member is gone.
                # Not an error: the year simply declares no events here.
                logger.info(
                    "CVM FRE %s has no %s; corporate actions for this year come "
                    "from B3 instead",
                    self._year,
                    self._member_name,
                )
                return index
            with archive_file.open(self._member_name) as member:
                reader = csv.DictReader(
                    io.TextIOWrapper(member, encoding=_ENCODING),
                    delimiter=_DELIMITER,
                )
                for row in reader:
                    cnpj = row["CNPJ_Companhia"]
                    if cnpj in wanted:
                        index.setdefault(cnpj, []).append(_to_event_payload(row))
        return index


def _to_event_payload(row: Mapping[str, str]) -> dict[str, Any]:
    """Mirror one declared corporate action as filed — no ratio computed here.

    The ratio is ``after / before``, and deriving it is the reader's job (ADR
    0016): a row with a zero on either side is unusable for that division but is
    still what the company filed, so it is stored rather than dropped.
    """
    return {
        "cnpj": row["CNPJ_Companhia"],
        "company_name": row["Nome_Companhia"],
        "reference_date": row["Data_Referencia"],
        "version": _int(row["Versao"]),
        "event_id": row["ID_Capital_Social_Desdobramento"],
        "approval_date": row["Data_Aprovacao"],
        "event_type": row["Tipo_Evento"],
        "common_before": _int(row["Quantidade_Acoes_Ordinarias_Antes_Aprovacao"]),
        "preferred_before": _int(row["Quantidade_Acoes_Preferenciais_Antes_Aprovacao"]),
        "total_before": _int(row["Quantidade_Total_Acoes_Antes_Aprovacao"]),
        "common_after": _int(row["Quantidade_Acoes_Ordinarias_Depois_Aprovacao"]),
        "preferred_after": _int(row["Quantidade_Acoes_Preferenciais_Depois_Aprovacao"]),
        "total_after": _int(row["Quantidade_Total_Acoes_Depois_Aprovacao"]),
    }


class CvmTreasurySource:
    """Fetch the DFP/ITR's own capital composition — the one that names treasury.

    The statements ZIP carries a ``composicao_capital`` member the FRE has no
    equivalent of: it reports **shares held in treasury**, which are issued but not
    outstanding, and which the market cap (ADR 0014) arguably should not count.

    It does **not** replace the FRE as the share-count source (ADR 0004 stands).
    Its counts are filed at an inconsistent scale — TAEE11, VALE3 and CXSE3 file
    thousands while PETR4, BBAS3 and WEGE3 file units, and the member has **no
    scale column** to tell them apart. So it is mirrored exactly as filed, scale
    problem and all, and resolving that is the reader's problem, not the mirror's.
    """

    source = "cvm"
    parser_identity = ParserIdentity("cvm.treasury.csv", 1)

    def __init__(
        self,
        http_client: httpx.AsyncClient,
        ticker_to_cnpj: Mapping[str, str],
        *,
        year: int,
        cache_dir: str,
        ticker_to_code: Mapping[str, str] | None = None,
        document: CvmDocument = "DFP",
        base_url: str | None = None,
        sleep: Sleeper = asyncio.sleep,
        artifact_store: SourceArtifactStore | None = None,
        artifact_id: str | None = None,
        validation_reporter: BatchValidationReporter | None = None,
    ) -> None:
        self._http = http_client
        self._ticker_to_cnpj = dict(ticker_to_cnpj)
        self._ticker_to_code = dict(ticker_to_code or {})
        self._year = year
        self._cache_dir = Path(cache_dir)
        self._document = document
        self._prefix = _DOCUMENT_PREFIX[document]
        self._base_url = (base_url or _DOCUMENT_BASE_URL[document]).rstrip("/")
        self._sleep = sleep
        self._artifact_store = artifact_store
        self._replay_artifact_id = artifact_id
        self._artifact: SourceArtifact | None = None
        self._validation_reporter = validation_reporter
        self._validated = False
        self._index: dict[str, list[dict[str, Any]]] | None = None
        self._lock = asyncio.Lock()

    @property
    def _zip_name(self) -> str:
        return f"{self._prefix}_{self._year}.zip"

    @property
    def archive_name(self) -> str:
        """The yearly archive this source reads — what a mirrored document names."""
        return self._zip_name

    async def artifact(self) -> SourceArtifact | None:
        """Acquire the statements identity without parsing its members."""
        return await self._ensure_artifact()

    @property
    def _member_name(self) -> str:
        return f"{self._prefix}_composicao_capital_{self._year}.csv"

    async def fetch(self, ticker: str, module: str) -> Sequence[RawFetchResult]:
        """Return every capital-composition row ``ticker`` filed — one per version."""
        index = await self._ensure_loaded()

        cnpj = self._ticker_to_cnpj.get(ticker)
        if cnpj is None:
            raise SourceNotFoundError(f"no CNPJ mapped for {ticker}")
        rows = index.get(cnpj)
        if not rows:
            raise SourceNotFoundError(
                f"no CVM {self._year} {self._document} capital for {ticker} ({cnpj})"
            )

        return [
            RawFetchResult(
                module=module,
                source="cvm",
                request={
                    "source": "cvm",
                    "file": self._zip_name,
                    "cnpj": cnpj,
                    "statement": module,
                    "reference_date": row["reference_date"],
                    "version": row["version"],
                },
                http_status=200,
                payload=row,
                cvm_code=self._ticker_to_code.get(ticker),
                artifact_id=(
                    self._artifact.artifact_id if self._artifact is not None else None
                ),
            )
            for row in rows
        ]

    async def _ensure_loaded(self) -> dict[str, list[dict[str, Any]]]:
        cached = self._index
        if cached is not None:
            return cached
        async with self._lock:
            cached = self._index
            if cached is not None:
                return cached
            raw = await self._archive_path()
            if not raw.exists():
                url = f"{self._base_url}/{self._zip_name}"
                logger.info("Downloading CVM %s %s", self._document, self._year)
                await download_zip(self._http, url, raw, sleep=self._sleep)
            if not self._validated:
                validation = await asyncio.to_thread(
                    _member_validation,
                    raw,
                    source="cvm",
                    batch=self._zip_name,
                    parser=self.parser_identity,
                    artifact=self._artifact,
                    year=self._year,
                    member=self._member_name,
                    columns=frozenset(
                        {
                            "CNPJ_CIA",
                            "DENOM_CIA",
                            "DT_REFER",
                            "VERSAO",
                            "QT_ACAO_ORDIN_CAP_INTEGR",
                            "QT_ACAO_PREF_CAP_INTEGR",
                            "QT_ACAO_TOTAL_CAP_INTEGR",
                            "QT_ACAO_ORDIN_TESOURO",
                            "QT_ACAO_PREF_TESOURO",
                            "QT_ACAO_TOTAL_TESOURO",
                        }
                    ),
                    registrant_column="CNPJ_CIA",
                    period_column="DT_REFER",
                    require_member=False,
                )
                await record_or_quarantine(self._validation_reporter, validation)
                self._validated = True
            index = await asyncio.to_thread(self._build_index, raw)
            self._index = index
            return index

    async def _archive_path(self) -> Path:
        artifact = await self._ensure_artifact()
        if artifact is not None:
            return artifact.path
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        return self._cache_dir / self._zip_name

    async def _ensure_artifact(self) -> SourceArtifact | None:
        if self._artifact_store is None:
            return None
        if self._artifact is None:
            if self._replay_artifact_id is not None:
                self._artifact = await self._artifact_store.open(
                    self._replay_artifact_id
                )
            else:
                try:
                    self._artifact = await self._artifact_store.acquire(
                        f"{self._base_url}/{self._zip_name}"
                    )
                except CvmDownloadError as exc:
                    if exc.quarantined_artifact_id is None:
                        raise
                    await record_or_quarantine(
                        self._validation_reporter,
                        quarantined_archive_validation(
                            batch=self._zip_name,
                            parser=self.parser_identity,
                            artifact_id=exc.quarantined_artifact_id,
                            detail=str(exc),
                        ),
                    )
                    raise AssertionError(
                        "quarantined archive returned unexpectedly"
                    ) from exc
        return self._artifact

    def _build_index(self, archive: Path) -> dict[str, list[dict[str, Any]]]:
        wanted = set(self._ticker_to_cnpj.values())
        index: dict[str, list[dict[str, Any]]] = {}
        with zipfile.ZipFile(archive) as archive_file:
            # The member is not in every year's archive: CVM began publishing the
            # capital composition partway through the series (the 2019 DFP has no
            # ``composicao_capital`` file, the 2020 one does). An absent member is
            # a year that never carried the data, not a mirror that dropped it —
            # so it yields an empty index, and each ticker then reports "not
            # found" for the module. Raising here aborted the whole year's
            # ingestion, which is how a 2015-2019 backfill failed outright (#63).
            if self._member_name not in archive_file.namelist():
                logger.info(
                    "CVM %s %s has no %s — treasury counts unavailable for that year",
                    self._document,
                    self._year,
                    self._member_name,
                )
                return index
            with archive_file.open(self._member_name) as member:
                reader = csv.DictReader(
                    io.TextIOWrapper(member, encoding=_ENCODING),
                    delimiter=_DELIMITER,
                )
                for row in reader:
                    cnpj = row["CNPJ_CIA"]
                    if cnpj in wanted:
                        index.setdefault(cnpj, []).append(_to_treasury_payload(row))
        return index


def _to_treasury_payload(row: Mapping[str, str]) -> dict[str, Any]:
    """Mirror the filed row. The counts carry the filer's own scale — see the class."""
    return {
        "cnpj": row["CNPJ_CIA"],
        "company_name": row["DENOM_CIA"],
        "reference_date": row["DT_REFER"],
        "version": _int(row["VERSAO"]),
        "common_shares": _int(row["QT_ACAO_ORDIN_CAP_INTEGR"]),
        "preferred_shares": _int(row["QT_ACAO_PREF_CAP_INTEGR"]),
        "total_shares": _int(row["QT_ACAO_TOTAL_CAP_INTEGR"]),
        "treasury_common_shares": _int(row["QT_ACAO_ORDIN_TESOURO"]),
        "treasury_preferred_shares": _int(row["QT_ACAO_PREF_TESOURO"]),
        "treasury_total_shares": _int(row["QT_ACAO_TOTAL_TESOURO"]),
    }
