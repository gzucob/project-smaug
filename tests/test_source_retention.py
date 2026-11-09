"""Installation-only cleanup, durable metadata and interrupted consumer recovery."""

import asyncio
import io
import os
import runpy
import shutil
import zipfile
from itertools import islice
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import pytest
from beanie import init_beanie
from pymongo import AsyncMongoClient

from smaug.analysis.infrastructure.b3_prices import _reduce
from smaug.analysis.infrastructure.mongo_prices import MongoCotahistArchive
from smaug.entrypoints import cli
from smaug.ingestion.application.source_inputs import MirrorSourceInputs
from smaug.ingestion.infrastructure.models import (
    SourceAcquisitionDocument,
    SourceFileSessionDocument,
    SourceInputArchiveDocument,
    SourceInputRowDocument,
)
from smaug.ingestion.infrastructure.source_inputs import (
    LocalSourceInputReader,
    MongoSourceInputRepository,
)
from smaug.ingestion.infrastructure.source_retention import InstallationSourceSession
from smaug.shared.config import Settings
from smaug.shared.errors import SourceError
from smaug.shared.local_artifacts import LocalSourceArtifactStore
from smaug.shared.source_inputs import MongoSourceInputs
from tests.test_source_inputs import make_archive, tape_archive

pytestmark = pytest.mark.skipif(
    not os.environ.get("SMAUG_TEST_MONGO_URI"),
    reason="Set SMAUG_TEST_MONGO_URI for isolated source retention tests",
)

_MODELS = [
    SourceAcquisitionDocument,
    SourceFileSessionDocument,
    SourceInputArchiveDocument,
    SourceInputRowDocument,
]
_URL = "https://bvmf.bmfbovespa.com.br/InstDados/SerHist/COTAHIST_A2015.ZIP"
_SCOPE = "a" * 64


def installed_settings(tmp_path: Path) -> Settings:
    return Settings(
        runtime_context="installation",
        source_artifact_dir=str(tmp_path / "installed-sources"),
        cvm_cache_dir=str(tmp_path / "legacy-cvm"),
        b3_cache_dir=str(tmp_path / "legacy-b3"),
    )


async def test_cleanup_waits_for_all_consumers_and_preserves_queryable_source_facts(
    tmp_path: Path,
) -> None:
    path = tape_archive(tmp_path)
    body = path.read_bytes()
    expected = _reduce(path)
    path.unlink()
    client = AsyncMongoClient(os.environ["SMAUG_TEST_MONGO_URI"])
    db = client["source_retention_" + uuid4().hex]
    try:
        await init_beanie(database=db, document_models=_MODELS)
        settings = installed_settings(tmp_path)
        async with InstallationSourceSession(
            settings, db, scope=_SCOPE, operation="ingest"
        ) as session:
            async with httpx.AsyncClient(
                transport=httpx.MockTransport(
                    lambda _: httpx.Response(
                        200, content=body, headers={"ETag": '"v1"'}
                    )
                )
            ) as http:
                store = LocalSourceArtifactStore(
                    http,
                    session.settings.source_artifact_dir,
                    observations=session.observations,
                )
                artifact = await store.acquire(_URL)
                await MirrorSourceInputs(MongoSourceInputRepository()).execute(
                    LocalSourceInputReader(artifact.path, artifact=artifact)
                )
                assert artifact.path.exists()
                # Another adapter can still open the same immutable file after
                # raw admission, until every semantic consumer has finished.
                assert (
                    await store.open(artifact.artifact_id)
                ).path.read_bytes() == body
                copies = [
                    Path(session.settings.cvm_cache_dir) / "COTAHIST_A2015.ZIP",
                    Path(session.settings.b3_cache_dir) / "COTAHIST_A2015.ZIP",
                ]
                for copy in copies:
                    copy.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(artifact.path, copy)
                (copies[0].parent / "extracted.csv").write_text("source rows")
                (copies[1].parent / "cotahist_2015.json").write_text(
                    "financial reduction"
                )
                await session.complete()
                assert not session.workspace.exists()
                assert not artifact.path.exists()
                assert all(not copy.exists() for copy in copies)
        row = await db.source_file_sessions.find_one({"scope": _SCOPE})
        assert row["state"] == "cleaned"
        assert row["artifact_ids"] == [artifact.artifact_id]
        assert (await db.source_acquisitions.find_one({"kind": "acquisition"}))[
            "metadata"
        ]["etag"] == '"v1"'
        inputs = MongoSourceInputs(db.source_input_archives, db.source_input_rows)
        assert await MongoCotahistArchive(inputs).year(2015) == expected
        assert not list(Path(settings.source_artifact_dir).rglob("*.ZIP"))
    finally:
        await client.drop_database(db.name)
        await client.close()


