from __future__ import annotations

import uuid

from general_ludd.events.types import (
    CustomEvent,
    Event,
    EventType,
    ModelAddedEvent,
    StallDetectedEvent,
    WorkerPingEvent,
)


class TestEventType:
    def test_all_values_non_empty(self) -> None:
        for member in EventType:
            assert isinstance(member.value, str)
            assert len(member.value) > 0

    def test_all_values_unique(self) -> None:
        vals = [m.value for m in EventType]
        assert len(vals) == len(set(vals))

    def test_member_count(self) -> None:
        assert len(list(EventType)) == 21


class TestEvent:
    def test_default_payload_is_empty_dict(self) -> None:
        e = Event(type=EventType.CUSTOM)
        assert e.payload == {}
        assert isinstance(e.payload, dict)

    def test_event_id_is_hex_string(self) -> None:
        e = Event(type=EventType.CUSTOM)
        assert len(e.event_id) == 32
        uuid.UUID(hex=e.event_id)

    def test_event_accepts_string_type(self) -> None:
        e = Event(type="user.defined.event")
        assert e.type == "user.defined.event"

    def test_custom_correlation_id(self) -> None:
        e = Event(type=EventType.CUSTOM, correlation_id="trace-abc-123")
        assert e.correlation_id == "trace-abc-123"

    def test_source_settable(self) -> None:
        e = Event(type=EventType.CUSTOM, source="test-harness")
        assert e.source == "test-harness"


class TestConcreteEventInheritance:
    def test_all_subclasses_inherit_from_event(self) -> None:
        ev = ModelAddedEvent(model_id="x", profile={})
        assert isinstance(ev, Event)
        ev2 = WorkerPingEvent()
        assert isinstance(ev2, Event)
        ev3 = CustomEvent(name="test")
        assert isinstance(ev3, Event)

    def test_event_kwargs_forwarded_to_base(self) -> None:
        ev = ModelAddedEvent(model_id="x", profile={}, source="s", correlation_id="c")
        assert ev.source == "s"
        assert ev.correlation_id == "c"

    def test_custom_event_name_in_payload(self) -> None:
        ev = CustomEvent(name="system.boot")
        assert ev.type == EventType.CUSTOM
        assert ev.payload["name"] == "system.boot"

    def test_custom_event_merges_extra_payload(self) -> None:
        ev = CustomEvent(name="event.x", payload={"extra": 42, "flag": True})
        assert ev.payload["name"] == "event.x"
        assert ev.payload["extra"] == 42
        assert ev.payload["flag"] is True

    def test_stall_detected_default_thread_stacks(self) -> None:
        ev = StallDetectedEvent(operation="build", elapsed_s=60.0, deadline_s=30.0)
        assert ev.payload["thread_stacks"] is None

    def test_stall_detected_with_thread_stacks(self) -> None:
        ev = StallDetectedEvent(
            operation="build",
            elapsed_s=60.0,
            deadline_s=30.0,
            thread_stacks={"main": "line 10"},
        )
        assert ev.payload["thread_stacks"] == {"main": "line 10"}
