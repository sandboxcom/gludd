"""Deep unit tests for MessageBroker — pubsub, wildcards, fanout,
queues, persistence, and replay."""

from __future__ import annotations

import json
import os
import tempfile
from typing import Any

import pytest

from general_ludd.messaging.broker import (
    MessageBroker,
    _topic_matches,
    _topic_pattern_to_regex,
)


@pytest.fixture
def broker() -> MessageBroker:
    return MessageBroker()


def _topic_matches_direct(pattern: str, topic: str) -> bool:
    return _topic_matches(pattern, topic)


# ---------------------------------------------------------------------------
# wildcard topic matching (standalone)
# ---------------------------------------------------------------------------


class TestTopicMatching:
    def test_exact_match(self) -> None:
        assert _topic_matches_direct("a.b.c", "a.b.c") is True

    def test_exact_mismatch(self) -> None:
        assert _topic_matches_direct("a.b.c", "a.b.x") is False

    def test_single_level_wildcard(self) -> None:
        assert _topic_matches_direct("a.*.c", "a.b.c") is True

    def test_single_level_wildcard_no_match(self) -> None:
        assert _topic_matches_direct("a.*.c", "a.b.x") is False

    def test_multi_level_wildcard_end(self) -> None:
        assert _topic_matches_direct("a.#", "a.b.c") is True

    def test_multi_level_wildcard_zero_segments(self) -> None:
        assert _topic_matches_direct("a.#", "a") is True

    def test_multi_level_wildcard_mismatch(self) -> None:
        assert _topic_matches_direct("a.#", "b.c") is False

    def test_pound_not_last_raises(self) -> None:
        with pytest.raises(ValueError, match="last segment"):
            _topic_pattern_to_regex("a.#.c")

    def test_literal_dots_in_segment(self) -> None:
        assert _topic_matches_direct("a.b", "a.b") is True
        assert _topic_matches_direct("a.b", "axb") is False

    def test_special_chars_escaped(self) -> None:
        assert _topic_matches_direct("sensor.+temp", "sensor.+temp") is True
        assert _topic_matches_direct("sensor.+temp", "sensor.xtemp") is False


# ---------------------------------------------------------------------------
# basic publish / subscribe
# ---------------------------------------------------------------------------


class TestPublishSubscribe:
    def test_publish_delivers_to_single_subscriber(self, broker: MessageBroker) -> None:
        received: list[Any] = []

        def handler(topic: str, payload: Any) -> None:
            received.append(payload)

        broker.subscribe("orders.created", handler)
        delivered = broker.publish("orders.created", {"id": 1})

        assert delivered == 1
        assert received == [{"id": 1}]
        assert broker.publish_count == 1
        assert broker.deliver_count == 1

    def test_publish_to_no_subscribers_returns_zero(self, broker: MessageBroker) -> None:
        delivered = broker.publish("ghost.topic", "data")
        assert delivered == 0

    def test_topic_argument_passed_to_handler(self, broker: MessageBroker) -> None:
        seen_topic: list[str] = []

        def handler(topic: str, payload: Any) -> None:
            seen_topic.append(topic)

        broker.subscribe("events.*", handler)
        broker.publish("events.login", {"user": "a"})

        assert seen_topic == ["events.login"]

    def test_unsubscribe_removes_handler(self, broker: MessageBroker) -> None:
        received: list[Any] = []

        def handler(topic: str, payload: Any) -> None:
            received.append(payload)

        broker.subscribe("tasks.run", handler)
        removed = broker.unsubscribe("tasks.run", handler)

        assert removed is True
        assert broker.publish("tasks.run", "x") == 0
        assert received == []

    def test_unsubscribe_nonexistent(self, broker: MessageBroker) -> None:
        def handler(topic: str, payload: Any) -> None:
            pass

        assert broker.unsubscribe("nope", handler) is False

    def test_clear_purges_all(self, broker: MessageBroker) -> None:
        received: list[Any] = []

        def handler(topic: str, payload: Any) -> None:
            received.append(payload)

        broker.subscribe("a.b", handler)
        broker.subscribe("c.d", handler)
        broker.clear()

        assert broker.publish("a.b", 1) == 0
        assert broker.publish("c.d", 2) == 0
        assert received == []


