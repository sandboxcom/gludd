"""Deep tests for memoize decorators: LRU, TTL, LFU, size-based eviction, thread safety."""

from __future__ import annotations

import threading
import time
from typing import Any

_CACHE_ATTR = "cache"
_CLEAR_ATTR = "cache_clear"
_STATS_ATTR = "cache_stats"


def _cache_len(fn: Any) -> int:
    return len(getattr(fn, _CACHE_ATTR))


def _cache_stats(fn: Any) -> dict:
    return getattr(fn, _STATS_ATTR)()


def _cache_clear(fn: Any) -> None:
    getattr(fn, _CLEAR_ATTR)()


class TestMemoizeLRU:
    def test_cache_hit_returns_cached_value(self) -> None:
        from general_ludd.util.memoize import memoize_lru

        call_count = 0

        @memoize_lru(maxsize=10)
        def expensive(x: int) -> int:
            nonlocal call_count
            call_count += 1
            return x * 2

        assert expensive(5) == 10
        assert call_count == 1
        assert expensive(5) == 10
        assert call_count == 1

    def test_cache_miss_calls_function(self) -> None:
        from general_ludd.util.memoize import memoize_lru

        call_count = 0

        @memoize_lru(maxsize=10)
        def compute(x: int) -> int:
            nonlocal call_count
            call_count += 1
            return x + 1

        assert compute(1) == 2
        assert compute(2) == 3
        assert compute(3) == 4
        assert call_count == 3

    def test_lru_eviction_when_full(self) -> None:
        from general_ludd.util.memoize import memoize_lru

        call_count = 0

        @memoize_lru(maxsize=3)
        def identity(x: int) -> int:
            nonlocal call_count
            call_count += 1
            return x

        identity(1)
        identity(2)
        identity(3)
        assert call_count == 3
        identity(1)
        assert call_count == 3
        identity(4)
        assert call_count == 4
        identity(2)
        assert call_count == 5

    def test_stats_hits_and_misses(self) -> None:
        from general_ludd.util.memoize import memoize_lru

        @memoize_lru(maxsize=5)
        def fn(x: int) -> int:
            return x

        fn(1)
        fn(1)
        fn(2)
        s = _cache_stats(fn)
        assert s["hits"] == 1
        assert s["misses"] == 2

    def test_clear_empties_cache(self) -> None:
        from general_ludd.util.memoize import memoize_lru

        call_count = 0

        @memoize_lru(maxsize=5)
        def fn(x: int) -> int:
            nonlocal call_count
            call_count += 1
            return x

        fn(1)
        fn(2)
        assert call_count == 2
        _cache_clear(fn)
        assert _cache_len(fn) == 0
        fn(1)
        assert call_count == 3

    def test_zero_maxsize_never_caches(self) -> None:
        from general_ludd.util.memoize import memoize_lru

        call_count = 0

        @memoize_lru(maxsize=0)
        def fn(x: int) -> int:
            nonlocal call_count
            call_count += 1
            return x

        fn(1)
        fn(1)
        fn(1)
        assert call_count == 3

    def test_kwargs_produce_different_keys(self) -> None:
        from general_ludd.util.memoize import memoize_lru

        call_count = 0

        @memoize_lru(maxsize=10)
        def fn(a: int, b: int = 0) -> int:
            nonlocal call_count
            call_count += 1
            return a + b

        assert fn(5) == 5
        assert call_count == 1
        assert fn(5) == 5
        assert call_count == 1
        assert fn(5, b=1) == 6
        assert call_count == 2

    def test_wraps_preserves_function_identity(self) -> None:
        from general_ludd.util.memoize import memoize_lru

        @memoize_lru(maxsize=5)
        def my_func(x: int) -> int:
            """Docstring for my_func."""
            return x

        assert my_func.__name__ == "my_func"
        assert my_func.__doc__ == "Docstring for my_func."


