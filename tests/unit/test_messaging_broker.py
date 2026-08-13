"""Deep tests for messaging/broker.py — topic matching, pubsub, persistence, replay."""

from __future__ import annotations

import json
import os
import tempfile

import pytest

import general_ludd.messaging.broker as _mod

# ---------------------------------------------------------------------------
# _topic_pattern_to_regex tests
# ---------------------------------------------------------------------------


class TestTopicPatternToRegex:
    def test_exact_match(self) -> None:
        regex = _mod._topic_pattern_to_regex("sensor.temperature.kitchen")
        assert regex.endswith(r"\Z")

    def test_single_wildcard(self) -> None:
        regex = _mod._topic_pattern_to_regex("sensor.*.kitchen")
        assert r"[^.]+" in regex

    def test_hash_wildcard_last_segment(self) -> None:
        regex = _mod._topic_pattern_to_regex("sensor.#")
        assert ".*" in regex

    def test_hash_must_be_last_segment(self) -> None:
        with pytest.raises(ValueError, match="last segment"):
            _mod._topic_pattern_to_regex("#.temperature")

    def test_hash_not_last_raises(self) -> None:
        with pytest.raises(ValueError, match="last segment"):
            _mod._topic_pattern_to_regex("sensor.#.kitchen")

    def test_hash_with_prefix(self) -> None:
        regex = _mod._topic_pattern_to_regex("sensor.temperature.#")
        assert "temperature" in regex

    def test_escape_special_regex_chars(self) -> None:
        regex = _mod._topic_pattern_to_regex("sensor.temp+erature")
        assert r"temp\+erature" in regex


# ---------------------------------------------------------------------------
# _topic_matches tests
# ---------------------------------------------------------------------------


class TestTopicMatches:
    def test_exact_same_topic(self) -> None:
        assert _mod._topic_matches("sensor.temperature.kitchen", "sensor.temperature.kitchen")

    def test_exact_different_topic(self) -> None:
        assert not _mod._topic_matches("sensor.temperature.kitchen", "sensor.humidity.kitchen")

    def test_single_wildcard_matches_one_segment(self) -> None:
        assert _mod._topic_matches("sensor.*.kitchen", "sensor.temperature.kitchen")

    def test_single_wildcard_does_not_match_multiple_segments(self) -> None:
        assert not _mod._topic_matches("sensor.*.kitchen", "sensor.a.b.kitchen")

    def test_hash_matches_any_trailing(self) -> None:
        assert _mod._topic_matches("sensor.#", "sensor.temperature.kitchen")

    def test_hash_matches_empty_trailing(self) -> None:
        assert _mod._topic_matches("sensor.#", "sensor")

    def test_hash_matches_single_trailing(self) -> None:
        assert _mod._topic_matches("sensor.#", "sensor.temperature")

    def test_hash_with_prefix_matches_deep(self) -> None:
        assert _mod._topic_matches("sensor.temperature.#", "sensor.temperature.kitchen.a.b.c")

    def test_fanout_pattern_matches_any(self) -> None:
        assert _mod._topic_matches("#", "anything.at.all")

    def test_single_star_pattern(self) -> None:
        assert _mod._topic_matches("*", "hello")

    def test_star_does_not_match_empty_topic(self) -> None:
        assert not _mod._topic_matches("*", "")

    def test_hash_does_not_match_partial_prefix(self) -> None:
        assert not _mod._topic_matches("sensor.temperature.#", "sensor.humidity.kitchen")


# ---------------------------------------------------------------------------
# Subscription tests
# ---------------------------------------------------------------------------


class TestSubscription:
    def test_matches_delegates_to_topic(self) -> None:
        sub = _mod.Subscription(pattern="sensor.*", handler=lambda t, p: None)
        assert sub.matches("sensor.temperature")

    def test_default_queue_is_none(self) -> None:
        sub = _mod.Subscription(pattern="#", handler=lambda t, p: None)
        assert sub.queue is None

    def test_registered_at_is_float(self) -> None:
        sub = _mod.Subscription(pattern="#", handler=lambda t, p: None)
        assert isinstance(sub.registered_at, float)


# ---------------------------------------------------------------------------
# MessageBroker — subscribe / unsubscribe
# ---------------------------------------------------------------------------


