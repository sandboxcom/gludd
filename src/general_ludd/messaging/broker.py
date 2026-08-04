"""Deep pubsub message broker with topics, queues, fanout, wildcard routing,
persistence, and replay.

Topics use dot-separated segments (e.g. ``sensor.temperature.kitchen``).
Wildcards follow the MQTT convention:

* ``*`` (single-level) — matches exactly one topic segment.
* ``#`` (multi-level) — matches zero or more trailing segments.

Usage::

    broker = MessageBroker()
    broker.subscribe("sensor.*.kitchen", handler, queue="q1")
    broker.subscribe("sensor.#", handler, queue="q2")
    broker.publish("sensor.temperature.kitchen", {"value": 72.0})
    broker.persist("/tmp/broker.json")
    broker.replay("/tmp/broker.json")
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# types
# ---------------------------------------------------------------------------

Handler = Callable[[str, Any], None]


@dataclass
class Subscription:
    pattern: str
    handler: Handler
    queue: str | None = None
    registered_at: float = field(default_factory=time.monotonic)

    def matches(self, topic: str) -> bool:
        return _topic_matches(self.pattern, topic)


@dataclass
class PersistedMessage:
    topic: str
    payload: Any
    timestamp: float


# ---------------------------------------------------------------------------
# wildcard matching
# ---------------------------------------------------------------------------

_WILDCARD_TO_REGEX = {
    "*": r"[^.]+",
    "#": r".*",
}


def _topic_pattern_to_regex(pattern: str) -> str:
    """Convert a topic pattern with ``*`` and ``#`` to a regex string."""
    segments = pattern.split(".")
    regex_parts: list[str] = []
    has_hash = False
    for i, seg in enumerate(segments):
        if seg == "#":
            if i != len(segments) - 1:
                raise ValueError(f"'#' wildcard must be the last segment in pattern {pattern!r}")
            has_hash = True
            regex_parts.append(_WILDCARD_TO_REGEX["#"])
        elif seg == "*":
            regex_parts.append(_WILDCARD_TO_REGEX["*"])
        else:
            regex_parts.append(re.escape(seg))
    if has_hash and len(regex_parts) > 1:
        prefix = r"\.".join(regex_parts[:-1])
        return prefix + r"(?:\." + _WILDCARD_TO_REGEX["#"] + r")?\Z"
    return r"\.".join(regex_parts) + r"\Z"


def _topic_matches(pattern: str, topic: str) -> bool:
    """Return True if *topic* matches *pattern* (supporting ``*`` and ``#``)."""
    regex = _topic_pattern_to_regex(pattern)
    return re.match(regex, topic) is not None


# ---------------------------------------------------------------------------
# MessageBroker
# ---------------------------------------------------------------------------