class TestMemoizeLFU:
    def test_lfu_evicts_least_used(self) -> None:
        from general_ludd.util.memoize import memoize_lfu

        call_count = 0

        @memoize_lfu(maxsize=3)
        def identity(x: int) -> int:
            nonlocal call_count
            call_count += 1
            return x

        identity(1)
        identity(1)
        identity(2)
        identity(3)
        assert call_count == 3
        identity(4)
        assert call_count == 4
        identity(1)
        assert call_count == 4

    def test_lfu_hit_increments_frequency(self) -> None:
        from general_ludd.util.memoize import memoize_lfu

        @memoize_lfu(maxsize=10)
        def fn(x: int) -> int:
            return x

        fn(1)
        fn(1)
        fn(1)
        fn(2)
        s = _cache_stats(fn)
        assert s["hits"] == 2
        assert s["misses"] == 2


class TestMemoizeTTL:
    def test_ttl_expires_entry(self) -> None:
        from general_ludd.util.memoize import memoize_ttl

        call_count = 0

        @memoize_ttl(ttl_seconds=0.05, maxsize=10)
        def fn(x: int) -> int:
            nonlocal call_count
            call_count += 1
            return x

        assert fn(1) == 1
        assert call_count == 1
        assert fn(1) == 1
        assert call_count == 1
        time.sleep(0.1)
        assert fn(1) == 1
        assert call_count == 2

    def test_ttl_not_expired_returns_cached(self) -> None:
        from general_ludd.util.memoize import memoize_ttl

        call_count = 0

        @memoize_ttl(ttl_seconds=5.0, maxsize=10)
        def fn(x: int) -> int:
            nonlocal call_count
            call_count += 1
            return x

        fn(1)
        fn(1)
        fn(1)
        assert call_count == 1


class TestMemoizeSize:
    def test_size_eviction_when_capacity_reached(self) -> None:
        from general_ludd.util.memoize import memoize_size

        call_count = 0

        @memoize_size(maxsize=2)
        def fn(s: str) -> str:
            nonlocal call_count
            call_count += 1
            return s

        fn("a")
        fn("b")
        assert call_count == 2
        fn("a")
        assert call_count == 2
        fn("c")
        assert call_count == 3
        fn("b")
        assert call_count == 4

    def test_max_item_bytes_filters_oversized(self) -> None:
        from general_ludd.util.memoize import memoize_size

        call_count = 0

        @memoize_size(maxsize=10, max_item_bytes=3)
        def fn(s: str) -> str:
            nonlocal call_count
            call_count += 1
            return s

        fn("ab")
        fn("ab")
        assert call_count == 1
        fn("long_string")
        fn("long_string")
        assert call_count == 3


class TestThreadSafety:
    def test_concurrent_access_no_corruption(self) -> None:
        from general_ludd.util.memoize import memoize_lru

        call_count = 0
        lock = threading.Lock()

        @memoize_lru(maxsize=50)
        def fn(x: int) -> int:
            with lock:
                nonlocal call_count
                call_count += 1
            return x * x

        threads = []
        for _ in range(4):
            t = threading.Thread(target=lambda: [fn(i) for i in range(20)])
            threads.append(t)
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert call_count == 20
        s = _cache_stats(fn)
        assert s["hits"] > 0

    def test_ttl_backend_thread_safety(self) -> None:
        from general_ludd.util.memoize import memoize_ttl

        @memoize_ttl(ttl_seconds=10, maxsize=100)
        def fn(x: int) -> int:
            return x

        errors = []

        def worker():
            try:
                for i in range(50):
                    fn(i)
                    fn(i)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert _cache_len(fn) <= 100


class TestMemoizeLarge:
    def test_large_cache_many_entries(self) -> None:
        from general_ludd.util.memoize import memoize_lru

        @memoize_lru(maxsize=1000)
        def fn(x: int) -> int:
            return x * 3

        for i in range(2000):
            fn(i)
        assert _cache_len(fn) == 1000
        s = _cache_stats(fn)
        assert s["evictions"] == 1000
        assert s["misses"] == 2000
