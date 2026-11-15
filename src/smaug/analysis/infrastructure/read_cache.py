"""Bounded, reader-owned memoization for a single analysis input snapshot."""

from collections import OrderedDict
from collections.abc import Awaitable, Callable, Hashable


class AsyncReadCache[K: Hashable, V]:
    """Retain successful reads only, including resolved absences, for this reader."""

    def __init__(self, capacity: int = 16) -> None:
        if capacity < 1:
            raise ValueError("read cache capacity must be positive")
        self._capacity = capacity
        self._values: OrderedDict[K, V] = OrderedDict()

    async def get(self, key: K, read: Callable[[], Awaitable[V]]) -> V:
        if key in self._values:
            self._values.move_to_end(key)
            return self._values[key]
        # Failed or cancelled reads must never become cached source evidence.
        value = await read()
        self._values[key] = value
        if len(self._values) > self._capacity:
            self._values.popitem(last=False)
        return value
