"""Deep LRU cache and eviction tests.

Tests a thread-safe LRU cache with capacity enforcement, TTL expiration,
hit/miss statistics, and eviction ordering.
"""

from __future__ import annotations

import threading
import time

import pytest

from general_ludd.util.lru_cache import LRUCache


class TestLRUCachePutGet:
    def test_put_then_get_returns_value(self):
        cache = LRUCache[int, str](capacity=10)
        cache.put(1, "one")
        assert cache.get(1) == "one"

    def test_get_missing_key_returns_none(self):
        cache = LRUCache[int, str](capacity=10)
        assert cache.get(999) is None

    def test_put_overwrites_existing_key(self):
        cache = LRUCache[int, str](capacity=10)
        cache.put(1, "first")
        cache.put(1, "second")
        assert cache.get(1) == "second"
        assert len(cache) == 1

    def test_put_does_not_extend_capacity_on_overwrite(self):
        cache = LRUCache[int, str](capacity=2)
        cache.put(1, "a")
        cache.put(2, "b")
        cache.put(1, "overwritten")
        assert len(cache) == 2
        assert cache.get(1) == "overwritten"
        assert cache.get(2) == "b"

    def test_contains_checks_membership(self):
        cache = LRUCache[int, str](capacity=10)
        cache.put(1, "one")
        assert 1 in cache
        assert 2 not in cache

    def test_explicit_delete(self):
        cache = LRUCache[int, str](capacity=10)
        cache.put(1, "one")
        cache.put(2, "two")
        assert cache.delete(1) is True
        assert cache.get(1) is None
        assert 1 not in cache
        assert cache.get(2) == "two"

    def test_delete_missing_key(self):
        cache = LRUCache[int, str](capacity=10)
        assert cache.delete(999) is False


class TestLRUCacheEviction:
    def test_capacity_enforced_lru_eviction(self):
        cache = LRUCache[int, str](capacity=3)
        cache.put(1, "a")
        cache.put(2, "b")
        cache.put(3, "c")
        cache.put(4, "d")
        assert cache.get(1) is None
        assert cache.get(2) == "b"
        assert cache.get(3) == "c"
        assert cache.get(4) == "d"

    def test_get_refreshes_lru_position(self):
        cache = LRUCache[int, str](capacity=3)
        cache.put(1, "a")
        cache.put(2, "b")
        cache.put(3, "c")
        cache.get(1)
        cache.put(4, "d")
        assert cache.get(2) is None
        assert cache.get(1) == "a"
        assert cache.get(3) == "c"
        assert cache.get(4) == "d"

    def test_put_refreshes_lru_position(self):
        cache = LRUCache[int, str](capacity=3)
        cache.put(1, "a")
        cache.put(2, "b")
        cache.put(3, "c")
        cache.put(1, "updated_a")
        cache.put(4, "d")
        assert cache.get(2) is None
        assert cache.get(1) == "updated_a"
        assert cache.get(3) == "c"
        assert cache.get(4) == "d"

    def test_eviction_at_exact_capacity(self):
        cache = LRUCache[int, str](capacity=1)
        cache.put(1, "a")
        cache.put(2, "b")
        assert cache.get(1) is None
        assert cache.get(2) == "b"
        assert len(cache) == 1

    def test_all_items_evicted_one_by_one(self):
        cache = LRUCache[int, str](capacity=3)
        cache.put(1, "a")
        cache.put(2, "b")
        cache.put(3, "c")
        cache.put(4, "d")
        cache.put(5, "e")
        cache.put(6, "f")
        assert len(cache) == 3
        assert cache.get(1) is None
        assert cache.get(2) is None
        assert cache.get(3) is None
        assert cache.get(4) == "d"
        assert cache.get(5) == "e"
        assert cache.get(6) == "f"

    def test_eviction_when_insert_order_is_access_order(self):
        cache = LRUCache[int, str](capacity=3)
        cache.put(1, "a")
        cache.put(2, "b")
        cache.put(3, "c")
        cache.get(1)
        cache.get(3)
        cache.get(1)
        cache.put(4, "d")
        assert cache.get(2) is None
        assert cache.get(1) == "a"
        assert cache.get(3) == "c"


class TestLRUCacheTTLExpiration:
    def test_stale_entry_returns_none(self):
        cache = LRUCache[int, str](capacity=10, ttl_seconds=0.05)
        cache.put(1, "one")
        time.sleep(0.1)
        assert cache.get(1) is None

    def test_no_ttl_entries_never_expire(self):
        cache = LRUCache[int, str](capacity=10, ttl_seconds=None)
        cache.put(1, "one")
        assert cache.get(1) == "one"

    def test_per_entry_ttl_override(self):
        cache = LRUCache[int, str](capacity=10, ttl_seconds=60)
        cache.put(1, "short", ttl_seconds=0.03)
        cache.put(2, "long")
        time.sleep(0.06)
        assert cache.get(1) is None
        assert cache.get(2) == "long"