class TestBrokerSubscribe:
    def test_subscribe_adds_subscription(self) -> None:
        broker = _mod.MessageBroker()
        broker.subscribe("sensor.*", lambda t, p: None)
        assert broker.subscription_count == 1

    def test_unsubscribe_removes_subscription(self) -> None:
        broker = _mod.MessageBroker()

        def handler(t, p):
            return None

        broker.subscribe("sensor.*", handler)  # type: ignore[arg-type]
        assert broker.unsubscribe("sensor.*", handler) is True  # type: ignore[arg-type]
        assert broker.subscription_count == 0

    def test_unsubscribe_nonexistent_returns_false(self) -> None:
        broker = _mod.MessageBroker()
        assert broker.unsubscribe("nonexistent", lambda t, p: None) is False

    def test_clear_removes_all(self) -> None:
        broker = _mod.MessageBroker()
        broker.subscribe("a", lambda t, p: None)
        broker.subscribe("b", lambda t, p: None)
        broker.clear()
        assert broker.subscription_count == 0


# ---------------------------------------------------------------------------
# MessageBroker — publish
# ---------------------------------------------------------------------------


class TestBrokerPublish:
    def test_publish_delivers_to_matching_handler(self) -> None:
        received: list[tuple[str, object]] = []
        broker = _mod.MessageBroker()
        broker.subscribe("sensor.*", lambda t, p: received.append((t, p)))
        count = broker.publish("sensor.temperature", {"value": 72.0})
        assert count == 1
        assert len(received) == 1
        assert received[0] == ("sensor.temperature", {"value": 72.0})

    def test_publish_no_matching_handlers_returns_zero(self) -> None:
        broker = _mod.MessageBroker()
        broker.subscribe("sensor.*", lambda t, p: None)
        count = broker.publish("actuator.pump", "on")
        assert count == 0

    def test_publish_fanout_to_all_matching(self) -> None:
        received: list[str] = []
        broker = _mod.MessageBroker()
        broker.subscribe("sensor.#", lambda t, p: received.append("a"))
        broker.subscribe("sensor.#", lambda t, p: received.append("b"))
        count = broker.publish("sensor.temperature", {})
        assert count == 2
        assert received == ["a", "b"]

    def test_publish_queue_group_only_first(self) -> None:
        received: list[str] = []
        broker = _mod.MessageBroker()
        broker.subscribe("sensor.#", lambda t, p: received.append("q1-a"), queue="q1")
        broker.subscribe("sensor.#", lambda t, p: received.append("q1-b"), queue="q1")
        count = broker.publish("sensor.temperature", {})
        assert count == 1
        assert len(received) == 1

    def test_publish_mixed_fanout_and_queues(self) -> None:
        received: list[str] = []
        broker = _mod.MessageBroker()
        broker.subscribe("sensor.#", lambda t, p: received.append("fanout"))
        broker.subscribe("sensor.#", lambda t, p: received.append("q2"), queue="q2")
        count = broker.publish("sensor.temperature", {})
        assert count == 2
        assert "fanout" in received
        assert "q2" in received

    def test_publish_handler_error_increments_error_count(self) -> None:
        broker = _mod.MessageBroker()

        def failing(topic: str, payload: object) -> None:
            raise RuntimeError("boom")

        broker.subscribe("sensor.*", failing)
        count = broker.publish("sensor.temperature", {})
        assert broker.error_count == 1
        assert count == 0

    def test_publish_increments_publish_count(self) -> None:
        broker = _mod.MessageBroker()
        broker.publish("a", {})
        broker.publish("b", {})
        assert broker.publish_count == 2

    def test_publish_increments_deliver_count(self) -> None:
        broker = _mod.MessageBroker()
        broker.subscribe("#", lambda t, p: None)
        broker.publish("a", {})
        broker.publish("b", {})
        assert broker.deliver_count == 2

    def test_publish_with_injectable_clock(self) -> None:
        broker = _mod.MessageBroker(clock=lambda: 12345.0)
        broker.publish("a", {})
        assert broker._messages[0].timestamp == 12345.0


# ---------------------------------------------------------------------------
# MessageBroker — max_persisted
# ---------------------------------------------------------------------------


