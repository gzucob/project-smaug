"""Regression tests for the shared archive downloader."""

from pathlib import Path

import httpx
import pytest

from smaug.shared.download import DownloadResult, download_zip
from smaug.shared.errors import CvmDownloadError

URL = "https://example.test/archive.zip"


async def test_transient_download_failures_retry_and_publish_complete_archive(
    tmp_path: Path,
) -> None:
    path = tmp_path / "archive.zip"
    path.write_bytes(b"previous archive")
    delays: list[float] = []
    attempts = 0

    async def wait(delay: float) -> None:
        assert path.read_bytes() == b"previous archive"
        delays.append(delay)

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        assert request.headers["If-None-Match"] == '"previous"'
        if attempts == 1:
            raise httpx.ReadError("connection interrupted", request=request)
        if attempts == 2:
            return httpx.Response(503)
        return httpx.Response(200, content=b"new archive", headers={"ETag": '"new"'})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        result = await download_zip(
            client, URL, path, sleep=wait, headers={"If-None-Match": '"previous"'}
        )

    assert delays == [1, 3]
    assert result == DownloadResult(200, '"new"', None)
    assert path.read_bytes() == b"new archive"
    assert list(tmp_path.iterdir()) == [path]


async def test_conditional_download_keeps_cached_archive(tmp_path: Path) -> None:
    path = tmp_path / "archive.zip"
    path.write_bytes(b"cached archive")
    transport = httpx.MockTransport(lambda _: httpx.Response(304))
    async with httpx.AsyncClient(transport=transport) as client:
        result = await download_zip(client, URL, path, allow_not_modified=True)
    assert result == DownloadResult(304, None, None)
    assert path.read_bytes() == b"cached archive"


@pytest.mark.parametrize(
    ("status", "attempts", "message"),
    [(404, 1, "not retryable"), (503, 3, "giving up")],
)
async def test_failed_download_leaves_no_archive(
    tmp_path: Path, status: int, attempts: int, message: str
) -> None:
    requests: list[httpx.Request] = []
    delays: list[float] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(status)

    async def wait(delay: float) -> None:
        delays.append(delay)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(CvmDownloadError, match=message):
            await download_zip(client, URL, tmp_path / "archive.zip", sleep=wait)
    assert len(requests) == attempts
    assert delays == ([1, 3] if attempts == 3 else [])
    assert not list(tmp_path.iterdir())
