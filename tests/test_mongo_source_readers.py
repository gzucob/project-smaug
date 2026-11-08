"""Frozen source parity, admitted revisions and source-independent analysis."""

import csv
import io
import json
import os
import socket
import zipfile
from dataclasses import asdict, is_dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import pytest
from beanie import init_beanie
from pymongo import AsyncMongoClient

from smaug.analysis.infrastructure.b3_prices import CotahistArchive, _reduce
from smaug.analysis.infrastructure.mongo_prices import MongoCotahistArchive
from smaug.analysis.infrastructure.quote_series import B3BaseChanges, B3PriceProvider
from smaug.entrypoints import cli
from smaug.ingestion.application.source_inputs import MirrorSourceInputs
from smaug.ingestion.infrastructure.models import (
    SourceInputArchiveDocument,
    SourceInputRowDocument,
)
from smaug.ingestion.infrastructure.source_inputs import (
    LocalSourceInputReader,
    MongoSourceInputRepository,
)
from smaug.portfolio.infrastructure.cvm_registry import CvmCompanyRegistry
from smaug.portfolio.infrastructure.cvm_securities import CvmSecurityHistory
from smaug.portfolio.infrastructure.mongo_registry import (
    MongoCompanyRegistry,
    MongoSecurityHistory,
)
from smaug.shared.config import Settings
from smaug.shared.errors import SourceMalformedError, SourceNotFoundError
from smaug.shared.source_inputs import MongoSourceInputs
from tests.fakes import SourceInputCollection


class FrozenSources:
    def __init__(self) -> None:
        self.archives = SourceInputCollection()
        self.rows = SourceInputCollection()

    async def add(self, path: Path, *, observed_at: datetime | None = None) -> None:
        reader = LocalSourceInputReader(path)
        archive = await reader.inspect()
        rows = list(reader.rows())
        key = {
            "artifact_id": archive.artifact_id,
            "parser_version": archive.parser_version,
        }
        self.archives.documents.append(
            {
                **asdict(archive),
                "member_counts": dict(reader.member_counts()),
                "validation": "zip-crc-and-lossless-row-layout-v1",
                "price_basis": "as_traded" if archive.dataset == "COTAHIST" else None,
                "observed_at": observed_at or archive.observed_at,
            }
        )
        self.rows.documents.extend({**key, **asdict(row)} for row in rows)

    def reader(self) -> MongoSourceInputs:
        return MongoSourceInputs(self.archives, self.rows)


def frozen_files(tmp_path: Path) -> list[Path]:
    paths = []
    for year in range(2010, 2027):
        path = tmp_path / f"fca_cia_aberta_{year}.zip"
        members = {
            "geral": [
                {
                    "CNPJ_Companhia": "33.000.167/0001-01",
                    "Versao": "1",
                    "Codigo_CVM": "9512",
                    "Nome_Empresarial": "PETROLEO BRASILEIRO SA",
                    "Setor_Atividade": "Petroleo e Gas",
                    "Situacao_Registro_CVM": "ATIVO",
                },
                {
                    "CNPJ_Companhia": "33.000.167/0001-01",
                    "Versao": "2",
                    "Codigo_CVM": "9512",
                    "Nome_Empresarial": "PETROLEO BRASILEIRO SA PETROBRAS",
                    "Setor_Atividade": "Petroleo e Gas",
                    "Situacao_Registro_CVM": "ATIVO",
                },
            ],
            "valor_mobiliario": [
                {
                    "CNPJ_Companhia": "33.000.167/0001-01",
                    "Versao": "2",
                    "Codigo_Negociacao": code,
                    "Mercado": "Bolsa",
                    "Sigla_Entidade_Administradora": "B3",
                    "Data_Fim_Negociacao": "",
                    "Data_Inicio_Listagem": "2015-01-01",
                    "Valor_Mobiliario": kind,
                    "Composicao_BDR_Unit": "",
                }
                for code, kind in (
                    ("PETR3", "Ações Ordinárias"),
                    ("PETR4", "Ações Preferenciais"),
                )
            ],
        }
        with zipfile.ZipFile(path, "w") as archive:
            for member, rows in members.items():
                stream = io.StringIO()
                writer = csv.DictWriter(stream, fieldnames=list(rows[0]), delimiter=";")
                writer.writeheader()
                writer.writerows(rows)
                archive.writestr(
                    f"fca_cia_aberta_{member}_{year}.csv",
                    stream.getvalue().encode("latin-1"),
                )
        paths.append(path)
    lines = Path("tests/fixtures/cotahist_sample.txt").read_text().splitlines()
    lines[-1] = lines[-1][:31] + f"{len(lines):011d}" + lines[-1][42:]
    for year in range(1986, 2027):
        if year == 2015:
            continue
        empty = tmp_path / f"COTAHIST_A{year}.ZIP"
        header = lines[0].replace("2015", str(year))
        trailer = lines[-1].replace("2015", str(year))
        trailer = trailer[:31] + f"{2:011d}" + trailer[42:]
        with zipfile.ZipFile(empty, "w") as archive:
            archive.writestr(f"COTAHIST_A{year}.TXT", header + "\n" + trailer)
        paths.append(empty)
    path = tmp_path / "COTAHIST_A2015.ZIP"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("COTAHIST_A2015.TXT", "\n".join(lines))
    paths.append(path)
    return paths