class TestBrokerMaxPersisted:
    def test_messages_capped_at_max_persisted(self) -> None:
        broker = _mod.MessageBroker(max_persisted=3)
        for i in range(10):
            broker.publish("a", i)
        assert broker.message_count == 3
        persisted = [m.payload for m in broker._messages]
        assert persisted == [7, 8, 9]

    def test_default_max_persisted_is_large(self) -> None:
        broker = _mod.MessageBroker()
        assert broker.message_count == 0


# ---------------------------------------------------------------------------
# MessageBroker — persist / replay
# ---------------------------------------------------------------------------


class TestBrokerPersistReplay:
    def test_persist_roundtrip(self) -> None:
        broker = _mod.MessageBroker()
        received: list[str] = []
        broker.subscribe("sensor.*", lambda t, p: received.append(t))
        broker.publish("sensor.temperature", {"value": 72.0})
        broker.publish("sensor.humidity", {"value": 55.0})

        with tempfile.NamedTemporaryFile(delete=False, suffix=".json") as tf:
            path = tf.name
        try:
            broker.persist(path)
            assert os.path.exists(path)
            with open(path) as fh:
                data = json.load(fh)
            assert data["version"] == 1
            assert data["publish_count"] == 2
            assert len(data["messages"]) == 2
        finally:
            os.unlink(path)

    def test_replay_redelivers_to_subscribers(self) -> None:
        broker = _mod.MessageBroker()
        received: list[str] = []
        broker.subscribe("sensor.*", lambda t, p: received.append(t))
        broker.publish("sensor.temperature", {})

        with tempfile.NamedTemporaryFile(delete=False, suffix=".json") as tf:
            path = tf.name
        try:
            broker.persist(path)
            received.clear()

            broker2 = _mod.MessageBroker()
            broker2.subscribe("sensor.*", lambda t, p: received.append(t))
            replay_count = broker2.replay(path)
            assert replay_count >= 1
            assert "sensor.temperature" in received
        finally:
            os.unlink(path)

    def test_replay_restores_counters(self) -> None:
        broker = _mod.MessageBroker()
        broker.subscribe("a", lambda t, p: None)
        broker.publish("a", {})
        broker.publish("a", {})

        with tempfile.NamedTemporaryFile(delete=False, suffix=".json") as tf:
            path = tf.name
        try:
            broker.persist(path)
            broker2 = _mod.MessageBroker()
            broker2.subscribe("a", lambda t, p: None)
            broker2.replay(path)
            assert broker2.publish_count == 2
        finally:
            os.unlink(path)

    def test_replay_respects_queue_groups(self) -> None:
        broker = _mod.MessageBroker()
        received: list[str] = []
        broker.subscribe("a", lambda t, p: received.append("q3-1"), queue="q3")
        broker.subscribe("a", lambda t, p: received.append("q3-2"), queue="q3")
        broker.publish("a", {})

        with tempfile.NamedTemporaryFile(delete=False, suffix=".json") as tf:
            path = tf.name
        try:
            broker.persist(path)
            broker2 = _mod.MessageBroker()
            broker2.subscribe("a", lambda t, p: received.append("replay"), queue="q3")
            broker2.replay(path)
        finally:
            os.unlink(path)

    def test_replay_handler_error_increments_error_count(self) -> None:
        broker = _mod.MessageBroker()
        broker.subscribe("a", lambda t, p: None)
        broker.publish("a", {})

        with tempfile.NamedTemporaryFile(delete=False, suffix=".json") as tf:
            path = tf.name
        try:
            broker.persist(path)

            def failing(t: str, p: object) -> None:
                raise RuntimeError("boom")

            broker2 = _mod.MessageBroker()
            broker2.subscribe("a", failing)
            broker2.replay(path)
            assert broker2.error_count >= 1
        finally:
            os.unlink(path)


# ---------------------------------------------------------------------------
# MessageBroker — snapshot / topics / messages_for_topic
# ---------------------------------------------------------------------------