class TestLRUCacheConcurrentAccess:
    def test_single_writer_does_not_crash(self):
        cache = LRUCache[int, int](capacity=10)

        def writer():
            for i in range(100):
                cache.put(i, i * 2)

        writer()
        assert len(cache) <= 10

    def test_concurrent_readers_and_writers(self):
        cache = LRUCache[int, str](capacity=20)
        [threading.Barrier(2) for _ in range(3)]

        def writer():
            for i in range(200):
                cache.put(i, f"val_{i}")

        def reader():
            for _ in range(200):
                cache.get(5)

        def mixed():
            for i in range(100):
                cache.put(1000 + i, f"mix_{i}")
                _ = cache.get(i)

        t1 = threading.Thread(target=writer)
        t2 = threading.Thread(target=reader)
        t3 = threading.Thread(target=mixed)
        t1.start()
        t2.start()
        t3.start()
        t1.join()
        t2.join()
        t3.join()

    def test_parallel_put_overwrites_stay_consistent(self):
        cache = LRUCache[int, int](capacity=50)

        def insert_range(start, n):
            for i in range(start, start + n):
                cache.put(i, i)

        t1 = threading.Thread(target=insert_range, args=(0, 200))
        t2 = threading.Thread(target=insert_range, args=(100, 200))
        t1.start()
        t2.start()
        t1.join()
        t2.join()
        for key in range(100, 200):
            val = cache.get(key)
            assert val is None or val == key


class TestLRUCacheStats:
    def test_initial_stats_zero(self):
        cache = LRUCache[int, str](capacity=10)
        s = cache.stats()
        assert s["hits"] == 0
        assert s["misses"] == 0
        assert s["evictions"] == 0

    def test_hit_increments(self):
        cache = LRUCache[int, str](capacity=10)
        cache.put(1, "a")
        cache.get(1)
        s = cache.stats()
        assert s["hits"] == 1
        assert s["misses"] == 0

    def test_miss_increments(self):
        cache = LRUCache[int, str](capacity=10)
        cache.get(999)
        s = cache.stats()
        assert s["hits"] == 0
        assert s["misses"] == 1

    def test_eviction_increments(self):
        cache = LRUCache[int, str](capacity=2)
        cache.put(1, "a")
        cache.put(2, "b")
        cache.put(3, "c")
        s = cache.stats()
        assert s["evictions"] == 1

    def test_mixed_stats(self):
        cache = LRUCache[int, str](capacity=3)
        cache.put(1, "a")
        cache.put(2, "b")
        cache.put(3, "c")
        cache.get(1)  # hit
        cache.get(4)  # miss
        cache.put(4, "d")  # evicts 2
        cache.get(2)  # miss (evicted)
        s = cache.stats()
        assert s["hits"] == 1
        assert s["misses"] == 2
        assert s["evictions"] == 1

    def test_clear_stats(self):
        cache = LRUCache[int, str](capacity=10)
        cache.put(1, "a")
        cache.get(1)
        cache.get(2)
        cache.get(1)
        cache.clear()
        s = cache.stats()
        assert s["hits"] == 2
        assert s["misses"] == 1
        assert cache.get(1) is None
        assert len(cache) == 0


class TestLRUCacheEdgeCases:
    def test_zero_capacity_never_stores(self):
        cache = LRUCache[int, str](capacity=0)
        cache.put(1, "one")
        assert cache.get(1) is None
        assert len(cache) == 0

    def test_none_value_stored_retrieval(self):
        cache = LRUCache[int, str | None](capacity=5)
        cache.put(1, None)
        assert 1 in cache
        assert cache.get(1) is None

    def test_type_parameters_work_with_hashable_keys(self):
        cache = LRUCache[tuple[str, int], float](capacity=3)
        cache.put(("a", 1), 3.14)
        cache.put(("b", 2), 2.71)
        assert cache.get(("a", 1)) == pytest.approx(3.14)
        assert cache.get(("b", 2)) == pytest.approx(2.71)

    def test_string_keys(self):
        cache = LRUCache[str, list[int]](capacity=5)
        cache.put("odds", [1, 3, 5])
        cache.put("evens", [2, 4, 6])
        assert cache.get("odds") == [1, 3, 5]
        assert cache.get("evens") == [2, 4, 6]

    def test_large_capacity(self):
        cache = LRUCache[int, int](capacity=10000)
        for i in range(15000):
            cache.put(i, -i)
        assert len(cache) == 10000
        for i in range(5000):
            assert cache.get(i) is None
        for i in range(10000, 15000):
            assert cache.get(i) == -i

    def test_len_and_bool(self):
        cache = LRUCache[int, str](capacity=3)
        assert len(cache) == 0
        assert not bool(cache)
        cache.put(1, "a")
        assert len(cache) == 1
        assert bool(cache)

    def test_len_reflects_live_entry_count(self):
        cache = LRUCache[int, str](capacity=5, ttl_seconds=0.03)
        cache.put(1, "a")
        cache.put(2, "b")
        time.sleep(0.06)
        _ = cache.get(1)
        _ = cache.get(2)
        assert len(cache) == 0
