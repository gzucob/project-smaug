"""Coordinate source synchronization and one atomic derived publication."""

import logging
from collections.abc import Awaitable, Callable
from time import perf_counter

from smaug.analysis.domain.incremental import RefreshCheckpoint, RefreshScope

logger = logging.getLogger(__name__)


async def refresh_incrementally(
    previous: RefreshCheckpoint,
    *,
    sync_prices: Callable[[], Awaitable[int]],
    sync_fundamentals: Callable[[], Awaitable[int]] | None,
    checkpoint: Callable[[], Awaitable[RefreshCheckpoint]],
    changes: Callable[[RefreshCheckpoint, RefreshCheckpoint], Awaitable[RefreshScope]],
    has_unfinished_disclosures: Callable[[], Awaitable[bool]],
    publish: Callable[[RefreshCheckpoint, RefreshScope], Awaitable[int]],
) -> int:
    """Only publish inputs that completed ingestion, retaining cursors on error."""
    started = perf_counter()
    price_status = await _sync("B3", sync_prices)
    fundamentals_status = (
        await _sync("CVM", sync_fundamentals) if sync_fundamentals is not None else 0
    )
    current = await checkpoint()
    scope = await changes(previous, current)
    if (price_status and scope.compare_prices) or await has_unfinished_disclosures():
        logger.warning("Publication deferred until the source mirror is complete")
        return 1
    result = await publish(current, scope)
    logger.info(
        "Incremental publication finished in %.2fs (changed issuers=%d, B3=%s)",
        perf_counter() - started,
        len(scope.issuers),
        scope.compare_prices,
    )
    return int(bool(price_status or fundamentals_status or result))


async def _sync(source: str, run: Callable[[], Awaitable[int]]) -> int:
    try:
        return await run()
    except Exception:
        logger.exception("%s background synchronization failed", source)
        return 1
