"""Bounded write queue for the IPC layer (Phase 1 of the gunicorn multi-worker architecture).

When the daemon runs under gunicorn with multiple workers, each worker needs
a bounded buffer to absorb bursts of outgoing messages before they are
drained by the broker's publish loop. Without a bound, a stuck consumer
(a worker whose broker connection has stalled) could grow the daemon's
memory without limit — the same failure mode :class:`ReceiverBuffer` guards
against on the ingress side.

Two overflow policies, mirroring ``receiver.buffer.OverflowPolicy``:

* :attr:`OverflowPolicy.DROP_OLDEST` — the default; a full queue evicts its
  oldest envelope to make room (lossy-but-available; backpressure is *not*
  signalled to the producer). Backed by ``deque(maxlen=...)`` for O(1) eviction.
* :attr:`OverflowPolicy.REJECT` — a full queue refuses the new envelope and
  :meth:`put` returns ``False`` so the producer can apply backpressure
  (e.g. answer HTTP 503 / retry) without losing anything already buffered.

This module is pure (no I/O, no global state). It is async because the broker
delivery loop and the daemon request handlers both run inside the event loop.
"""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

__all__ = [
    "DEFAULT_WRITE_QUEUE_MAXSIZE",
    "Envelope",
    "OverflowPolicy",
    "WriteQueue",
]

DEFAULT_WRITE_QUEUE_MAXSIZE = 1000


class OverflowPolicy(Enum):
    """What a full queue does with a newly offered envelope."""

    DROP_OLDEST = "drop_oldest"
    REJECT = "reject"


@dataclass
class Envelope:
    """A single queued message: its topic and the payload dict.

    A dataclass (rather than a bare tuple) so the broker delivery loop and
    any future tracing/metrics layer can reference fields by name.
    """

    topic: str
    payload: dict[str, Any] = field(default_factory=dict)


class WriteQueue:
    """A bounded, async-safe FIFO queue of :class:`Envelope` objects.

    Thread-safety is not required (the queue lives inside one event loop),
    but the async :class:`asyncio.Lock` serializes ``put``/``get`` so a
    burst of concurrent producers cannot interleave half-written state
    during the overflow check + append pair under ``REJECT``.
    """

    def __init__(
        self,
        maxsize: int = DEFAULT_WRITE_QUEUE_MAXSIZE,
        policy: OverflowPolicy = OverflowPolicy.DROP_OLDEST,
    ) -> None:
        if maxsize <= 0:
            raise ValueError("maxsize must be positive")
        self._maxsize = maxsize
        self._policy = policy
        # DROP_OLDEST delegates eviction to deque(maxlen=...); REJECT needs a
        # plain deque so it can inspect len() and refuse the new item.
        self._queue: deque[Envelope] = deque(
            maxlen=maxsize if policy == OverflowPolicy.DROP_OLDEST else None
        )
        self._lock = asyncio.Lock()
        self._not_empty = asyncio.Event()
        self._total_offered = 0
        self._total_dropped = 0
        self._total_rejected = 0

    @property
    def maxsize(self) -> int:
        return self._maxsize

    @property
    def policy(self) -> OverflowPolicy:
        return self._policy

    @property
    def total_offered(self) -> int:
        return self._total_offered

    @property
    def total_dropped(self) -> int:
        """Envelopes evicted by DROP_OLDEST overflow."""
        return self._total_dropped

    @property
    def total_rejected(self) -> int:
        """Envelopes refused by REJECT overflow."""
        return self._total_rejected

    def __len__(self) -> int:
        return len(self._queue)

    def is_full(self) -> bool:
        return len(self._queue) >= self._maxsize

    async def put(self, envelope: Envelope) -> bool:
        """Append ``envelope``. Returns False ONLY when REJECT refused it.

        DROP_OLDEST always accepts (evicting the oldest if full) and returns True.
        REJECT returns False (and stores nothing) when the queue is full.
        """
        async with self._lock:
            self._total_offered += 1
            if self._policy == OverflowPolicy.REJECT and len(self._queue) >= self._maxsize:
                self._total_rejected += 1
                return False
            # DROP_OLDEST: deque(maxlen=...) evicts silently; count it so the
            # drop metric is honest.
            if (
                self._policy == OverflowPolicy.DROP_OLDEST
                and len(self._queue) >= self._maxsize
            ):
                self._total_dropped += 1
            self._queue.append(envelope)
            self._not_empty.set()
            return True

    async def get(self) -> Envelope:
        """Remove and return the oldest envelope (FIFO).

        Blocks (awaits) while the queue is empty. Once an envelope is returned
        the caller owns it; no redelivery is attempted.
        """
        while True:
            async with self._lock:
                if self._queue:
                    envelope = self._queue.popleft()
                    if not self._queue:
                        self._not_empty.clear()
                    return envelope
            await self._not_empty.wait()

    def snapshot(self) -> dict[str, Any]:
        """Non-destructive, JSON-safe view: queue size + counters."""
        return {
            "size": len(self._queue),
            "maxsize": self._maxsize,
            "policy": self._policy.value,
            "total_offered": self._total_offered,
            "total_dropped": self._total_dropped,
            "total_rejected": self._total_rejected,
        }

    def clear(self) -> None:
        self._queue.clear()
        self._not_empty.clear()
