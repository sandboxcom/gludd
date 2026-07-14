"""Structural tests for events/bus.py — EventBus publish-subscribe system."""

from __future__ import annotations

from general_ludd.events.bus import EventBus
from general_ludd.events.types import Event


def _make_event(name: str = "test.event") -> Event:
    return Event(type=name, payload={"msg": "hello"})


class TestEventBusInit:
    def test_default_construction(self):
        bus = EventBus()
        assert bus._subscribers == {}
        assert bus._history == []
        assert bus._history_size == 0
        assert isinstance(bus._next_id, int)

    def test_history_size_configured(self):
        bus = EventBus(history_size=42)
        assert bus._history_size == 42


class TestSubscribe:
    def test_returns_subscription_id(self):
        bus = EventBus()
        sub_id = bus.subscribe("test.event", lambda e: None)
        assert sub_id.startswith("sub-")

    def test_unique_ids(self):
        bus = EventBus()
        id1 = bus.subscribe("e1", lambda e: None)
        id2 = bus.subscribe("e2", lambda e: None)
        assert id1 != id2

    def test_multiple_subscribers_same_event(self):
        bus = EventBus()
        bus.subscribe("test.event", lambda e: None)
        bus.subscribe("test.event", lambda e: None)
        assert len(bus._subscribers["test.event"]) == 2


class TestUnsubscribe:
    def test_removes_subscriber(self):
        bus = EventBus()
        sub_id = bus.subscribe("test.event", lambda e: None)
        assert len(bus._subscribers["test.event"]) == 1
        bus.unsubscribe(sub_id)
        assert len(bus._subscribers["test.event"]) == 0

    def test_does_not_affect_other_subscribers(self):
        bus = EventBus()
        id1 = bus.subscribe("test.event", lambda e: None)
        bus.subscribe("test.event", lambda e: None)
        bus.unsubscribe(id1)
        assert len(bus._subscribers["test.event"]) == 1


class TestPublish:
    def test_delivers_to_subscriber(self):
        bus = EventBus()
        received: list[Event] = []
        bus.subscribe("test.event", received.append)
        count = bus.publish(_make_event("test.event"))
        assert count == 1
        assert len(received) == 1
        assert received[0].type == "test.event"

    def test_returns_delivered_count(self):
        bus = EventBus()
        bus.subscribe("test.event", lambda e: None)
        bus.subscribe("test.event", lambda e: None)
        count = bus.publish(_make_event("test.event"))
        assert count == 2

    def test_only_delivers_to_matching_event_type(self):
        bus = EventBus()
        received: list[Event] = []
        bus.subscribe("other.event", received.append)
        count = bus.publish(_make_event("test.event"))
        assert count == 0
        assert len(received) == 0

    def test_wildcard_subscriber_receives_all(self):
        bus = EventBus()
        received: list[Event] = []
        bus.subscribe("*", received.append)
        count = bus.publish(_make_event("any.event"))
        assert count == 1
        assert len(received) == 1

    def test_subscriber_exception_does_not_propagate(self):
        bus = EventBus()
        bus.subscribe("test.event", lambda e: 1 / 0)  # type: ignore[arg-type]
        try:
            bus.publish(_make_event("test.event"))
        except ZeroDivisionError:
            raise AssertionError(
                "publish() should not propagate subscriber exceptions"
            ) from None

    def test_history_captured_when_enabled(self):
        bus = EventBus(history_size=3)
        for i in range(5):
            bus.publish(Event(type="e", payload={"i": i}))
        history = bus.get_history()
        assert len(history) == 3
        assert history[0].payload == {"i": 2}  # type: ignore[index]


class TestGetHistory:
    def test_returns_copy(self):
        bus = EventBus(history_size=5)
        bus.publish(_make_event())
        h1 = bus.get_history()
        h2 = bus.get_history()
        assert h1 == h2
        assert h1 is not h2


class TestClear:
    def test_clears_subscribers(self):
        bus = EventBus()
        bus.subscribe("test.event", lambda e: None)
        bus.clear()
        assert bus._subscribers == {}