# ---------------------------------------------------------------------------
# fanout (all matching subscribers receive)
# ---------------------------------------------------------------------------


class TestFanout:
    def test_all_matching_subscribers_receive(self, broker: MessageBroker) -> None:
        a: list[Any] = []
        b: list[Any] = []
        c: list[Any] = []

        def make(sink: list[Any]) -> Any:
            def handler(topic: str, payload: Any) -> None:
                sink.append(payload)

            return handler

        broker.subscribe("chat.room1", make(a))
        broker.subscribe("chat.room1", make(b))
        broker.subscribe("chat.room1", make(c))

        delivered = broker.publish("chat.room1", "hello")

        assert delivered == 3
        assert a == ["hello"]
        assert b == ["hello"]
        assert c == ["hello"]

    def test_fanout_respects_wildcard_matching(self, broker: MessageBroker) -> None:
        a: list[str] = []
        b: list[str] = []

        broker.subscribe("sensor.*.active", lambda t, p: a.append(str(p)))
        broker.subscribe("sensor.temperature.active", lambda t, p: b.append(str(p)))

        broker.publish("sensor.temperature.active", "hot")

        assert len(a) == 1
        assert len(b) == 1

    def test_failing_handler_does_not_block_others(self, broker: MessageBroker) -> None:
        received: list[Any] = []

        def good(topic: str, payload: Any) -> None:
            received.append(payload)

        def bad(topic: str, payload: Any) -> None:
            raise RuntimeError("boom")

        broker.subscribe("data.stream", bad)
        broker.subscribe("data.stream", good)

        delivered = broker.publish("data.stream", 42)

        assert delivered == 1
        assert received == [42]
        assert broker.error_count == 1


# ---------------------------------------------------------------------------
# queue groups (competing consumers)
# ---------------------------------------------------------------------------


class TestQueueGroups:
    def test_single_handler_per_queue_group(self, broker: MessageBroker) -> None:
        results: dict[str, list[Any]] = {}

        def make(worker: str) -> Any:
            def handler(topic: str, payload: Any) -> None:
                results.setdefault(worker, []).append(payload)

            return handler

        broker.subscribe("jobs.render", make("w1"), queue="render-q")
        broker.subscribe("jobs.render", make("w2"), queue="render-q")
        broker.subscribe("jobs.render", make("w3"), queue="render-q")

        delivered = broker.publish("jobs.render", {"frame": 1})

        total_received = sum(len(v) for v in results.values())
        assert total_received == 1
        assert delivered == 1

    def test_different_queues_each_get_copy(self, broker: MessageBroker) -> None:
        results: dict[str, list[Any]] = {}

        def make(name: str) -> Any:
            def handler(topic: str, payload: Any) -> None:
                results.setdefault(name, []).append(payload)

            return handler

        broker.subscribe("alerts", make("q1-a"), queue="q1")
        broker.subscribe("alerts", make("q1-b"), queue="q1")
        broker.subscribe("alerts", make("q2-a"), queue="q2")

        delivered = broker.publish("alerts", "fire")

        assert "q1" in str(results) or any(k.startswith("q1") for k in results)
        assert delivered == 2  # one from q1, one from q2

    def test_fanout_and_queue_mix(self, broker: MessageBroker) -> None:
        fanout_sink: list[Any] = []
        queue_sink: list[Any] = []

        broker.subscribe("events", lambda t, p: fanout_sink.append(p))
        broker.subscribe("events", lambda t, p: queue_sink.append(p), queue="persist")

        delivered = broker.publish("events", "e1")

        assert len(fanout_sink) == 1
        assert len(queue_sink) == 1
        assert delivered == 2


# ---------------------------------------------------------------------------
# persistence
# ---------------------------------------------------------------------------