class MessageBroker:
    """In-process pubsub broker with topics, queues, fanout, wildcard routing,
    persistence, and replay.

    Parameters:
        max_persisted:  Maximum messages retained for replay (default *10_000*).
        clock:          Injectable monotonic clock for deterministic tests.
    """

    def __init__(
        self,
        max_persisted: int = 10_000,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._max_persisted = max_persisted
        self._clock = clock

        self._lock = threading.Lock()

        self._subscriptions: list[Subscription] = []
        self._messages: list[PersistedMessage] = []
        self._publish_count: int = 0
        self._deliver_count: int = 0
        self._error_count: int = 0

    # ------------------------------------------------------------------ properties

    @property
    def subscription_count(self) -> int:
        with self._lock:
            return len(self._subscriptions)

    @property
    def message_count(self) -> int:
        with self._lock:
            return len(self._messages)

    @property
    def publish_count(self) -> int:
        return self._publish_count

    @property
    def deliver_count(self) -> int:
        return self._deliver_count

    @property
    def error_count(self) -> int:
        return self._error_count

    # ------------------------------------------------------------------ subscribe / unsubscribe

    def subscribe(
        self,
        pattern: str,
        handler: Handler,
        queue: str | None = None,
    ) -> None:
        """Register a handler for a topic pattern.

        *pattern* may contain ``*`` (single-segment) and ``#`` (multi-segment) wildcards.
        If *queue* is given, only one subscriber per queue group receives each message.
        """
        with self._lock:
            sub = Subscription(pattern=pattern, handler=handler, queue=queue)
            self._subscriptions.append(sub)

    def unsubscribe(self, pattern: str, handler: Handler) -> bool:
        """Remove a subscription. Returns ``True`` if found and removed."""
        with self._lock:
            before = len(self._subscriptions)
            self._subscriptions = [
                s for s in self._subscriptions if not (s.pattern == pattern and s.handler is handler)
            ]
            return len(self._subscriptions) < before

    def clear(self) -> None:
        """Remove all subscriptions."""
        with self._lock:
            self._subscriptions.clear()
            self._messages.clear()

    # ------------------------------------------------------------------ publish

    def publish(self, topic: str, payload: Any) -> int:
        """Publish a message to *topic*. Returns number of handlers delivered to."""
        with self._lock:
            self._publish_count += 1

            self._messages.append(PersistedMessage(topic=topic, payload=payload, timestamp=self._clock()))
            if len(self._messages) > self._max_persisted:
                self._messages = self._messages[-self._max_persisted :]

            matching = [s for s in self._subscriptions if s.matches(topic)]
            fanout_subs = [s for s in matching if s.queue is None]
            queued_subs: dict[str, list[Subscription]] = OrderedDict()
            for s in matching:
                if s.queue is not None:
                    queued_subs.setdefault(s.queue, []).append(s)

            fanout_subs.extend(q_subs[0] for q_subs in queued_subs.values() if q_subs)

            delivered = 0
            for sub in fanout_subs:
                try:
                    sub.handler(topic, payload)
                    delivered += 1
                except Exception:
                    self._error_count += 1

            self._deliver_count += delivered
            return delivered

    # ------------------------------------------------------------------ persistence

    def persist(self, filepath: str) -> None:
        """Serialize broker state (messages, subscriptions, counters) to JSON."""
        with self._lock:
            state: dict[str, Any] = {
                "version": 1,
                "publish_count": self._publish_count,
                "deliver_count": self._deliver_count,
                "error_count": self._error_count,
                "subscriptions": [
                    {
                        "pattern": s.pattern,
                        "queue": s.queue,
                        "registered_at": s.registered_at,
                    }
                    for s in self._subscriptions
                ],
                "messages": [
                    {"topic": m.topic, "payload": m.payload, "timestamp": m.timestamp} for m in self._messages
                ],
            }
            tmp = filepath + ".tmp"
            with open(tmp, "w") as fh:
                json.dump(state, fh, default=str)
            os.replace(tmp, filepath)

    def replay(self, filepath: str) -> int:
        """Restore broker state and re-deliver persisted messages to current subscribers.

        Returns the number of messages replayed.
        """
        with self._lock:
            with open(filepath) as fh:
                state = json.load(fh)

            self._error_count = state.get("error_count", 0)

            restored_messages = state.get("messages", [])
            replay_count = 0
            for raw in restored_messages:
                msg = PersistedMessage(
                    topic=raw["topic"],
                    payload=raw["payload"],
                    timestamp=raw.get("timestamp", 0.0),
                )
                matching = [s for s in self._subscriptions if s.matches(msg.topic)]
                fanout_subs = [s for s in matching if s.queue is None]
                queued_subs: dict[str, list[Subscription]] = OrderedDict()
                for s in matching:
                    if s.queue is not None:
                        queued_subs.setdefault(s.queue, []).append(s)
                to_deliver = fanout_subs + [q_subs[0] for q_subs in queued_subs.values() if q_subs]
                for sub in to_deliver:
                    try:
                        sub.handler(msg.topic, msg.payload)
                        self._deliver_count += 1
                        replay_count += 1
                    except Exception:
                        self._error_count += 1
                self._messages.append(msg)

            self._publish_count = state.get("publish_count", 0)

            return replay_count

    def snapshot(self) -> dict[str, Any]:
        """Return current broker state as a dict (for introspection / testing)."""
        with self._lock:
            return {
                "subscription_count": len(self._subscriptions),
                "message_count": len(self._messages),
                "publish_count": self._publish_count,
                "deliver_count": self._deliver_count,
                "error_count": self._error_count,
                "subscriptions": [{"pattern": s.pattern, "queue": s.queue} for s in self._subscriptions],
                "recent_topics": [m.topic for m in self._messages[-10:]],
            }

    def topics(self) -> set[str]:
        """Return the set of all unique topics that have been published to."""
        with self._lock:
            return {m.topic for m in self._messages}

    def messages_for_topic(self, topic: str) -> list[dict[str, Any]]:
        """Return all persisted messages for *topic* (most recent last)."""
        with self._lock:
            return [
                {"topic": m.topic, "payload": m.payload, "timestamp": m.timestamp}
                for m in self._messages
                if _topic_matches(topic, m.topic)
            ]
