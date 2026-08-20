"""Memoization decorators with LRU, TTL, LFU, and size-based eviction strategies."""

from __future__ import annotations

import functools
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from typing import Any, TypeVar, cast

F = TypeVar("F", bound=Callable[..., Any])


class _CacheBackend:
    def get(self, key: tuple[Any, ...]) -> Any | None:
        raise NotImplementedError

    def put(self, key: tuple[Any, ...], value: Any) -> None:
        raise NotImplementedError

    def clear(self) -> None:
        raise NotImplementedError

    def stats(self) -> dict[str, int]:
        raise NotImplementedError

    def __len__(self) -> int:
        raise NotImplementedError


class _LRUBackend(_CacheBackend):
    def __init__(self, maxsize: int) -> None:
        self._maxsize = max(0, maxsize)
        self._data: OrderedDict[tuple[Any, ...], Any] = OrderedDict()
        self._hits = 0
        self._misses = 0
        self._evictions = 0

    def get(self, key: tuple[Any, ...]) -> Any | None:
        if key not in self._data:
            self._misses += 1
            return None
        self._data.move_to_end(key)
        self._hits += 1
        return self._data[key]

    def put(self, key: tuple[Any, ...], value: Any) -> None:
        if self._maxsize == 0:
            return
        if key in self._data:
            self._data.move_to_end(key)
        self._data[key] = value
        while len(self._data) > self._maxsize:
            self._data.popitem(last=False)
            self._evictions += 1

    def clear(self) -> None:
        self._data.clear()
        self._hits = 0
        self._misses = 0
        self._evictions = 0

    def stats(self) -> dict[str, int]:
        return {"hits": self._hits, "misses": self._misses, "evictions": self._evictions}

    def __len__(self) -> int:
        return len(self._data)


class _LFUBackend(_CacheBackend):
    def __init__(self, maxsize: int) -> None:
        self._maxsize = max(0, maxsize)
        self._data: dict[tuple[Any, ...], Any] = {}
        self._freq: dict[tuple[Any, ...], int] = {}
        self._hits = 0
        self._misses = 0
        self._evictions = 0

    def get(self, key: tuple[Any, ...]) -> Any | None:
        if key not in self._data:
            self._misses += 1
            return None
        self._freq[key] = self._freq.get(key, 0) + 1
        self._hits += 1
        return self._data[key]

    def put(self, key: tuple[Any, ...], value: Any) -> None:
        if self._maxsize == 0:
            return
        if key not in self._data:
            self._freq[key] = 0
        self._data[key] = value
        while len(self._data) > self._maxsize:
            lfu_key = min(self._freq, key=lambda k: self._freq[k])
            del self._data[lfu_key]
            del self._freq[lfu_key]
            self._evictions += 1

    def clear(self) -> None:
        self._data.clear()
        self._freq.clear()
        self._hits = 0
        self._misses = 0
        self._evictions = 0

    def stats(self) -> dict[str, int]:
        return {"hits": self._hits, "misses": self._misses, "evictions": self._evictions}

    def __len__(self) -> int:
        return len(self._data)


class _TTLBackend(_CacheBackend):
    def __init__(self, ttl_seconds: float, maxsize: int) -> None:
        self._ttl = ttl_seconds
        self._maxsize = max(0, maxsize)
        self._data: dict[tuple[Any, ...], tuple[Any, float]] = {}
        self._hits = 0
        self._misses = 0
        self._evictions = 0
        self._lock = threading.Lock()

    def get(self, key: tuple[Any, ...]) -> Any | None:
        with self._lock:
            if key not in self._data:
                self._misses += 1
                return None
            value, expires_at = self._data[key]
            if time.monotonic() >= expires_at:
                del self._data[key]
                self._misses += 1
                self._evictions += 1
                return None
            self._hits += 1
            return value

    def put(self, key: tuple[Any, ...], value: Any) -> None:
        with self._lock:
            if self._maxsize == 0:
                return
            if len(self._data) >= self._maxsize and key not in self._data:
                oldest = min(self._data, key=lambda k: self._data[k][1])
                del self._data[oldest]
                self._evictions += 1
            self._data[key] = (value, time.monotonic() + self._ttl)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()
            self._hits = 0
            self._misses = 0
            self._evictions = 0

    def stats(self) -> dict[str, int]:
        with self._lock:
            return {"hits": self._hits, "misses": self._misses, "evictions": self._evictions}

    def __len__(self) -> int:
        with self._lock:
            return len(self._data)


