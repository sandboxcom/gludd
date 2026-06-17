"""A bounded in-process buffer for pushed telemetry records.

Modelled on the ``RecentTracesBuffer`` deque precedent
(:mod:`general_ludd.observability.trace_store`): pushed records land in a bounded
ring and are drained by a downstream consumer (the event loop / a connector
bridge). Bounding is the whole point — a hostile or runaway pusher must never be
able to grow the daemon's memory without limit.

Two overflow policies, chosen by the operator at construction:

* :attr:`OverflowPolicy.DROP_OLDEST` — the default; a full buffer evicts its
  oldest record to make room (lossy-but-available; backpressure is *not* signalled
  to the pusher). The ``deque(maxlen=...)`` does this in O(1).
* :attr:`OverflowPolicy.REJECT` — a full buffer refuses the new record and
  :meth:`offer` returns ``False`` so the router can answer **503** (backpressure
  is signalled; nothing already buffered is lost).

Retention is by record count (``maxlen``) and, optionally, by age:
:meth:`offer` stamps each record's monotonic arrival time and
:meth:`drain` / :meth:`snapshot` evict anything older than ``retention_s`` first.

This module is pure (no I/O, no global state) and total — a malformed record is
stored as-is (the parser layer already normalized/validated shape); nothing here
raises on the hot path.
"""

from __future__ import annotations

import enum
import threading
import time
from collections import deque
from typing import Any

__all__ = [
    "DEFAULT_RECEIVER_MAXLEN",
    "OverflowPolicy",
    "ReceiverBuffer",
]

# Default ring capacity. Sized so a momentary burst is absorbed without letting a
# stuck consumer grow memory unbounded; tune per deployment at construction.
DEFAULT_RECEIVER_MAXLEN = 10_000


class OverflowPolicy(enum.StrEnum):
    """What a full buffer does with a newly offered record."""

    DROP_OLDEST = "drop_oldest"
    REJECT = "reject"


class ReceiverBuffer:
    """A bounded, optionally age-retained ring of pushed telemetry records.

    Thread-safe (a lock guards every mutation) because the FastAPI router writes
    from request handlers while a consumer drains concurrently. Newest records
    are returned LAST by :meth:`drain` (FIFO delivery order) and FIRST by
    :meth:`snapshot` (most-recent-first inspection), matching the trace-store
    precedent.
    """

    def __init__(
        self,
        maxlen: int = DEFAULT_RECEIVER_MAXLEN,
        *,
        overflow: OverflowPolicy = OverflowPolicy.DROP_OLDEST,
        retention_s: float | None = None,
    ) -> None:
        if maxlen <= 0:
            raise ValueError("maxlen must be positive")
        self._maxlen = maxlen
        self._overflow = overflow
        self._retention_s = retention_s
        # Each item is ``(arrival_monotonic, record)``.
        self._items: deque[tuple[float, dict[str, Any]]] = deque(maxlen=maxlen)
        self._lock = threading.Lock()
        self._total_offered = 0
        self._total_dropped = 0
        self._total_rejected = 0

    # -- introspection ----------------------------------------------------- #
    @property
    def maxlen(self) -> int:
        return self._maxlen

    @property
    def overflow(self) -> OverflowPolicy:
        return self._overflow

    @property
    def total_offered(self) -> int:
        return self._total_offered

    @property
    def total_dropped(self) -> int:
        """Records evicted by DROP_OLDEST overflow OR by age retention."""
        return self._total_dropped

    @property
    def total_rejected(self) -> int:
        """Records refused by REJECT overflow (the 503 backpressure count)."""
        return self._total_rejected

    def __len__(self) -> int:
        with self._lock:
            self._evict_expired_locked(time.monotonic())
            return len(self._items)

    def is_full(self) -> bool:
        """True when the ring is at capacity (after expiring aged-out records)."""
        with self._lock:
            self._evict_expired_locked(time.monotonic())
            return len(self._items) >= self._maxlen

    # -- write ------------------------------------------------------------- #
    def offer(self, record: dict[str, Any]) -> bool:
        """Add one record. Returns False ONLY when REJECT overflow refused it.

        DROP_OLDEST always accepts (evicting the oldest if full) and returns True.
        REJECT returns False (and stores nothing) when the buffer is full.
        """
        now = time.monotonic()
        with self._lock:
            self._total_offered += 1
            self._evict_expired_locked(now)
            if len(self._items) >= self._maxlen:
                if self._overflow is OverflowPolicy.REJECT:
                    self._total_rejected += 1
                    return False
                # DROP_OLDEST: the deque maxlen will evict on append, but count it
                # explicitly so the drop metric is honest.
                self._total_dropped += 1
            self._items.append((now, record))
            return True

    def offer_many(self, records: list[dict[str, Any]]) -> int:
        """Offer each record; return the count actually accepted."""
        accepted = 0
        for record in records:
            if self.offer(record):
                accepted += 1
        return accepted

    # -- read / drain ------------------------------------------------------ #
    def drain(self, limit: int | None = None) -> list[dict[str, Any]]:
        """Remove and return up to ``limit`` records, oldest-first (FIFO).

        Aged-out records are expired (not returned) before draining. ``limit=None``
        drains everything currently retained.
        """
        now = time.monotonic()
        out: list[dict[str, Any]] = []
        with self._lock:
            self._evict_expired_locked(now)
            count = len(self._items) if limit is None else min(limit, len(self._items))
            for _ in range(count):
                out.append(self._items.popleft()[1])
        return out

    def snapshot(self, limit: int | None = None) -> dict[str, Any]:
        """Non-destructive, JSON-safe view: most-recent-first records + counters."""
        now = time.monotonic()
        with self._lock:
            self._evict_expired_locked(now)
            items = list(reversed(self._items))
            if limit is not None:
                items = items[:limit]
            recent = [rec for _, rec in items]
            return {
                "size": len(self._items),
                "maxlen": self._maxlen,
                "overflow": self._overflow.value,
                "retention_s": self._retention_s,
                "total_offered": self._total_offered,
                "total_dropped": self._total_dropped,
                "total_rejected": self._total_rejected,
                "recent": recent,
            }

    def clear(self) -> None:
        with self._lock:
            self._items.clear()

    # -- internal ---------------------------------------------------------- #
    def _evict_expired_locked(self, now: float) -> None:
        """Drop records older than ``retention_s`` from the front. Caller holds lock."""
        if self._retention_s is None:
            return
        cutoff = now - self._retention_s
        while self._items and self._items[0][0] < cutoff:
            self._items.popleft()
            self._total_dropped += 1