@pytest.mark.parametrize("interruption", [RuntimeError, asyncio.CancelledError])
async def test_failed_consumers_keep_files_even_when_raw_admission_finished(
    tmp_path: Path, interruption: type[BaseException]
) -> None:
    body = tape_archive(tmp_path).read_bytes()
    client = AsyncMongoClient(os.environ["SMAUG_TEST_MONGO_URI"])
    db = client["retention_failure_" + uuid4().hex]
    try:
        await init_beanie(database=db, document_models=_MODELS)
        settings = installed_settings(tmp_path)
        session = InstallationSourceSession(
            settings, db, scope=_SCOPE, operation="ingest"
        )
        cached = Path(session.settings.b3_cache_dir) / "COTAHIST_A2015.ZIP"

        async def fail_consumer() -> None:
            async with session:
                cached.parent.mkdir(parents=True)
                cached.write_bytes(body)
                await MirrorSourceInputs(MongoSourceInputRepository()).execute(
                    LocalSourceInputReader(cached)
                )
                await session.record_run("failed-run")
                raise interruption("semantic consumer unfinished")

        with pytest.raises(interruption):
            await fail_consumer()
        assert cached.exists()
        assert (await db.source_file_sessions.find_one({"scope": _SCOPE}))[
            "state"
        ] == "failed"
        # Resume by ingestion run id, even when the CLI request has a different scope.
        async with InstallationSourceSession(
            settings, db, scope="b" * 64, operation="resume", resume_run_id="failed-run"
        ) as resumed:
            assert resumed.workspace == session.workspace
            assert cached.exists()
            await resumed.complete()
        assert not cached.exists()
        assert (await db.source_file_sessions.find_one({"scope": _SCOPE}))[
            "attempts"
        ] == 2
    finally:
        await client.drop_database(db.name)
        await client.close()


async def test_partial_persistence_is_not_admitted_and_retries_without_losing_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = tape_archive(tmp_path).read_bytes()
    client = AsyncMongoClient(os.environ["SMAUG_TEST_MONGO_URI"])
    db = client["retention_partial_" + uuid4().hex]
    try:
        await init_beanie(database=db, document_models=_MODELS)
        settings = installed_settings(tmp_path)
        original = MongoSourceInputRepository.write_rows

        async def interrupted(repository: Any, archive: Any, rows: Any) -> None:
            await original(repository, archive, islice(rows, 2))
            raise RuntimeError("write interrupted")

        monkeypatch.setattr(MongoSourceInputRepository, "write_rows", interrupted)
        session = InstallationSourceSession(
            settings, db, scope=_SCOPE, operation="ingest"
        )
        path = Path(session.settings.b3_cache_dir) / "COTAHIST_A2015.ZIP"

        async def fail_operation() -> None:
            async with session:
                path.parent.mkdir(parents=True)
                path.write_bytes(body)
                await session.complete()

        with pytest.raises(RuntimeError, match="write interrupted"):
            await fail_operation()
        assert path.exists()
        assert await db.source_input_archives.count_documents({}) == 0
        assert await db.source_input_rows.count_documents({}) == 2
        monkeypatch.setattr(MongoSourceInputRepository, "write_rows", original)
        async with InstallationSourceSession(
            settings, db, scope=_SCOPE, operation="ingest"
        ) as resumed:
            await resumed.complete()
        assert not path.exists()
        assert await db.source_input_archives.count_documents({}) == 1
        assert await db.source_input_rows.count_documents({}) > 2
    finally:
        await client.drop_database(db.name)
        await client.close()


