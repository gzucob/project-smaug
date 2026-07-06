"""In-process, synchronous event bus and the domain-event base type.

Phase 1 has *no* subscribers — the bus exists so Phase 2 (analysis) can
subscribe later without ingestion ever knowing it is there. Locking the
pattern now keeps the contexts at zero coupling.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class DomainEvent:
    """Base class for domain events. Frozen: events are facts, immutable."""


EventHandler = Callable[[DomainEvent], None]


class EventBus:
    """Minimal synchronous publish/subscribe registry."""

    def __init__(self) -> None:
        self._handlers: dict[type[DomainEvent], list[EventHandler]] = defaultdict(list)

    def subscribe(self, event_type: type[DomainEvent], handler: EventHandler) -> None:
        """Register ``handler`` to receive events of ``event_type``."""
        self._handlers[event_type].append(handler)

    def publish(self, event: DomainEvent) -> None:
        """Deliver ``event`` to every handler subscribed to its exact type."""
        for handler in self._handlers[type(event)]:
            handler(event)
