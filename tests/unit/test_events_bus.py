"""Structural tests for events/bus.py — EventBus."""

from __future__ import annotations

from general_ludd.events.bus import EventBus
from general_ludd.events.types import Event, EventType


class TestEventBus:
    def test_default_constructor(self):
        bus = EventBus()
        assert bus._history_size == 0
        assert bus._next_id == 0
        assert isinstance(bus._subscribers, dict)
        assert isinstance(bus._history, list)
        assert isinstance(bus._background_tasks, set)

    def test_constructor_with_history(self):
        bus = EventBus(history_size=10)
        assert bus._history_size == 10

    def test_subscribe_returns_id_and_increments(self):
        bus = EventBus()
        sid1 = bus.subscribe(EventType.MODEL_ADDED, lambda e: None)
        sid2 = bus.subscribe(EventType.MODEL_ADDED, lambda e: None)
        assert sid1 == "sub-0"
        assert sid2 == "sub-1"

    def test_subscribe_with_str_type(self):
        bus = EventBus()
        sid = bus.subscribe("custom_type", lambda e: None)
        assert sid == "sub-0"

    def test_subscribe_wildcard(self):
        bus = EventBus()
        sid = bus.subscribe("*", lambda e: None)
        assert sid == "sub-0"

    def test_unsubscribe_removes_subscriber(self):
        bus = EventBus()
        sid = bus.subscribe(EventType.MODEL_ADDED, lambda e: None)
        bus.unsubscribe(sid)
        event = Event(type=EventType.MODEL_ADDED, payload={"model_id": "x"})
        delivered = bus.publish(event)
        assert delivered == 0

    def test_publish_delivers_to_matching_subscriber(self):
        bus = EventBus()
        received: list[Event] = []
        bus.subscribe(EventType.MODEL_ADDED, received.append)
        event = Event(type=EventType.MODEL_ADDED, payload={"model_id": "x"})
        delivered = bus.publish(event)
        assert delivered == 1
        assert len(received) == 1
        assert received[0] is event

    def test_publish_delivers_to_matching_and_wildcard(self):
        bus = EventBus()
        received: list[Event] = []
        bus.subscribe(EventType.MODEL_ADDED, received.append)
        bus.subscribe("*", received.append)
        event = Event(type=EventType.MODEL_ADDED, payload={"model_id": "x"})
        delivered = bus.publish(event)
        assert delivered == 2
        assert len(received) == 2

    def test_publish_skips_non_matching_type(self):
        bus = EventBus()
        received: list[Event] = []
        bus.subscribe(EventType.MODEL_ADDED, received.append)
        event = Event(type=EventType.MODEL_REMOVED, payload={"model_id": "x"})
        delivered = bus.publish(event)
        assert delivered == 0
        assert len(received) == 0

    def test_publish_with_str_type_event(self):
        bus = EventBus()
        received: list[Event] = []
        bus.subscribe("custom", received.append)
        event = Event(type="custom", payload={})
        delivered = bus.publish(event)
        assert delivered == 1

    def test_publish_subscriber_exception_logged_not_raised(self):
        bus = EventBus()

        def failing(_event: Event) -> None:
            raise ValueError("boom")

        bus.subscribe(EventType.MODEL_ADDED, failing)
        event = Event(type=EventType.MODEL_ADDED, payload={})
        delivered = bus.publish(event)
        assert delivered == 0

    def test_history_disabled_by_default(self):
        bus = EventBus()
        event = Event(type=EventType.MODEL_ADDED, payload={"model_id": "x"})
        bus.publish(event)
        assert bus.get_history() == []

    def test_history_enabled(self):
        bus = EventBus(history_size=2)
        e1 = Event(type=EventType.MODEL_ADDED, payload={"model_id": "a"})
        e2 = Event(type=EventType.MODEL_ADDED, payload={"model_id": "b"})
        bus.publish(e1)
        bus.publish(e2)
        assert bus.get_history() == [e1, e2]

    def test_history_truncates_to_size(self):
        bus = EventBus(history_size=2)
        e1 = Event(type=EventType.MODEL_ADDED, payload={"model_id": "a"})
        e2 = Event(type=EventType.MODEL_ADDED, payload={"model_id": "b"})
        e3 = Event(type=EventType.MODEL_ADDED, payload={"model_id": "c"})
        bus.publish(e1)
        bus.publish(e2)
        bus.publish(e3)
        assert bus.get_history() == [e2, e3]

    def test_clear_removes_all_subscribers(self):
        bus = EventBus()
        bus.subscribe(EventType.MODEL_ADDED, lambda e: None)
        bus.clear()
        event = Event(type=EventType.MODEL_ADDED, payload={})
        delivered = bus.publish(event)
        assert delivered == 0

    def test_subscribe_unsubscribe_mixed_keys(self):
        bus = EventBus()
        sid1 = bus.subscribe(EventType.MODEL_ADDED, lambda e: None)
        bus.subscribe(EventType.MODEL_REMOVED, lambda e: None)
        bus.unsubscribe(sid1)
        event = Event(type=EventType.MODEL_ADDED, payload={})
        assert bus.publish(event) == 0
        event2 = Event(type=EventType.MODEL_REMOVED, payload={})
        assert bus.publish(event2) == 1

    def test_constructor_history_size_type_default(self):
        bus = EventBus()
        assert isinstance(bus._history_size, int)