async def test_interrupted_cleanup_keeps_a_durable_consumer_checkpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = AsyncMongoClient(os.environ["SMAUG_TEST_MONGO_URI"])
    db = client["retention_cleanup_" + uuid4().hex]
    try:
        await init_beanie(database=db, document_models=_MODELS)
        settings = installed_settings(tmp_path)
        original = InstallationSourceSession._remove_workspace

        async def interrupted(_: Any) -> None:
            raise OSError("disk unavailable")

        monkeypatch.setattr(InstallationSourceSession, "_remove_workspace", interrupted)
        session = InstallationSourceSession(
            settings, db, scope=_SCOPE, operation="ingest"
        )
        path = session.workspace / "COTAHIST_A2015.ZIP"

        async def fail_operation() -> None:
            async with session:
                tape_archive(session.workspace)
                await session.complete()

        with pytest.raises(OSError, match="disk unavailable"):
            await fail_operation()
        assert path.exists()
        row = await db.source_file_sessions.find_one({"scope": _SCOPE})
        assert row["state"] == "consumers_complete"
        ids = row["artifact_ids"]
        monkeypatch.setattr(InstallationSourceSession, "_remove_workspace", original)
        async with InstallationSourceSession(
            settings, db, scope=_SCOPE, operation="ingest"
        ) as resumed:
            assert not path.exists()
            await resumed.complete()
        assert (await db.source_file_sessions.find_one({"scope": _SCOPE}))[
            "artifact_ids"
        ] == ids
    finally:
        await client.drop_database(db.name)
        await client.close()


async def test_file_lock_prevents_cleaning_another_active_consumer(
    tmp_path: Path,
) -> None:
    client = AsyncMongoClient(os.environ["SMAUG_TEST_MONGO_URI"])
    db = client["retention_lock_" + uuid4().hex]
    try:
        settings = installed_settings(tmp_path)
        async with InstallationSourceSession(
            settings, db, scope=_SCOPE, operation="first"
        ) as first:
            source = first.workspace / "held.csv"
            source.write_text("unpersisted facts")
            with pytest.raises(SourceError, match="source_ingestion_already_running"):
                async with InstallationSourceSession(
                    settings, db, scope="b" * 64, operation="second"
                ):
                    pytest.fail("another consumer acquired the source lock")
            assert source.exists()
        assert source.exists()
    finally:
        await client.drop_database(db.name)
        await client.close()


