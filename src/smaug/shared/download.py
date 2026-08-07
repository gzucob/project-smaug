"""Resilient ZIP download, shared by every source that reads a published archive.

Each yearly archive is the step every ticker of a run shares, so a transient
network failure here is the worst possible place to give up (#16): the
download retries with backoff, writes atomically, and raises a typed error
the calling use case treats as fatal-with-log instead of a traceback.

It lives in ``shared`` because three contexts read a yearly ZIP off a public
server and none of them owns the plumbing: ``ingestion`` takes CVM's statements
and FRE, ``portfolio`` takes the FCA registry, and ``analysis`` takes B3's
COTAHIST price series. Importing it out of ``ingestion/infrastructure`` — which
``portfolio`` did — reaches into another context's internals, which
``RULES_LAYERS.md`` rules out.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import httpx

from smaug.shared.errors import CvmDownloadError
from smaug.shared.logging import get_logger

logger = get_logger(__name__)

# Backoff before the 2nd and 3rd attempts. CVM's server occasionally closes
# the connection mid-body (RemoteProtocolError); a plain re-try heals it.
_RETRY_DELAYS: tuple[float, ...] = (1.0, 3.0)

Sleeper = Callable[[float], Awaitable[None]]


@dataclass(frozen=True)
class DownloadResult:
    """HTTP metadata retained from an archive acquisition."""

    status_code: int
    etag: str | None
    last_modified: str | None


def _write_atomic(dst: Path, content: bytes) -> None:
    """Write to a sibling temp file and rename, so an interrupted run never
    leaves a truncated ZIP in the cache (a partial file would poison every
    later execution, which trusts ``dst.exists()``)."""
    tmp = dst.with_name(f".{dst.name}.{uuid4().hex}.part")
    try:
        tmp.write_bytes(content)
        tmp.replace(dst)
    finally:
        tmp.unlink(missing_ok=True)


async def download_zip(
    http: httpx.AsyncClient,
    url: str,
    dst: Path,
    *,
    follow_redirects: bool = False,
    sleep: Sleeper = asyncio.sleep,
    headers: Mapping[str, str] | None = None,
    allow_not_modified: bool = False,
) -> DownloadResult:
    """Fetch ``url`` into ``dst``, retrying transient failures, atomically.

    Transport errors (connection cut mid-body, timeouts) and 5xx are
    transient: retried with backoff. Any other non-200 is permanent — a 404
    means the year/document file does not exist — and fails immediately.
    Exhausted retries raise ``CvmDownloadError``.
    """
    attempts = len(_RETRY_DELAYS) + 1
    failure = "no attempt made"
    cause: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            response = await http.get(
                url,
                timeout=180.0,
                follow_redirects=follow_redirects,
                headers=headers,
            )
        except httpx.TransportError as exc:
            failure = f"transport error: {exc}"
            cause = exc
        else:
            if response.status_code == httpx.codes.OK:
                await asyncio.to_thread(_write_atomic, dst, response.content)
                return DownloadResult(
                    status_code=response.status_code,
                    etag=response.headers.get("ETag"),
                    last_modified=response.headers.get("Last-Modified"),
                )
            if allow_not_modified and response.status_code == httpx.codes.NOT_MODIFIED:
                return DownloadResult(
                    status_code=response.status_code,
                    etag=response.headers.get("ETag"),
                    last_modified=response.headers.get("Last-Modified"),
                )
            if response.status_code < httpx.codes.INTERNAL_SERVER_ERROR:
                raise CvmDownloadError(
                    f"HTTP {response.status_code} for {dst.name}: "
                    "not retryable (does the year/document exist?)"
                )
            failure = f"HTTP {response.status_code}"
            cause = None
        if attempt < attempts:
            delay = _RETRY_DELAYS[attempt - 1]
            logger.warning(
                "CVM download attempt %d/%d failed (%s); retrying in %.0fs",
                attempt,
                attempts,
                failure,
                delay,
            )
            await sleep(delay)
    raise CvmDownloadError(
        f"giving up on {dst.name} after {attempts} attempts ({failure})"
    ) from cause