class _SizeBackend(_CacheBackend):
    def __init__(self, maxsize: int, max_item_bytes: int) -> None:
        self._maxsize = max(0, maxsize)
        self._max_item_bytes = max_item_bytes
        self._data: OrderedDict[tuple[Any, ...], tuple[Any, int]] = OrderedDict()
        self._total_bytes = 0
        self._hits = 0
        self._misses = 0
        self._evictions = 0

    def _item_size(self, value: Any) -> int:
        try:
            return len(value)
        except TypeError:
            return 0

    def get(self, key: tuple[Any, ...]) -> Any | None:
        if key not in self._data:
            self._misses += 1
            return None
        value, _ = self._data[key]
        self._data.move_to_end(key)
        self._hits += 1
        return value

    def put(self, key: tuple[Any, ...], value: Any) -> None:
        if self._maxsize == 0:
            return
        item_bytes = self._item_size(value)
        if self._max_item_bytes > 0 and item_bytes > self._max_item_bytes:
            return
        if key in self._data:
            _, old_size = self._data.pop(key)
            self._total_bytes -= old_size
        self._data[key] = (value, item_bytes)
        self._total_bytes += item_bytes
        while len(self._data) > self._maxsize and self._data:
            _oldest_key, (_, oldest_size) = self._data.popitem(last=False)
            self._total_bytes -= oldest_size
            self._evictions += 1

    def clear(self) -> None:
        self._data.clear()
        self._total_bytes = 0
        self._hits = 0
        self._misses = 0
        self._evictions = 0

    def stats(self) -> dict[str, int]:
        return {
            "hits": self._hits,
            "misses": self._misses,
            "evictions": self._evictions,
            "total_bytes": self._total_bytes,
        }

    def __len__(self) -> int:
        return len(self._data)


def _memoize(backend: _CacheBackend, lock: threading.Lock | None = None) -> Callable[[F], F]:
    if lock is None:
        lock = threading.Lock()

    def decorator(func: F) -> F:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            key = (args, tuple(sorted(kwargs.items())))
            with lock:
                result = backend.get(key)
                if result is not None:
                    return result
            computed = func(*args, **kwargs)
            with lock:
                backend.put(key, computed)
            return computed

        wrapper.__dict__.update(
            cache=backend,
            cache_clear=backend.clear,
            cache_stats=backend.stats,
        )
        return cast(F, wrapper)

    return decorator


def memoize_lru(maxsize: int = 128) -> Callable[[F], F]:
    """Execute ``memoize_lru``."""
    backend = _LRUBackend(maxsize)
    return _memoize(backend)


def memoize_lfu(maxsize: int = 128) -> Callable[[F], F]:
    """Execute ``memoize_lfu``."""
    backend = _LFUBackend(maxsize)
    return _memoize(backend)


def memoize_ttl(ttl_seconds: float, maxsize: int = 128) -> Callable[[F], F]:
    """Execute ``memoize_ttl``."""
    backend = _TTLBackend(ttl_seconds, maxsize)
    return _memoize(backend)


def memoize_size(maxsize: int = 128, max_item_bytes: int = 0) -> Callable[[F], F]:
    """Execute ``memoize_size``."""
    backend = _SizeBackend(maxsize, max_item_bytes)
    return _memoize(backend)