async def test_conditional_http_and_republication_survive_cleanup(
    tmp_path: Path,
) -> None:
    body = tape_archive(tmp_path).read_bytes()
    with zipfile.ZipFile(io.BytesIO(body)) as archive:
        records = archive.read("COTAHIST_A2015.TXT").decode("latin-1").splitlines()
    records[1] = records[1][:108] + "0000000001234" + records[1][121:]
    changed = make_archive(
        tmp_path / "changed.zip", {"COTAHIST_A2015.TXT": "\n".join(records)}
    ).read_bytes()
    version = [body]
    requests = []

    def source(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.headers.get("if-none-match") == '"v1"':
            return httpx.Response(304)
        return httpx.Response(200, content=version[0], headers={"ETag": '"v1"'})

    client = AsyncMongoClient(os.environ["SMAUG_TEST_MONGO_URI"])
    db = client["retention_http_" + uuid4().hex]
    try:
        await init_beanie(database=db, document_models=_MODELS)
        settings = installed_settings(tmp_path)
        for attempt in range(3):
            if attempt == 2:
                version[0] = changed
            async with InstallationSourceSession(
                settings, db, scope=_SCOPE, operation="ingest"
            ) as session:
                async with httpx.AsyncClient(
                    transport=httpx.MockTransport(source)
                ) as http:
                    store = LocalSourceArtifactStore(
                        http,
                        session.settings.source_artifact_dir,
                        observations=session.observations,
                    )
                    await store.acquire(_URL)
                    await session.complete()
        assert requests[1].headers["if-none-match"] == '"v1"'
        assert "if-none-match" not in requests[2].headers
        assert await db.source_input_archives.count_documents({}) == 2
        assert (
            await db.source_acquisitions.count_documents({"kind": "acquisition"}) == 3
        )
    finally:
        await client.drop_database(db.name)
        await client.close()


async def test_installed_cli_prepares_sources_and_cleans_only_its_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = runpy.run_path("scripts/installer/source_fixture.py")
    client = AsyncMongoClient(os.environ["SMAUG_TEST_MONGO_URI"])
    db = client["retention_cli_" + uuid4().hex]
    settings = installed_settings(tmp_path).model_copy(update={"mongo_db": db.name})
    # Unrelated development files remain outside the operation's private workspace.
    original = Path(settings.source_artifact_dir) / "dev-kept.zip"
    original.parent.mkdir(parents=True)
    original.write_bytes(b"development bytes")

    class Client:
        def __getitem__(self, _: str) -> Any:
            return db

        async def close(self) -> None:
            pass

    async def init(_: Any) -> Any:
        await init_beanie(database=db, document_models=_MODELS)
        return Client()

    original_http = httpx.AsyncClient
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    monkeypatch.setattr(cli, "init_database", init)
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: original_http(
            transport=httpx.MockTransport(fixture["source"]), **kwargs
        ),
    )
    try:
        assert await cli._run_prepare_source_inputs() == 0
        session = await db.source_file_sessions.find_one({})
        assert session["state"] == "cleaned"
        assert session["artifact_ids"]
        assert original.read_bytes() == b"development bytes"
        assert not list(
            (Path(settings.source_artifact_dir) / ".ingestion").rglob("*.zip")
        )
        assert not list(
            (Path(settings.source_artifact_dir) / ".ingestion").rglob("*.csv")
        )
        assert not list(
            (Path(settings.source_artifact_dir) / ".ingestion").rglob("*.json")
        )
        inputs = MongoSourceInputs(db.source_input_archives, db.source_input_rows)
        assert (await MongoCotahistArchive(inputs).year(2026))["PETR4"].last_close == 10
    finally:
        await client.drop_database(db.name)
        await client.close()


async def test_sigkill_releases_lock_and_resumes_an_unadmitted_archive(
    tmp_path: Path,
) -> None:
    import sys

    body = tape_archive(tmp_path).read_bytes()
    client = AsyncMongoClient(os.environ["SMAUG_TEST_MONGO_URI"])
    db = client["retention_kill_" + uuid4().hex]
    settings = installed_settings(tmp_path)
    script = """
import asyncio, os, sys
from itertools import islice
from pathlib import Path
from pymongo import AsyncMongoClient
from smaug.shared.config import Settings
from smaug.ingestion.infrastructure.source_retention import InstallationSourceSession
from smaug.ingestion.infrastructure.source_inputs import (
    LocalSourceInputReader, MongoSourceInputRepository,
)
async def main():
    client = AsyncMongoClient(os.environ["SMAUG_TEST_MONGO_URI"])
    db = client[sys.argv[1]]
    settings = Settings(runtime_context="installation", source_artifact_dir=sys.argv[2])
    session = InstallationSourceSession(settings, db, scope="a"*64, operation="ingest")
    await session.__aenter__()
    path = session.workspace / "COTAHIST_A2015.ZIP"
    path.write_bytes(Path(sys.argv[3]).read_bytes())
    reader = LocalSourceInputReader(path)
    archive = await reader.inspect()
    repo = MongoSourceInputRepository(db.source_input_archives, db.source_input_rows)
    await repo.write_rows(archive, islice(reader.rows(), 2))
    reader.close()
    print("source held", flush=True)
    await asyncio.Event().wait()
asyncio.run(main())
"""
    child = None
    try:
        await init_beanie(database=db, document_models=_MODELS)
        child = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            script,
            db.name,
            settings.source_artifact_dir,
            str(tmp_path / "COTAHIST_A2015.ZIP"),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        assert (
            await asyncio.wait_for(child.stdout.readline(), timeout=15)
            == b"source held\n"
        )
        assert await db.source_input_archives.count_documents({}) == 0
        child.kill()
        await child.wait()
        row = await db.source_file_sessions.find_one({"scope": _SCOPE})
        assert row["state"] == "active"
        async with InstallationSourceSession(
            settings, db, scope=_SCOPE, operation="ingest"
        ) as resumed:
            assert (resumed.workspace / "COTAHIST_A2015.ZIP").read_bytes() == body
            await resumed.complete()
        assert await db.source_input_archives.count_documents({}) == 1
        assert (await db.source_file_sessions.find_one({"scope": _SCOPE}))[
            "state"
        ] == "cleaned"
    finally:
        if child is not None and child.returncode is None:
            child.kill()
            await child.wait()
        await client.drop_database(db.name)
        await client.close()


