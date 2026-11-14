"""Reader-local evidence survives reuse without retaining failures or other runs."""

import asyncio

import pytest

from smaug.analysis.infrastructure.read_cache import AsyncReadCache


async def test_reader_cache_retains_named_absence_with_bounded_lifetime() -> None:
    cache = AsyncReadCache[str, int | None](capacity=2)
    calls = 0

    async def read() -> int | None:
        nonlocal calls
        calls += 1
        return None

    assert await cache.get("a", read) is None
    assert await cache.get("b", read) is None
    assert await cache.get("a", read) is None
    assert calls == 2
    await cache.get("c", read)
    await cache.get("b", read)
    assert calls == 4
    # A new analysis reader must query the newly selected source snapshot.
    await AsyncReadCache[str, int | None]().get("a", read)
    assert calls == 5


@pytest.mark.parametrize("error", [RuntimeError, asyncio.CancelledError])
async def test_failed_or_cancelled_source_read_is_retried(
    error: type[BaseException],
) -> None:
    cache = AsyncReadCache[str, int]()

    async def failed() -> int:
        raise error()

    async def recovered() -> int:
        return 123

    with pytest.raises(error):
        await cache.get("source", failed)
    assert await cache.get("source", recovered) == 123
