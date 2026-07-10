"""Resilient ZIP download shared by the CVM sources (statements and FRE).

Each yearly archive is the step every ticker of a run shares, so a transient
network failure here is the worst possible place to give up (#16): the
download retries with backoff, writes atomically, and raises a typed error
the ingestion use case treats as fatal-with-log instead of a traceback.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path

import httpx

from smaug.shared.errors import CvmDownloadError
from smaug.shared.logging import get_logger

logger = get_logger(__name__)

# Backoff before the 2nd and 3rd attempts. CVM's server occasionally closes
# the connection mid-body (RemoteProtocolError); a plain re-try heals it.
_RETRY_DELAYS: tuple[float, ...] = (1.0, 3.0)

Sleeper = Callable[[float], Awaitable[None]]


def _write_atomic(dst: Path, content: bytes) -> None:
    """Write to a sibling temp file and rename, so an interrupted run never
    leaves a truncated ZIP in the cache (a partial file would poison every
    later execution, which trusts ``dst.exists()``)."""
    tmp = dst.with_suffix(".part")
    tmp.write_bytes(content)
    tmp.replace(dst)


async def download_zip(
    http: httpx.AsyncClient,
    url: str,
    dst: Path,
    *,
    follow_redirects: bool = False,
    sleep: Sleeper = asyncio.sleep,
) -> None:
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
                url, timeout=180.0, follow_redirects=follow_redirects
            )
        except httpx.TransportError as exc:
            failure = f"transport error: {exc}"
            cause = exc
        else:
            if response.status_code == httpx.codes.OK:
                await asyncio.to_thread(_write_atomic, dst, response.content)
                return
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
