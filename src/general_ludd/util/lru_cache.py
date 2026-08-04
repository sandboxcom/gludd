"""Thread-safe LRU cache with capacity enforcement, TTL, and hit/miss stats."""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from typing import Generic, TypeVar

K = TypeVar("K")
V = TypeVar("V")

_EntryID = int


class LRUCache(Generic[K, V]):
    def __init__(
        self,
        capacity: int,
        ttl_seconds: float | None = None,
    ) -> None:
        self._capacity = max(0, capacity)
        self._ttl_default = None if ttl_seconds is None else float(ttl_seconds)
        self._data: dict[K, tuple[V, float | None, _EntryID]] = {}
        self._order: OrderedDict[_EntryID, None] = OrderedDict()
        self._lock = threading.Lock()
        self._hits = 0
        self._misses = 0
        self._evictions = 0
        self._id_counter = 0

    def _next_id(self) -> _EntryID:
        self._id_counter += 1
        return self._id_counter

    def get(self, key: K) -> V | None:
        with self._lock:
            if key not in self._data:
                self._misses += 1
                return None
            value, expires_at, entry_id = self._data[key]
            if expires_at is not None and time.monotonic() >= expires_at:
                del self._data[key]
                self._order.pop(entry_id, None)
                self._misses += 1
                return None
            self._order.move_to_end(entry_id)
            self._hits += 1
            return value

    def put(self, key: K, value: V, ttl_seconds: float | None = None) -> None:
        effective_ttl = self._ttl_default if ttl_seconds is None else ttl_seconds
        expires_at: float | None = None
        if effective_ttl is not None:
            if effective_ttl <= 0:
                return
            expires_at = time.monotonic() + float(effective_ttl)

        with self._lock:
            if self._capacity == 0:
                return
            if key in self._data:
                old_entry_id = self._data[key][2]
                self._order.pop(old_entry_id, None)
            entry_id = self._next_id()
            self._data[key] = (value, expires_at, entry_id)
            self._order[entry_id] = None
            while len(self._order) > self._capacity:
                evicted_id, _ = self._order.popitem(last=False)
                for k, (_, _, eid) in list(self._data.items()):
                    if eid == evicted_id:
                        del self._data[k]
                        self._evictions += 1
                        break

    def delete(self, key: K) -> bool:
        with self._lock:
            if key not in self._data:
                return False
            _, _, entry_id = self._data.pop(key)
            self._order.pop(entry_id, None)
            return True

    def clear(self) -> None:
        with self._lock:
            self._data.clear()
            self._order.clear()

    def stats(self) -> dict[str, int]:
        with self._lock:
            return {
                "hits": self._hits,
                "misses": self._misses,
                "evictions": self._evictions,
            }

    def __len__(self) -> int:
        with self._lock:
            return len(self._data)

    def __bool__(self) -> bool:
        return len(self) > 0

    def __contains__(self, key: object) -> bool:
        with self._lock:
            return key in self._data