def canonical(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return canonical(asdict(value))
    if isinstance(value, dict):
        return {str(key): canonical(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [canonical(item) for item in value]
    if isinstance(value, (date, datetime, Decimal)):
        return str(value)
    return value


def deny_sources(monkeypatch: pytest.MonkeyPatch) -> None:
    from smaug.portfolio.domain.taxonomy import TAXONOMY_SNAPSHOT

    permitted = {TAXONOMY_SNAPSHOT.resolve()}
    original_open = Path.open

    def guarded_open(path: Path, *args: Any, **kwargs: Any) -> Any:
        if path.resolve() in permitted:
            return original_open(path, *args, **kwargs)
        raise AssertionError(f"financial source file I/O: {path}")

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("financial source I/O reached a database-only reader")

    monkeypatch.setattr(Path, "open", guarded_open)
    monkeypatch.setattr(zipfile, "ZipFile", forbidden)
    monkeypatch.setattr(httpx.AsyncClient, "request", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)


async def test_frozen_prices_identity_history_and_provenance_without_sources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    files = frozen_files(tmp_path)
    source = FrozenSources()
    for path in files:
        await source.add(path)
    async with httpx.AsyncClient() as http:
        reference_registry = CvmCompanyRegistry(
            http, year=2026, cache_dir=str(tmp_path)
        )
        reference_identities = await reference_registry.resolve_all(("PETR3", "PETR4"))
        reference_history = CvmSecurityHistory(
            http, through=2026, cache_dir=str(tmp_path)
        )
        reference_names = await reference_history.names()
        reference_classes = await reference_history.period_share_classes()
        reference_archive = CotahistArchive(
            http, cache_dir=str(tmp_path), today=lambda: date(2016, 1, 1)
        )
        reference_prices = await B3PriceProvider(
            reference_archive, today=lambda: date(2015, 1, 6)
        ).year_prices("PETR4", 2015)
        reference_changes = await B3BaseChanges(reference_archive).base_changes(
            "CEGR3", [2015]
        )
        reference_quotes = _reduce(files[-1])
    inputs = source.reader()
    revisions = await inputs.pin()
    expected_provenance = {
        "year": 2026,
        "source": "cvm_fca",
        "source_url": revisions[("FCA", 2026)].source_url,
        "artifact_id": revisions[("FCA", 2026)].artifact_id,
    }
    # Mongo readers consume fields extracted by ingestion, never the original
    # fixed-width line retained for audit/replay.
    for doc in source.rows.documents:
        doc["fields"].pop("record", None)
    deny_sources(monkeypatch)
    registry = MongoCompanyRegistry(inputs, year=2026)
    assert await registry.resolve_all(("PETR3", "PETR4")) == reference_identities
    assert canonical(await registry.provenance()) == expected_provenance
    history = MongoSecurityHistory(inputs, through=2026)
    assert (await history.names())("PETR4") == reference_names("PETR4")
    assert (await history.period_share_classes())(
        "33.000.167/0001-01", 2015
    ) == reference_classes("33.000.167/0001-01", 2015)
    archive = MongoCotahistArchive(inputs)
    assert await archive.year(2015) == reference_quotes
    assert await B3PriceProvider(archive).year_prices("PETR4", 2015) == reference_prices
    assert (
        await B3BaseChanges(archive).base_changes("CEGR3", [2015]) == reference_changes
    )
    tickers, identities = await cli._universe_tickers(
        Settings(cvm_fca_year=2026), inputs, SourceInputCollection()
    )
    assert tickers == ("PETR3", "PETR4")
    assert identities == reference_identities


async def test_incomplete_or_unsupported_sources_are_not_admitted(
    tmp_path: Path,
) -> None:
    source = FrozenSources()
    await source.add(frozen_files(tmp_path)[-1])
    source.archives.documents[0]["parser_version"] = "future-parser"
    with pytest.raises(
        SourceNotFoundError, match="missing_mirrored_source:COTAHIST:2015"
    ):
        await MongoCotahistArchive(source.reader()).year(2015)
    source.archives.documents.clear()
    with pytest.raises(SourceNotFoundError):
        await MongoCotahistArchive(source.reader()).year(2015)


async def test_missing_rows_never_publish_a_partial_series(tmp_path: Path) -> None:
    source = FrozenSources()
    await source.add(frozen_files(tmp_path)[-1])
    source.rows.documents.pop(2)
    archive = MongoCotahistArchive(source.reader())
    with pytest.raises(SourceMalformedError, match="incomplete_mirrored_member"):
        await archive.year(2015)
    assert archive._years == {}


async def test_republication_is_pinned_for_the_whole_run(tmp_path: Path) -> None:
    source = FrozenSources()
    files = frozen_files(tmp_path)
    await source.add(files[-1], observed_at=datetime(2026, 1, 1, tzinfo=UTC))
    inputs = source.reader()
    before = await inputs.revision("COTAHIST", 2015)
    source.archives.documents.append(
        {
            **source.archives.documents[0],
            "artifact_id": "sha256:replacement",
            "observed_at": datetime(2026, 2, 1, tzinfo=UTC),
        }
    )
    assert await inputs.revision("COTAHIST", 2015) == before
    assert (
        await source.reader().revision("COTAHIST", 2015)
    ).artifact_id == "sha256:replacement"


@pytest.mark.skipif(
    not os.environ.get("SMAUG_TEST_MONGO_URI"),
    reason="Set SMAUG_TEST_MONGO_URI for isolated Mongo source parity",
)
async def test_real_mongo_admission_and_offline_queries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    files = frozen_files(tmp_path)
    expected = _reduce(files[-1])
    client = AsyncMongoClient(os.environ["SMAUG_TEST_MONGO_URI"])
    db = client[f"smaug_phase2_test_{uuid4().hex}"]
    try:
        await init_beanie(
            database=db,
            document_models=[SourceInputArchiveDocument, SourceInputRowDocument],
        )
        for path in files:
            await MirrorSourceInputs(MongoSourceInputRepository()).execute(
                LocalSourceInputReader(path)
            )
        inputs = MongoSourceInputs(db["source_input_archives"], db["source_input_rows"])
        await inputs.pin()
        deny_sources(monkeypatch)
        assert await MongoCotahistArchive(inputs).year(2015) == expected
        assert (
            await MongoCompanyRegistry(inputs, year=2026).resolve("PETR4")
        ).cd_cvm == "9512"
    finally:
        await client.drop_database(db.name)
        await client.close()


def frozen_raw() -> list[dict[str, Any]]:
    docs = []
    accounts = {
        "BPA": [
            ("1", "Ativo Total", "1000"),
            ("1.01", "Ativo Circulante", "400"),
            ("1.01.01", "Caixa e Equivalentes de Caixa", "100"),
        ],
        "BPP": [
            ("2.01", "Passivo Circulante", "200"),
            ("2.01.04", "Empréstimos e Financiamentos", "50"),
            ("2.02.01", "Empréstimos e Financiamentos", "150"),
            ("2.03", "Patrimônio Líquido Consolidado", "600"),
        ],
        "DRE": [
            ("3.01", "Receita de Venda de Bens e/ou Serviços", "900"),
            ("3.03", "Resultado Bruto", "300"),
            ("3.05", "Resultado Antes do Resultado Financeiro", "200"),
            ("3.11", "Lucro/Prejuízo Consolidado do Período", "120"),
        ],
        "DFC": [
            ("6.01", "Caixa Líquido Atividades Operacionais", "500"),
            ("6.01.01.04", "Depreciação e amortização", "80"),
            ("6.02.01", "Aquisição de Imobilizado", "-150"),
        ],
    }
    for reference in ("2015-03-31", "2015-06-30", "2015-09-30", "2015-12-31"):
        for module, values in accounts.items():
            docs.append(
                {
                    "ticker": "PETR4",
                    "source": "cvm",
                    "cvm_code": "9512",
                    "module": module,
                    "fetched_at": datetime(2016, 1, 1, tzinfo=UTC),
                    "payload": {
                        "reference_date": reference,
                        "document_type": "DFP"
                        if reference.endswith("12-31")
                        else "ITR",
                        "balance_type": "con",
                        "version": 1,
                        "period_start_date": "2015-01-01",
                        "accounts": [
                            {"code": code, "name": name, "quantity": quantity}
                            for code, name, quantity in values
                        ],
                    },
                }
            )
    docs.append(
        {
            "ticker": "PETR4",
            "source": "cvm",
            "cvm_code": "9512",
            "module": "CAPITAL",
            "fetched_at": datetime(2016, 1, 1, tzinfo=UTC),
            "payload": {
                "reference_date": "2015-12-31",
                "version": 1,
                "common_shares": 100,
                "preferred_shares": 100,
                "total_shares": 200,
                "approval_date": "2015-01-01",
            },
        }
    )
    return docs


async def run_composed_analysis(
    module: Any,
    source: FrozenSources,
    raw: SourceInputCollection,
    monkeypatch: pytest.MonkeyPatch,
    *,
    settings: Settings,
    repository: Any | None = None,
    engine: Any | None = None,
    database: Any | None = None,
) -> Any:
    from tests.fakes import RecordingAnalysisRepository

    store = repository or RecordingAnalysisRepository()

    class Client:
        def __getitem__(self, name: str) -> Any:
            if database is not None:
                return database
            return {
                "source_input_archives": source.archives,
                "source_input_rows": source.rows,
                "raw_ingestions": raw,
                "ingestion_validations": SourceInputCollection(),
            }

        async def close(self) -> None:
            pass

    class Engine:
        async def dispose(self) -> None:
            pass

    async def init(_: Any) -> Client:
        return Client()

    succession = module.CodeSuccession
    monkeypatch.setattr(
        module,
        "CodeSuccession",
        lambda *args, **kwargs: succession(
            *args, today=lambda: date(2015, 1, 6), **kwargs
        ),
    )
    analyze = module.AnalyzePortfolioUseCase
    monkeypatch.setattr(
        module,
        "AnalyzePortfolioUseCase",
        lambda *args, **kwargs: analyze(
            *args,
            clock=lambda: datetime(2016, 1, 7, tzinfo=UTC),
            id_factory=lambda: "frozen-reference",
            **kwargs,
        ),
    )
    monkeypatch.setattr(
        module,
        "B3PriceProvider",
        lambda archive: B3PriceProvider(archive, today=lambda: date(2015, 1, 6)),
    )
    monkeypatch.setattr(module, "get_settings", lambda: settings)
    monkeypatch.setattr(module, "init_database", init)
    monkeypatch.setattr(module, "create_engine", lambda _: engine or Engine())
    monkeypatch.setattr(module, "create_session_factory", lambda _: None)
    monkeypatch.setattr(module, "SqlAlchemyAnalysisRepository", lambda _: store)
    result = await module._run_analyze((), whole_exchange=True)
    assert result == 0
    return store


def analysis_contract(store: Any) -> dict[str, Any]:
    analyses = [canonical(item) for item in store.saved]
    outcomes = [canonical(item) for item in store.outcomes]
    for item in analyses + outcomes:
        for key in ("computed_at", "recorded_at", "run_id"):
            item.pop(key, None)
    return {"analyses": analyses, "outcomes": outcomes}


async def test_full_composed_analysis_matches_frozen_reference_without_sources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    expected = json.loads(
        Path("tests/fixtures/mongo_analysis_reference.json").read_text()
    )
    files = frozen_files(tmp_path)
    source = FrozenSources()
    for path in files:
        await source.add(path)
    raw = SourceInputCollection(frozen_raw())
    deny_sources(monkeypatch)
    store = await run_composed_analysis(
        cli, source, raw, monkeypatch, settings=Settings(cvm_fca_year=2026)
    )
    assert len(store.saved) == 4
    assert analysis_contract(store) == expected


@pytest.mark.skipif(
    not os.environ.get("SMAUG_TEST_MONGO_URI")
    or not os.environ.get("SMAUG_TEST_POSTGRES_URI"),
    reason="Set both database test URIs for isolated end-to-end persistence",
)
async def test_composed_offline_analysis_persists_and_reads_real_databases(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from smaug.analysis.infrastructure.sql_repository import (
        SqlAlchemyAnalysisRepository,
    )
    from smaug.analysis.infrastructure.sqlalchemy_models import Base
    from tests.fakes import RecordingAnalysisRepository

    files = frozen_files(tmp_path)
    client = AsyncMongoClient(os.environ["SMAUG_TEST_MONGO_URI"])
    db = client[f"smaug_phase2_e2e_{uuid4().hex}"]
    schema = "phase2_e2e_" + uuid4().hex
    admin = create_async_engine(os.environ["SMAUG_TEST_POSTGRES_URI"])
    engine = create_async_engine(
        os.environ["SMAUG_TEST_POSTGRES_URI"],
        connect_args={"server_settings": {"search_path": schema}},
    )
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    persisted = SqlAlchemyAnalysisRepository(sessions)

    class RecordingDatabase(RecordingAnalysisRepository):
        async def save(self, analysis: Any) -> None:
            await persisted.save(analysis)
            await super().save(analysis)

        async def save_outcome(self, outcome: Any) -> None:
            await persisted.save_outcome(outcome)
            await super().save_outcome(outcome)

    try:
        async with admin.begin() as conn:
            await conn.execute(text("CREATE SCHEMA " + schema))
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        await init_beanie(
            database=db,
            document_models=[SourceInputArchiveDocument, SourceInputRowDocument],
        )
        for path in files:
            await MirrorSourceInputs(MongoSourceInputRepository()).execute(
                LocalSourceInputReader(path)
            )
        await db["raw_ingestions"].insert_many(frozen_raw())
        expected = json.loads(
            Path("tests/fixtures/mongo_analysis_reference.json").read_text()
        )
        for path in files:
            path.unlink()
        deny_sources(monkeypatch)
        store = await run_composed_analysis(
            cli,
            FrozenSources(),
            SourceInputCollection(),
            monkeypatch,
            settings=Settings(cvm_fca_year=2026),
            repository=RecordingDatabase(),
            engine=engine,
            database=db,
        )
        assert analysis_contract(store) == expected
        for analysis in store.saved:
            rows = (
                await persisted.history(analysis.ticker)
                if analysis.view == "closed_year"
                else [await persisted.latest(analysis.ticker)]
            )
            assert analysis in rows
    finally:
        await client.drop_database(db.name)
        await client.close()
        await engine.dispose()
        async with admin.begin() as conn:
            await conn.execute(text("DROP SCHEMA IF EXISTS " + schema + " CASCADE"))
        await admin.dispose()


async def test_missing_historical_year_does_not_resolve_from_partial_names(
    tmp_path: Path,
) -> None:
    source = FrozenSources()
    for path in frozen_files(tmp_path):
        await source.add(path)
    source.archives.documents[:] = [
        doc
        for doc in source.archives.documents
        if (doc["dataset"], doc["year"]) != ("FCA", 2014)
    ]
    with pytest.raises(
        SourceNotFoundError, match="missing_mirrored_security_history:FCA:2014"
    ):
        await MongoSecurityHistory(source.reader(), through=2026).names()


async def test_mirrored_listing_matches_acquisition() -> None:
    from smaug.entrypoints.cli import _B3RegistrantAdapter
    from smaug.ingestion.infrastructure.b3_listed_company import (
        B3ListedCompany,
        B3ListedCompanyResolver,
    )
    from smaug.portfolio.infrastructure.mongo_listing import MongoRegistrantResolver

    payload = {
        "observed_at": "2026-10-01T00:00:00+00:00",
        "detail": {
            "codeCVM": "09512",
            "cnpj": "33.000.167/0001-01",
            "code": "PETR3",
            "isin": "BRPETRACNOR9",
            "dateQuotation": "01/01/2000",
            "market": "BOLSA",
            "otherCodes": [
                {"code": "PETR4", "isinCode": "BRPETRACNPR6"},
                {"code": "PETR"},
            ],
        },
        "supplement": {
            "codeCVM": "9512",
            "issuingCompany": "PETR",
            "tradingName": "PETROBRAS",
            "code": "PETR4",
            "quotedPerSharSince": "02/01/2000",
        },
    }
    company = B3ListedCompany(
        requested_root="PETR",
        issuing_company="PETR",
        trading_name="PETROBRAS",
        cvm_code="9512",
        supplement=payload["supplement"],
        detail=payload["detail"],
        quotation_date=date(2000, 1, 1),
    )

    class Resolver:
        async def resolve_by_cvm(self, *args: Any, **kwargs: Any) -> Any:
            return company

        def official_codes(self, value: Any) -> Any:
            return B3ListedCompanyResolver.official_codes(self, value)

    reference = await _B3RegistrantAdapter(Resolver()).resolve_by_cvm(
        "9512", cnpj="33.000.167/0001-01"
    )
    docs = SourceInputCollection(
        [
            {
                "source": "b3",
                "module": "LISTING_B3",
                "cvm_code": "9512",
                "payload": payload,
            }
        ]
    )
    actual = await MongoRegistrantResolver(docs).resolve_by_cvm(
        "9512", cnpj="33.000.167/0001-01"
    )
    assert actual == reference
    with pytest.raises(SourceMalformedError, match="mirrored_listing_cnpj_mismatch"):
        await MongoRegistrantResolver(docs).resolve_by_cvm(
            "9512", cnpj="00.000.000/0001-00"
        )
    with pytest.raises(SourceNotFoundError, match="missing_mirrored_listing"):
        await MongoRegistrantResolver(SourceInputCollection()).resolve_by_cvm("9512")


@pytest.mark.skipif(
    not os.environ.get("SMAUG_TEST_MONGO_URI"),
    reason="Set SMAUG_TEST_MONGO_URI for explicit preparation ingestion",
)
async def test_prepare_explicitly_mirrors_public_sources_before_analysis(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import runpy

    fixture = runpy.run_path("scripts/installer/source_fixture.py")
    client = AsyncMongoClient(os.environ["SMAUG_TEST_MONGO_URI"])
    db = client[f"smaug_phase2_prepare_{uuid4().hex}"]

    class Client:
        def __getitem__(self, _: str) -> Any:
            return db

        async def close(self) -> None:
            pass

    async def init(_: Any) -> Any:
        await init_beanie(
            database=db,
            document_models=[SourceInputArchiveDocument, SourceInputRowDocument],
        )
        return Client()

    original_http = httpx.AsyncClient
    monkeypatch.setattr(cli, "init_database", init)
    monkeypatch.setattr(
        cli,
        "get_settings",
        lambda: Settings(
            cvm_fca_year=2026, source_artifact_dir=str(tmp_path / "artifacts")
        ),
    )
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: original_http(
            transport=httpx.MockTransport(fixture["source"]), **kwargs
        ),
    )
    try:
        assert await cli._run_prepare_source_inputs() == 0
        count = await db["source_input_rows"].count_documents({})
        assert count > 0
        assert await cli._run_prepare_source_inputs() == 0
        assert await db["source_input_rows"].count_documents({}) == count
        inputs = MongoSourceInputs(db["source_input_archives"], db["source_input_rows"])
        monkeypatch.setattr(httpx, "AsyncClient", original_http)
        deny_sources(monkeypatch)
        assert (
            await MongoCompanyRegistry(inputs, year=2026).resolve("PETR4")
        ).cd_cvm == "9512"
        assert (await MongoCotahistArchive(inputs).year(2026))[
            "PETR4"
        ].last_close == Decimal("10")
    finally:
        await client.drop_database(db.name)
        await client.close()