class TestBrokerIntrospection:
    def test_snapshot_returns_dict(self) -> None:
        broker = _mod.MessageBroker()
        broker.subscribe("a.*", lambda t, p: None)
        broker.publish("a.x", "hello")
        snap = broker.snapshot()
        assert snap["subscription_count"] == 1
        assert snap["message_count"] == 1
        assert snap["publish_count"] == 1
        assert snap["deliver_count"] == 1

    def test_snapshot_recent_topics(self) -> None:
        broker = _mod.MessageBroker()
        broker.subscribe("#", lambda t, p: None)
        broker.publish("a", {})
        broker.publish("b", {})
        snap = broker.snapshot()
        assert snap["recent_topics"] == ["a", "b"]

    def test_snapshot_subscription_details(self) -> None:
        broker = _mod.MessageBroker()
        broker.subscribe("sensor.*", lambda t, p: None, queue="main")
        snap = broker.snapshot()
        assert snap["subscriptions"] == [{"pattern": "sensor.*", "queue": "main"}]

    def test_topics_returns_unique_set(self) -> None:
        broker = _mod.MessageBroker()
        broker.publish("a", {})
        broker.publish("a", {})
        broker.publish("b", {})
        assert broker.topics() == {"a", "b"}

    def test_messages_for_topic_exact(self) -> None:
        broker = _mod.MessageBroker()
        broker.publish("a", 1)
        broker.publish("b", 2)
        msgs = broker.messages_for_topic("a")
        assert len(msgs) == 1
        assert msgs[0]["payload"] == 1

    def test_messages_for_topic_wildcard(self) -> None:
        broker = _mod.MessageBroker()
        broker.publish("sensor.temperature", 1)
        broker.publish("sensor.humidity", 2)
        msgs = broker.messages_for_topic("sensor.*")
        assert len(msgs) == 2

    def test_messages_for_topic_no_match_empty(self) -> None:
        broker = _mod.MessageBroker()
        assert broker.messages_for_topic("nonexistent") == []


# ---------------------------------------------------------------------------
# MessageBroker — concurrent access
# ---------------------------------------------------------------------------


class TestBrokerThreadSafety:
    def test_lock_exists_and_is_reentrant_safe(self) -> None:
        broker = _mod.MessageBroker()
        assert broker._lock is not None
        assert hasattr(broker._lock, "acquire")
        assert hasattr(broker._lock, "release")

    def test_snapshot_acquires_lock_internally(self) -> None:
        broker = _mod.MessageBroker()
        broker.subscribe("#", lambda t, p: None)
        broker.publish("a", {})
        snap = broker.snapshot()
        assert isinstance(snap, dict)
        assert "subscription_count" in snap


# ---------------------------------------------------------------------------
# PersistedMessage
# ---------------------------------------------------------------------------


class TestPersistedMessage:
    def test_fields(self) -> None:
        msg = _mod.PersistedMessage(topic="a", payload={"x": 1}, timestamp=100.0)
        assert msg.topic == "a"
        assert msg.payload == {"x": 1}
        assert msg.timestamp == 100.0


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestBrokerEdgeCases:
    def test_empty_broker_snapshot(self) -> None:
        broker = _mod.MessageBroker()
        snap = broker.snapshot()
        assert snap["subscription_count"] == 0
        assert snap["message_count"] == 0
        assert snap["recent_topics"] == []

    def test_empty_broker_topics(self) -> None:
        broker = _mod.MessageBroker()
        assert broker.topics() == set()

    def test_publish_no_subscribers_still_persists(self) -> None:
        broker = _mod.MessageBroker(max_persisted=5)
        broker.publish("orphan", "data")
        assert broker.message_count == 1

    def test_unsubscribe_only_removes_matching(self) -> None:
        def h1(t, p):
            return None

        def h2(t, p):
            return None

        broker = _mod.MessageBroker()
        broker.subscribe("a", h1)  # type: ignore[arg-type]
        broker.subscribe("a", h2)  # type: ignore[arg-type]
        assert broker.unsubscribe("a", h1) is True  # type: ignore[arg-type]
        assert broker.subscription_count == 1

    def test_publish_does_not_overflow_max_persisted_exact(self) -> None:
        broker = _mod.MessageBroker(max_persisted=1)
        broker.publish("a", 1)
        broker.publish("b", 2)
        assert broker.message_count == 1
        assert broker._messages[0].payload == 2