async def test_legacy_ingestion_tape_records_http_metadata_and_releases_reduction(
    tmp_path: Path,
) -> None:
    body = tape_archive(tmp_path).read_bytes()
    client = AsyncMongoClient(os.environ["SMAUG_TEST_MONGO_URI"])
    db = client["retention_tape_" + uuid4().hex]
    try:
        await init_beanie(database=db, document_models=_MODELS)
        async with InstallationSourceSession(
            installed_settings(tmp_path), db, scope=_SCOPE, operation="ingest"
        ) as session:
            token = cli._source_session.set(session)
            try:
                async with httpx.AsyncClient(
                    transport=httpx.MockTransport(
                        lambda _: httpx.Response(
                            200, content=body, headers={"ETag": '"tape"'}
                        )
                    )
                ) as http:
                    archive = cli._build_ingestion_archive(session.settings, http)
                    assert (await archive.year(2015))["PETR4"].sessions == 3
                cache = Path(session.settings.b3_cache_dir)
                assert (cache / "COTAHIST_A2015.ZIP").exists()
                assert (cache / "cotahist_2015.json").exists()
                assert await db.source_input_archives.count_documents({}) == 1
                record = await db.source_acquisitions.find_one({})
                assert record["metadata"]["etag"] == '"tape"'
                assert record["metadata"]["http_status"] == 200
                await session.complete()
                assert not cache.exists()
            finally:
                cli._source_session.reset(token)
    finally:
        await client.drop_database(db.name)
        await client.close()


async def test_cancelled_cleanup_keeps_lock_until_filesystem_worker_finishes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import threading

    from smaug.ingestion.infrastructure import source_retention

    started = threading.Event()
    release = threading.Event()
    original = source_retention.shutil.rmtree

    def slow_remove(path: Path) -> None:
        started.set()
        assert release.wait(timeout=10)
        original(path)

    client = AsyncMongoClient(os.environ["SMAUG_TEST_MONGO_URI"])
    db = client["retention_cancel_" + uuid4().hex]
    task = None
    try:
        await init_beanie(database=db, document_models=_MODELS)
        settings = installed_settings(tmp_path)
        monkeypatch.setattr(source_retention.shutil, "rmtree", slow_remove)

        async def cleanup() -> None:
            async with InstallationSourceSession(
                settings, db, scope=_SCOPE, operation="ingest"
            ) as session:
                tape_archive(session.workspace)
                await session.complete()

        task = asyncio.create_task(cleanup())
        assert await asyncio.to_thread(started.wait, 5)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        with pytest.raises(SourceError, match="source_ingestion_already_running"):
            async with InstallationSourceSession(
                settings, db, scope="b" * 64, operation="other"
            ):
                pytest.fail("cleanup worker lost ownership")
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        monkeypatch.setattr(source_retention.shutil, "rmtree", original)
        async with InstallationSourceSession(
            settings, db, scope=_SCOPE, operation="ingest"
        ) as resumed:
            await resumed.complete()
    finally:
        release.set()
        if task is not None and not task.done():
            await asyncio.gather(task, return_exceptions=True)
        await client.drop_database(db.name)
        await client.close()