class TestPersistence:
    def test_persist_and_replay(self, broker: MessageBroker) -> None:
        received: list[Any] = []

        def handler(topic: str, payload: Any) -> None:
            received.append(payload)

        broker.subscribe("data.created", handler)
        broker.subscribe("data.updated", handler)
        broker.publish("data.created", {"id": 1})
        broker.publish("data.created", {"id": 2})
        broker.publish("data.updated", {"id": 3})

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            path = f.name
        try:
            broker.persist(path)

            with open(path) as fh:
                saved = json.load(fh)
            assert len(saved["messages"]) == 3

            broker2 = MessageBroker()
            sink2: list[Any] = []

            def replay_handler(topic: str, payload: Any) -> None:
                sink2.append(payload)

            broker2.subscribe("data.created", replay_handler)
            broker2.subscribe("data.updated", replay_handler)

            replayed = broker2.replay(path)
            assert replayed == 3
            assert len(sink2) == 3
        finally:
            os.unlink(path)

    def test_persist_respects_max_persisted(self) -> None:
        broker = MessageBroker(max_persisted=5)

        def handler(topic: str, payload: Any) -> None:
            pass

        broker.subscribe("log", handler)
        for i in range(10):
            broker.publish("log", i)

        assert broker.message_count == 5
        assert broker.snapshot()["message_count"] == 5


# ---------------------------------------------------------------------------
# snapshot and introspection
# ---------------------------------------------------------------------------


class TestSnapshot:
    def test_snapshot_subscription_count(self, broker: MessageBroker) -> None:
        def handler(topic: str, payload: Any) -> None:
            pass

        broker.subscribe("a.b", handler)
        broker.subscribe("c.d", handler)
        snap = broker.snapshot()

        assert snap["subscription_count"] == 2
        assert snap["publish_count"] == 0

    def test_topics_returns_unique_set(self, broker: MessageBroker) -> None:
        def handler(topic: str, payload: Any) -> None:
            pass

        broker.subscribe("#", handler)
        broker.publish("x.y", 1)
        broker.publish("x.y", 2)
        broker.publish("a", 3)

        topics = broker.topics()
        assert topics == {"x.y", "a"}


class TestMessagesForTopic:
    def test_messages_for_topic_with_wildcard(self, broker: MessageBroker) -> None:
        def handler(topic: str, payload: Any) -> None:
            pass

        broker.subscribe("#", handler)
        broker.publish("sensor.temp.kitchen", 72)
        broker.publish("sensor.temp.attic", 90)
        broker.publish("sensor.humidity.kitchen", 55)

        msgs = broker.messages_for_topic("sensor.temp.*")
        assert len(msgs) == 2

    def test_messages_for_topic_exact(self, broker: MessageBroker) -> None:
        def handler(topic: str, payload: Any) -> None:
            pass

        broker.subscribe("#", handler)
        broker.publish("orders.created", 1)
        broker.publish("orders.paid", 2)
        broker.publish("orders.created", 3)

        msgs = broker.messages_for_topic("orders.created")
        assert len(msgs) == 2
        assert [m["payload"] for m in msgs] == [1, 3]


class TestCounterAccuracy:
    def test_counters_accumulate(self, broker: MessageBroker) -> None:
        received: list[Any] = []

        broker.subscribe("counter.test", lambda t, p: received.append(p))

        for i in range(5):
            broker.publish("counter.test", i)

        assert broker.publish_count == 5
        assert broker.deliver_count == 5
        assert len(received) == 5

    def test_counter_persisted_and_restored(self) -> None:
        b1 = MessageBroker()

        def handler(topic: str, payload: Any) -> None:
            pass

        b1.subscribe("c", handler)
        b1.publish("c", 1)
        b1.publish("c", 2)

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            path = f.name
        try:
            b1.persist(path)
            b2 = MessageBroker()
            b2.subscribe("c", handler)
            b2.replay(path)

            assert b2.publish_count == 2
            assert b2.deliver_count == 2
        finally:
            os.unlink(path)
