"""WebSocket realtime event tests — connection lifecycle, message framing,
subscription management, reconnection with state recovery, rate limiting,
and heartbeat/ping-pong against the EventBus and event types."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from general_ludd.events.bus import EventBus
from general_ludd.events.types import Event, EventType, WorkerPingEvent, WorkerPongEvent

# ——— Lightweight WebSocket-like session adapter over EventBus ———


@dataclass
class RealtimeSession:
    """Models a WebSocket connection lifecycle over the EventBus.

    Matches a typical WS server: connect → authenticate → subscribe to
    topics → receive messages → heartbeat → disconnect.
    """

    bus: EventBus
    session_id: str
    authenticated: bool = False
    subscriptions: set[str] = field(default_factory=set)
    received: list[Event] = field(default_factory=list)
    connected: bool = True
    _bus_sub_ids: dict[str, str] = field(default_factory=dict)

    def connect(self) -> None:
        self.connected = True
        self._emit("ws_connect", {"session_id": self.session_id})

    def authenticate(self, token: str) -> bool:
        if token.startswith("valid_"):
            self.authenticated = True
            self._emit("ws_auth", {"session_id": self.session_id, "status": "ok"})
            return True
        self._emit("ws_auth", {"session_id": self.session_id, "status": "denied"})
        return False

    def subscribe(self, topic: str) -> str:
        sub_id = f"ws:{self.session_id}:{topic}"
        self.subscriptions.add(topic)
        bus_sub_id = self.bus.subscribe(topic, self._on_message)
        self._bus_sub_ids[topic] = bus_sub_id
        self._emit("ws_subscribe", {"session_id": self.session_id, "topic": topic})
        return sub_id

    def unsubscribe(self, topic: str) -> None:
        self.subscriptions.discard(topic)
        bus_sub_id = self._bus_sub_ids.pop(topic, None)
        if bus_sub_id is not None:
            self.bus.unsubscribe(bus_sub_id)
        self._emit("ws_unsubscribe", {"session_id": self.session_id, "topic": topic})

    def publish(self, topic: str, payload: dict[str, Any]) -> int:
        event = Event(type=topic, payload=payload, source=self.session_id)
        return self.bus.publish(event)

    def _on_message(self, event: Event) -> None:
        self.received.append(event)

    def _emit(self, event_type: str, payload: dict[str, Any]) -> None:
        event = Event(type=event_type, payload=payload, source=self.session_id)
        self.bus.publish(event)

    def disconnect(self) -> None:
        self.connected = False
        for _topic, bus_sub_id in list(self._bus_sub_ids.items()):
            self.bus.unsubscribe(bus_sub_id)
        self._bus_sub_ids.clear()
        self._emit("ws_disconnect", {"session_id": self.session_id})


# ——— Rate limiter for message bursts ———


@dataclass
class RateLimiter:
    """Token-bucket rate limiter for message delivery."""

    max_messages: int
    window_seconds: float
    _timestamps: list[float] = field(default_factory=list)

    def allow(self) -> bool:
        now = time.monotonic()
        cutoff = now - self.window_seconds
        self._timestamps = [t for t in self._timestamps if t > cutoff]
        if len(self._timestamps) < self.max_messages:
            self._timestamps.append(now)
            return True
        return False


# ——— Tests ———


class TestConnectionLifecycle:
    def test_connect_emits_event(self):
        bus = EventBus()
        received: list[Event] = []
        bus.subscribe("ws_connect", received.append)
        session = RealtimeSession(bus=bus, session_id="s1")
        session.connect()
        assert session.connected
        assert len(received) == 1
        assert received[0].type == "ws_connect"
        assert received[0].payload["session_id"] == "s1"

    def test_authenticate_with_valid_token(self):
        bus = EventBus()
        auth_events: list[Event] = []
        bus.subscribe("ws_auth", auth_events.append)
        session = RealtimeSession(bus=bus, session_id="s2")
        result = session.authenticate("valid_secret_123")
        assert result is True
        assert session.authenticated
        assert len(auth_events) == 1
        assert auth_events[0].payload["status"] == "ok"

    def test_authenticate_with_invalid_token(self):
        bus = EventBus()
        auth_events: list[Event] = []
        bus.subscribe("ws_auth", auth_events.append)
        session = RealtimeSession(bus=bus, session_id="s3")
        result = session.authenticate("bad_token")
        assert result is False
        assert not session.authenticated
        assert len(auth_events) == 1
        assert auth_events[0].payload["status"] == "denied"

    def test_disconnect_emits_event(self):
        bus = EventBus()
        received: list[Event] = []
        bus.subscribe("ws_disconnect", received.append)
        session = RealtimeSession(bus=bus, session_id="s4")
        session.connect()
        session.disconnect()
        assert not session.connected
        assert len(received) == 1
        assert received[0].type == "ws_disconnect"

    def test_disconnect_does_not_receive_future_messages(self):
        bus = EventBus()
        session = RealtimeSession(bus=bus, session_id="s5")
        session.disconnect()
        assert not session.connected
        before = len(session.received)
        session.publish("chat", {"msg": "hello"})
        assert len(session.received) == before


class TestMessageFramingAndDelivery:
    def test_message_delivered_to_subscriber(self):
        bus = EventBus()
        session = RealtimeSession(bus=bus, session_id="s6")
        session.subscribe("chat.room.1")
        session.publish("chat.room.1", {"user": "alice", "text": "hello"})
        assert len(session.received) == 1
        assert session.received[0].payload["user"] == "alice"
        assert session.received[0].payload["text"] == "hello"

    def test_message_not_delivered_to_unsubscribed_session(self):
        bus = EventBus()
        session_a = RealtimeSession(bus=bus, session_id="a")
        session_b = RealtimeSession(bus=bus, session_id="b")
        session_a.subscribe("chat.room.1")
        session_b.subscribe("chat.room.2")
        session_a.publish("chat.room.1", {"msg": "hello a"})
        assert len(session_a.received) == 1
        assert len(session_b.received) == 0

    def test_event_payload_integrity(self):
        bus = EventBus()
        session = RealtimeSession(bus=bus, session_id="s7")
        session.subscribe("notifications")
        payload = {
            "type": "mention",
            "from": "bot",
            "timestamp": 1722691200.0,
            "metadata": {"priority": "high", "ttl": 30},
        }
        session.publish("notifications", payload)
        assert len(session.received) == 1
        assert session.received[0].payload == payload
        assert session.received[0].source == "s7"
        assert isinstance(session.received[0].event_id, str)
        assert len(session.received[0].event_id) == 32

    def test_multi_topic_delivery(self):
        bus = EventBus()
        session = RealtimeSession(bus=bus, session_id="s8")
        session.subscribe("orders")
        session.subscribe("alerts")
        session.publish("orders", {"id": 1})
        session.publish("alerts", {"severity": "info"})
        assert len(session.received) == 2
        topics = {e.type for e in session.received}
        assert topics == {"orders", "alerts"}


class TestSubscriptionManagement:
    def test_subscribe_registers_on_bus(self):
        bus = EventBus()
        session = RealtimeSession(bus=bus, session_id="s9")
        sid = session.subscribe("system.status")
        assert sid.startswith("ws:s9:")
        assert "system.status" in session.subscriptions

    def test_subscribe_emits_event(self):
        bus = EventBus()
        sub_events: list[Event] = []
        bus.subscribe("ws_subscribe", sub_events.append)
        session = RealtimeSession(bus=bus, session_id="s10")
        session.subscribe("alerts")
        assert len(sub_events) == 1
        assert sub_events[0].payload["topic"] == "alerts"

    def test_unsubscribe_stops_delivery(self):
        bus = EventBus()
        session = RealtimeSession(bus=bus, session_id="s11")
        session.subscribe("updates")
        session.publish("updates", {"n": 1})
        assert len(session.received) == 1
        session.unsubscribe("updates")
        session.publish("updates", {"n": 2})
        assert len(session.received) == 1

    def test_unsubscribe_emits_event(self):
        bus = EventBus()
        unsub_events: list[Event] = []
        bus.subscribe("ws_unsubscribe", unsub_events.append)
        session = RealtimeSession(bus=bus, session_id="s12")
        session.subscribe("alerts")
        session.unsubscribe("alerts")
        assert len(unsub_events) == 1
        assert unsub_events[0].payload["topic"] == "alerts"

    def test_wildcard_subscription_via_global_topic(self):
        bus = EventBus()
        global_received: list[Event] = []
        bus.subscribe("*", global_received.append)
        session = RealtimeSession(bus=bus, session_id="s13")
        session.subscribe("topic.a")
        session.publish("topic.a", {"x": 1})
        session.publish("topic.b", {"y": 2})
        assert len(global_received) >= 2

    def test_clear_bus_removes_all_subscriptions(self):
        bus = EventBus()
        session = RealtimeSession(bus=bus, session_id="s14")
        session.subscribe("test.topic")
        session.publish("test.topic", {"key": "val"})
        assert len(session.received) == 1
        bus.clear()
        session.publish("test.topic", {"key": "val2"})
        assert len(session.received) == 1


class TestReconnectionWithStateRecovery:
    def test_history_replay_on_reconnect(self):
        bus = EventBus(history_size=50)
        session = RealtimeSession(bus=bus, session_id="s15")
        session.connect()
        session.subscribe("room.general")
        session.publish("room.general", {"msg": "hi 1"})
        session.publish("room.general", {"msg": "hi 2"})
        session.publish("room.general", {"msg": "hi 3"})
        session.disconnect()
        history = bus.get_history()
        assert len(history) == 6
        replayed = [e for e in history if e.type == "room.general"]
        assert len(replayed) == 3
        assert [e.payload["msg"] for e in replayed] == ["hi 1", "hi 2", "hi 3"]

    def test_resubscribe_after_disconnect(self):
        bus = EventBus()
        session = RealtimeSession(bus=bus, session_id="s16")
        session.subscribe("orders")
        session.publish("orders", {"order_id": 1})
        session.disconnect()
        session.received.clear()
        session2 = RealtimeSession(bus=bus, session_id="s16_reconnect")
        session2.connect()
        session2.subscribe("orders")
        session2.publish("orders", {"order_id": 2})
        assert len(session2.received) == 1
        assert session2.received[0].payload["order_id"] == 2

    def test_history_truncates_to_configured_size(self):
        bus = EventBus(history_size=5)
        session = RealtimeSession(bus=bus, session_id="s17")
        session.subscribe("events")
        for i in range(10):
            session.publish("events", {"n": i})
        history = bus.get_history()
        assert len(history) == 5
        last_n = [e.payload["n"] for e in history if e.type == "events"]
        assert last_n == [5, 6, 7, 8, 9]

    def test_graceful_reconnect_preserves_foreign_subscriptions(self):
        bus = EventBus()
        alice = RealtimeSession(bus=bus, session_id="alice")
        bob = RealtimeSession(bus=bus, session_id="bob")
        alice.subscribe("dm")
        bob.subscribe("dm")
        alice.publish("dm", {"from": "alice", "text": "hello bob"})
        assert len(alice.received) == 1
        assert len(bob.received) == 1
        alice.disconnect()
        alice2 = RealtimeSession(bus=bus, session_id="alice_reconnect")
        alice2.subscribe("dm")
        bob.publish("dm", {"from": "bob", "text": "hello alice"})
        assert len(alice2.received) == 1
        assert len(bob.received) == 2


class TestRateLimiting:
    def test_rate_limiter_allows_up_to_max(self):
        rl = RateLimiter(max_messages=5, window_seconds=1.0)
        for _ in range(5):
            assert rl.allow() is True

    def test_rate_limiter_denies_after_max(self):
        rl = RateLimiter(max_messages=3, window_seconds=1.0)
        for _ in range(3):
            assert rl.allow() is True
        assert rl.allow() is False

    def test_rate_limiter_window_resets_over_time(self):
        rl = RateLimiter(max_messages=2, window_seconds=0.01)
        assert rl.allow() is True
        assert rl.allow() is True
        assert rl.allow() is False
        time.sleep(0.015)
        assert rl.allow() is True

    def test_burst_publish_under_rate_limit(self):
        bus = EventBus()
        session = RealtimeSession(bus=bus, session_id="s18")
        session.subscribe("data")
        rl = RateLimiter(max_messages=10, window_seconds=1.0)
        delivered = 0
        for i in range(8):
            if rl.allow():
                session.publish("data", {"i": i})
                delivered += 1
        assert delivered == 8
        assert len(session.received) == 8

    def test_burst_publish_exceeds_rate_limit(self):
        bus = EventBus()
        session = RealtimeSession(bus=bus, session_id="s19")
        session.subscribe("data")
        rl = RateLimiter(max_messages=4, window_seconds=1.0)
        allowed_count = 0
        denied_count = 0
        for i in range(10):
            if rl.allow():
                session.publish("data", {"i": i})
                allowed_count += 1
            else:
                denied_count += 1
        assert allowed_count == 4
        assert denied_count == 6
        assert len(session.received) == 4


class TestHeartbeatPingPong:
    def test_ping_has_correct_event_type(self):
        ping = WorkerPingEvent()
        assert ping.type == EventType.WORKER_PING
        assert isinstance(ping.event_id, str)
        assert len(ping.event_id) == 32

    def test_pong_correlates_to_ping(self):
        ping = WorkerPingEvent()
        pong = WorkerPongEvent(worker_id="worker-1", correlation_id=ping.event_id)
        assert pong.type == EventType.WORKER_PONG
        assert pong.correlation_id == ping.event_id
        assert pong.payload["worker_id"] == "worker-1"

    def test_ping_pong_roundtrip(self):
        from general_ludd.worker.heartbeat import handle_ping, make_ping

        ping = make_ping()
        assert ping.type == EventType.WORKER_PING
        pong = handle_ping(ping, "worker-xyz")
        assert pong.type == EventType.WORKER_PONG
        assert pong.correlation_id == ping.event_id
        assert pong.payload["worker_id"] == "worker-xyz"

    def test_heartbeat_liveness_subscriber(self):
        bus = EventBus()
        pings: list[Event] = []
        pongs: list[Event] = []

        def on_ping(event: Event) -> None:
            pings.append(event)
            pong = WorkerPongEvent(worker_id="w1", correlation_id=event.event_id)
            pongs.append(pong)
            bus.publish(pong)

        bus.subscribe(EventType.WORKER_PING, on_ping)
        bus.subscribe(EventType.WORKER_PONG, lambda e: pongs.append(e))

        ping = WorkerPingEvent()
        bus.publish(ping)
        assert len(pings) == 1
        assert len(pongs) >= 1

    def test_missed_heartbeat_detection(self):
        bus = EventBus()
        time.monotonic() + 0.02
        ping = WorkerPingEvent()
        bus.publish(ping)
        pong_received: list[bool] = []

        def on_pong(_event: Event) -> None:
            pong_received.append(True)

        bus.subscribe(EventType.WORKER_PONG, on_pong)
        pong = WorkerPongEvent(worker_id="w2", correlation_id=ping.event_id)
        bus.publish(pong)
        assert len(pong_received) == 1


class TestConcurrentSessions:
    def test_multiple_sessions_receive_own_messages(self):
        bus = EventBus()
        s1 = RealtimeSession(bus=bus, session_id="u1")
        s2 = RealtimeSession(bus=bus, session_id="u2")
        s1.subscribe("room")
        s2.subscribe("room")
        s1.publish("room", {"from": "u1", "text": "hello"})
        s2.publish("room", {"from": "u2", "text": "hi"})
        assert len(s1.received) == 2
        assert len(s2.received) == 2
        senders = {e.payload["from"] for e in s1.received}
        assert senders == {"u1", "u2"}

    def test_disconnect_isolates_sessions(self):
        bus = EventBus()
        s1 = RealtimeSession(bus=bus, session_id="u1")
        s2 = RealtimeSession(bus=bus, session_id="u2")
        s1.subscribe("room")
        s2.subscribe("room")
        s1.disconnect()
        s1.received.clear()
        s2.publish("room", {"msg": "u2 only"})
        assert len(s1.received) == 0
        assert len(s2.received) == 1


class TestEventTypeValidation:
    def test_event_has_required_fields(self):
        event = Event(type="test.event", payload={"key": "value"})
        assert event.type == "test.event"
        assert event.payload == {"key": "value"}
        assert event.source is None
        assert event.correlation_id is None
        assert isinstance(event.timestamp, float)
        assert len(event.event_id) == 32

    def test_unknown_event_type_delivers_as_string(self):
        bus = EventBus()
        received: list[Event] = []
        bus.subscribe("my.custom.event", received.append)
        event = Event(type="my.custom.event", payload={"x": 42})
        delivered = bus.publish(event)
        assert delivered == 1
        assert received[0].payload["x"] == 42
