"""Immutable source archives: identity, metadata, reuse, and offline replay."""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from smaug.shared.artifacts import SourceArtifact
from smaug.shared.errors import CvmDownloadError
from smaug.shared.local_artifacts import LocalSourceArtifactStore
from tests.fakes import no_sleep

URL = "https://dados.cvm.gov.br/example/archive.zip"
NOW = datetime(2026, 8, 6, 12, 0, tzinfo=UTC)


def _zip_bytes(value: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("source.csv", value)
    return buffer.getvalue()


class _SequenceTransport(httpx.AsyncBaseTransport):
    def __init__(self, responses: Sequence[tuple[int, bytes, dict[str, str]]]) -> None:
        self._responses = iter(responses)
        self.requests: list[httpx.Request] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        status, content, headers = next(self._responses)
        return httpx.Response(status, content=content, headers=headers, request=request)


async def _acquire(
    root: Path,
    transport: httpx.AsyncBaseTransport,
) -> SourceArtifact:
    async with httpx.AsyncClient(transport=transport) as http:
        store = LocalSourceArtifactStore(http, root, clock=lambda: NOW, sleep=no_sleep)
        return await store.acquire(URL)


async def test_acquire_records_content_identity_and_http_metadata(
    tmp_path: Path,
) -> None:
    content = _zip_bytes("filed bytes")
    observed: list[SourceArtifact] = []

    async def observer(artifact: SourceArtifact) -> None:
        observed.append(artifact)

    transport = _SequenceTransport(
        [(200, content, {"ETag": '"v1"', "Last-Modified": "Wed, 05 Aug 2026"})]
    )
    async with httpx.AsyncClient(transport=transport) as http:
        store = LocalSourceArtifactStore(
            http,
            tmp_path / "sources",
            observer=observer,
            clock=lambda: NOW,
            sleep=no_sleep,
        )
        artifact = await store.acquire(URL)

    digest = hashlib.sha256(content).hexdigest()
    assert artifact.artifact_id == f"sha256:{digest}"
    assert artifact.byte_size == len(content)
    assert artifact.path.read_bytes() == content
    assert artifact.etag == '"v1"'
    assert artifact.last_modified == "Wed, 05 Aug 2026"
    assert artifact.downloaded_at == NOW
    assert observed == [artifact]

    observations = tuple((tmp_path / "sources" / "observations").glob("*.json"))
    assert len(observations) == 1
    recorded = json.loads(observations[0].read_text(encoding="utf-8"))
    assert recorded == {
        "artifact_id": f"sha256:{digest}",
        "byte_size": len(content),
        "downloaded_at": NOW.isoformat(),
        "etag": '"v1"',
        "http_status": 200,
        "last_modified": "Wed, 05 Aug 2026",
        "observed_at": NOW.isoformat(),
        "sha256": digest,
        "source_url": URL,
    }


async def test_identical_conditional_download_reuses_the_existing_artifact(
    tmp_path: Path,
) -> None:
    root = tmp_path / "sources"
    content = _zip_bytes("same")
    first = _SequenceTransport([(200, content, {"ETag": '"v1"'})])
    first_artifact = await _acquire(root, first)

    second = _SequenceTransport([(304, b"", {})])
    second_artifact = await _acquire(root, second)

    assert first_artifact.artifact_id == second_artifact.artifact_id
    assert second.requests[0].headers["If-None-Match"] == '"v1"'
    assert len(tuple((root / "sha256").glob("*/*.zip"))) == 1
    assert len(tuple((root / "observations").glob("*.json"))) == 2


async def test_changed_republication_preserves_both_artifacts(tmp_path: Path) -> None:
    root = tmp_path / "sources"
    first = await _acquire(
        root, _SequenceTransport([(200, _zip_bytes("old"), {"ETag": '"v1"'})])
    )
    second_transport = _SequenceTransport([(200, _zip_bytes("new"), {"ETag": '"v2"'})])
    second = await _acquire(root, second_transport)

    assert first.artifact_id != second.artifact_id
    assert first.path.is_file()
    assert second.path.is_file()
    assert len(tuple((root / "sha256").glob("*/*.zip"))) == 2


async def test_invalid_zip_never_enters_the_artifact_namespace(tmp_path: Path) -> None:
    root = tmp_path / "sources"
    transport = _SequenceTransport([(200, b"not a zip", {})])
    async with httpx.AsyncClient(transport=transport) as http:
        store = LocalSourceArtifactStore(http, root, sleep=no_sleep)
        with pytest.raises(CvmDownloadError, match="valid ZIP"):
            await store.acquire(URL)

    assert not tuple((root / "sha256").glob("*/*.zip"))
    assert not tuple((tmp_path / ".sources-staging").glob("*"))


async def test_open_replays_stored_content_without_network(tmp_path: Path) -> None:
    root = tmp_path / "sources"
    artifact = await _acquire(
        root, _SequenceTransport([(200, _zip_bytes("stored"), {})])
    )

    class _NoNetwork(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            raise AssertionError(f"unexpected network request: {request.url}")

    async with httpx.AsyncClient(transport=_NoNetwork()) as http:
        replay = LocalSourceArtifactStore(http, root)
        opened = await replay.open(artifact.artifact_id)

    assert opened.path == artifact.path
    assert opened.sha256 == artifact.sha256


async def test_open_rejects_unknown_or_malformed_identity(tmp_path: Path) -> None:
    async with httpx.AsyncClient() as http:
        store = LocalSourceArtifactStore(http, tmp_path / "sources")
        with pytest.raises(ValueError, match="invalid source artifact id"):
            await store.open("not-a-digest")
        with pytest.raises(FileNotFoundError, match="not found"):
            await store.open("sha256:" + "f" * 64)
