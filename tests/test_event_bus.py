"""In-process event bus."""

from dataclasses import dataclass

from smaug.shared.events import DomainEvent, EventBus


@dataclass(frozen=True)
class _Alpha(DomainEvent):
    value: int


@dataclass(frozen=True)
class _Beta(DomainEvent):
    pass


def test_should_deliver_event_to_matching_subscriber() -> None:
    bus = EventBus()
    seen: list[_Alpha] = []
    bus.subscribe(_Alpha, lambda event: seen.append(event))  # type: ignore[arg-type]

    bus.publish(_Alpha(1))

    assert seen == [_Alpha(1)]


def test_should_not_deliver_event_to_unrelated_subscriber() -> None:
    bus = EventBus()
    seen: list[DomainEvent] = []
    bus.subscribe(_Beta, lambda event: seen.append(event))

    bus.publish(_Alpha(1))

    assert seen == []
