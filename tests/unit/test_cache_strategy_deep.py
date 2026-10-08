"""Deep cache strategy tests: key generation, TTL, invalidation, eviction, concurrency."""

from __future__ import annotations

import functools
import json
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_cache_dir():
    with tempfile.TemporaryDirectory(prefix="gludd_test_cache_") as d:
        yield d


# ---------------------------------------------------------------------------
# ModelResponseCache — _make_cache_key
# ---------------------------------------------------------------------------


class TestMakeCacheKey:
    def test_deterministic_same_inputs_same_key(self):
        from general_ludd.models.response_cache import _make_cache_key

        msgs = [{"role": "user", "content": "hello"}]
        k1 = _make_cache_key("profile-a", msgs, model_name="gpt-4")
        k2 = _make_cache_key("profile-a", msgs, model_name="gpt-4")
        assert k1 == k2
        assert isinstance(k1, str)
        assert len(k1) == 64

    def test_different_profile_yields_different_key(self):
        from general_ludd.models.response_cache import _make_cache_key

        msgs = [{"role": "user", "content": "hello"}]
        k1 = _make_cache_key("profile-a", msgs)
        k2 = _make_cache_key("profile-b", msgs)
        assert k1 != k2

    def test_different_model_name_yields_different_key(self):
        from general_ludd.models.response_cache import _make_cache_key

        msgs = [{"role": "user", "content": "hello"}]
        k1 = _make_cache_key("p", msgs, model_name="gpt-4")
        k2 = _make_cache_key("p", msgs, model_name="claude-3")
        assert k1 != k2

    def test_key_is_sha256_hex(self):
        from general_ludd.models.response_cache import _make_cache_key

        k = _make_cache_key("x", [{"role": "user", "content": "hi"}])
        assert len(k) == 64
        assert all(c in "0123456789abcdef" for c in k)

    def test_message_order_matters(self):
        from general_ludd.models.response_cache import _make_cache_key

        msgs_a = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ]
        msgs_b = [
            {"role": "assistant", "content": "hi"},
            {"role": "user", "content": "hello"},
        ]
        k1 = _make_cache_key("p", msgs_a)
        k2 = _make_cache_key("p", msgs_b)
        assert k1 != k2

    def test_none_model_name_is_handled(self):
        from general_ludd.models.response_cache import _make_cache_key

        msgs = [{"role": "user", "content": "hi"}]
        k1 = _make_cache_key("p", msgs, model_name=None)
        k2 = _make_cache_key("p", msgs, model_name=None)
        assert k1 == k2

    def test_extra_kwargs_change_key(self):
        from general_ludd.models.response_cache import _make_cache_key

        msgs = [{"role": "user", "content": "hi"}]
        k1 = _make_cache_key("p", msgs, temperature=0.7)
        k2 = _make_cache_key("p", msgs, temperature=1.0)
        assert k1 != k2


# ---------------------------------------------------------------------------
# ModelResponseCache — diskcache-backed get/set/invalidate
# ---------------------------------------------------------------------------


class TestModelResponseCache:
    def test_set_and_get_roundtrip(self, tmp_cache_dir):
        from general_ludd.models.response_cache import ModelResponseCache

        cache = ModelResponseCache(cache_dir=tmp_cache_dir)
        cache.set("key1", {"answer": 42}, expire=3600)
        result = cache.get("key1")
        cache.close()
        assert result == {"answer": 42}

    def test_get_missing_key_returns_none(self, tmp_cache_dir):
        from general_ludd.models.response_cache import ModelResponseCache

        cache = ModelResponseCache(cache_dir=tmp_cache_dir)
        result = cache.get("nonexistent")
        cache.close()
        assert result is None

    def test_invalidate_removes_entry(self, tmp_cache_dir):
        from general_ludd.models.response_cache import ModelResponseCache

        cache = ModelResponseCache(cache_dir=tmp_cache_dir)
        cache.set("key1", {"val": 1}, expire=3600)
        cache.invalidate("key1")
        assert cache.get("key1") is None
        cache.close()

    def test_clear_empties_all_entries(self, tmp_cache_dir):
        from general_ludd.models.response_cache import ModelResponseCache

        cache = ModelResponseCache(cache_dir=tmp_cache_dir)
        for i in range(5):
            cache.set(f"key{i}", {"n": i}, expire=3600)
        cache.clear()
        for i in range(5):
            assert cache.get(f"key{i}") is None
        cache.close()

    def test_persistence_across_instances(self, tmp_cache_dir):
        from general_ludd.models.response_cache import ModelResponseCache

        c1 = ModelResponseCache(cache_dir=tmp_cache_dir)
        c1.set("persist", {"data": "survives"}, expire=3600)
        c1.close()

        c2 = ModelResponseCache(cache_dir=tmp_cache_dir)
        result = c2.get("persist")
        c2.close()
        assert result == {"data": "survives"}


# ---------------------------------------------------------------------------
# RendererCache — in-memory TTL dict
# ---------------------------------------------------------------------------


class TestRendererCacheTTL:
    def test_set_and_get_roundtrip(self):
        from general_ludd.renderers.cache import RendererCache

        c = RendererCache()
        c.set("name1", {"rendered": True}, ttl=30)
        assert c.get("name1") == {"rendered": True}

    def test_entry_expires_after_ttl(self):
        from general_ludd.renderers.cache import RendererCache

        c = RendererCache()
        c.set("e", "value", ttl=0.01)
        time.sleep(0.02)
        assert c.get("e") is None

    def test_entry_without_ttl_never_expires(self):
        from general_ludd.renderers.cache import RendererCache

        c = RendererCache(ttl_default=3600)
        c.set("e", "persistent", ttl=99999)
        time.sleep(0.01)
        assert c.get("e") == "persistent"

    def test_ttl_zero_disables_caching(self):
        from general_ludd.renderers.cache import RendererCache

        c = RendererCache()
        c.set("e", "val", ttl=0)
        assert c.get("e") is None

    def test_clear_single_entry(self):
        from general_ludd.renderers.cache import RendererCache

        c = RendererCache()
        c.set("a", 1, ttl=30)
        c.set("b", 2, ttl=30)
        assert c.clear("a") is True
        assert c.get("a") is None
        assert c.get("b") == 2
        assert c.clear("a") is False

    def test_clear_all(self):
        from general_ludd.renderers.cache import RendererCache

        c = RendererCache()
        for i in range(5):
            c.set(f"k{i}", i, ttl=30)
        assert c.clear_all() == 5
        assert len(c) == 0

    def test_contains_detects_ttl_expiry(self):
        from general_ludd.renderers.cache import RendererCache

        c = RendererCache()
        c.set("fresh", "ok", ttl=30)
        c.set("stale", "old", ttl=0.01)
        time.sleep(0.02)
        assert "fresh" in c
        assert "stale" not in c

    def test_overwrite_refreshes_expiry(self):
        from general_ludd.renderers.cache import RendererCache

        c = RendererCache()
        c.set("e", "first", ttl=0.01)
        time.sleep(0.005)
        c.set("e", "second", ttl=30)
        time.sleep(0.01)
        assert c.get("e") == "second"


# ---------------------------------------------------------------------------
# RendererCache — capacity / eviction scenario
# ---------------------------------------------------------------------------


class TestRendererCacheCapacity:
    def test_large_insertion_does_not_auto_evict(self):
        from general_ludd.renderers.cache import RendererCache

        c = RendererCache()
        for i in range(1000):
            c.set(f"k{i}", i, ttl=3600)
        for i in range(1000):
            assert c.get(f"k{i}") == i
        assert len(c) == 1000

    def test_overwrite_does_not_increase_size(self):
        from general_ludd.renderers.cache import RendererCache

        c = RendererCache()
        c.set("a", 1, ttl=30)
        c.set("a", 2, ttl=30)
        assert len(c) == 1

    def test_lazy_expiry_reduces_size_on_access(self):
        from general_ludd.renderers.cache import RendererCache

        c = RendererCache()
        c.set("live", "ok", ttl=30)
        c.set("dead", "goner", ttl=0.01)
        time.sleep(0.02)
        assert c.get("dead") is None
        assert len(c) == 1
        assert c.get("live") == "ok"
        assert "dead" not in c


# ---------------------------------------------------------------------------
# FileClaimRegistry — TTL-based stale detection + reaping
# ---------------------------------------------------------------------------


class TestFileClaimRegistryTTL:
    def test_fresh_claim_not_stale(self):
        from general_ludd.coordination.file_claims import FileClaimRegistry

        clock = iter([0.0, 1.0]).__next__
        registry = FileClaimRegistry(ttl_seconds=900.0, clock=clock)
        registry.claim("w1", ["file_a.py"])
        conflicts = registry.overlaps("w1")
        assert conflicts == {}

    def test_claim_past_ttl_is_reaped(self):
        from general_ludd.coordination.file_claims import FileClaimRegistry

        ticks = [0.0, 1000.0, 1001.0]
        clock = iter(ticks).__next__
        registry = FileClaimRegistry(ttl_seconds=900.0, clock=clock)
        registry.claim("old_worker", ["file_a.py"])
        conflicts = registry.overlaps("old_worker")
        assert conflicts == {}
        assert registry.all_claims() == {}

    def test_two_workers_same_file_conflict(self):
        from general_ludd.coordination.file_claims import FileClaimRegistry

        clock = iter([0.0, 1.0, 2.0]).__next__
        registry = FileClaimRegistry(ttl_seconds=900.0, clock=clock)
        registry.claim("w1", ["shared.py"])
        registry.claim("w2", ["shared.py"])
        conflicts = registry.overlaps("w2")
        assert "shared.py" in conflicts
        assert "w1" in conflicts["shared.py"]

    def test_reclaim_refreshes_timestamp(self):
        from general_ludd.coordination.file_claims import FileClaimRegistry

        clock = iter([0.0, 500.0, 800.0]).__next__
        registry = FileClaimRegistry(ttl_seconds=900.0, clock=clock)
        registry.claim("w1", ["a.py"])
        registry.claim("w1", ["a.py"])
        conflicts = registry.overlaps("w1")
        assert conflicts == {}

    def test_reap_stale_removes_all_expired(self):
        from general_ludd.coordination.file_claims import FileClaimRegistry

        ticks = [0.0, 1.0, 2000.0, 2001.0]
        clock = iter(ticks).__next__
        registry = FileClaimRegistry(ttl_seconds=5.0, clock=clock)
        registry.claim("w1", ["f1.py"])
        registry.claim("w2", ["f2.py"])
        reaped = registry.reap_stale(now=2000.0)
        assert set(reaped) == {"w1", "w2"}
        assert registry.all_claims() == {}

    def test_concurrent_claim_and_overlaps(self):
        from general_ludd.coordination.file_claims import FileClaimRegistry

        registry = FileClaimRegistry(ttl_seconds=60.0)
        errors: list[Exception] = []

        def claimer(wid: str):
            try:
                registry.claim(wid, [f"{wid}_file.py"])
            except Exception as exc:
                errors.append(exc)

        def overlap_checker():
            try:
                registry.overlaps("worker_0")
            except Exception as exc:
                errors.append(exc)

        threads: list[threading.Thread] = []
        for i in range(5):
            threads.append(threading.Thread(target=claimer, args=(f"worker_{i}",)))
        threads.append(threading.Thread(target=overlap_checker))
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Threading errors: {errors}"
        result = registry.all_claims()
        assert len(result) == 5

    def test_merge_plan_detects_contested_files(self):
        from general_ludd.coordination.file_claims import FileClaimRegistry

        registry = FileClaimRegistry(ttl_seconds=60.0)
        registry.claim("w1", ["a.py", "shared.py"])
        registry.claim("w2", ["b.py", "shared.py"])
        plan = registry.merge_plan()
        assert isinstance(plan, dict)
        assert "shared.py" in plan
        assert plan["shared.py"] == "union"


# ---------------------------------------------------------------------------
# LocalAgentMemory — TTL expiration
# ---------------------------------------------------------------------------


class TestLocalAgentMemoryTTL:
    @pytest.mark.asyncio
    async def test_set_and_get_without_ttl(self, tmp_cache_dir):
        from general_ludd.memory.local import LocalAgentMemory

        mem = LocalAgentMemory(cache_dir=tmp_cache_dir)
        await mem.set("agent1", "k1", "value1")
        record = await mem.get("agent1", "k1")
        mem.close()
        assert record is not None
        assert record.value == "value1"

    @pytest.mark.asyncio
    async def test_ttl_expired_returns_none(self, tmp_cache_dir):
        from general_ludd.memory.local import LocalAgentMemory

        mem = LocalAgentMemory(cache_dir=tmp_cache_dir)
        await mem.set("a1", "kk", "vv", ttl_seconds=0)
        record = await mem.get("a1", "kk")
        mem.close()
        assert record is None

    @pytest.mark.asyncio
    async def test_ttl_not_expired_returns_value(self, tmp_cache_dir):
        from general_ludd.memory.local import LocalAgentMemory

        mem = LocalAgentMemory(cache_dir=tmp_cache_dir)
        await mem.set("a1", "kk", "vv", ttl_seconds=3600)
        record = await mem.get("a1", "kk")
        mem.close()
        assert record is not None
        assert record.value == "vv"

    @pytest.mark.asyncio
    async def test_purge_expired_removes_entries(self, tmp_cache_dir):
        from general_ludd.memory.local import LocalAgentMemory

        mem = LocalAgentMemory(cache_dir=tmp_cache_dir)
        await mem.set("a1", "live", "keep", ttl_seconds=3600)
        await mem.set("a2", "dead", "gone", ttl_seconds=0)
        purged = await mem.purge_expired()
        mem.close()
        assert purged >= 1


# ---------------------------------------------------------------------------
# LocalAgentMemory — cache key generation
# ---------------------------------------------------------------------------


class TestLocalAgentMemoryKeyGen:
    def test_key_generation_and_close_do_not_open_cache(
        self, tmp_cache_dir: str
    ) -> None:
        from general_ludd.memory.local import LocalAgentMemory

        with patch("general_ludd.memory.local.open_safe_diskcache") as open_cache:
            mem = LocalAgentMemory(cache_dir=tmp_cache_dir)
            assert mem._data_key("agent", "key", "namespace")
            mem.close()

        open_cache.assert_not_called()

    def test_data_key_deterministic(self):
        from general_ludd.memory.local import LocalAgentMemory

        mem = LocalAgentMemory(cache_dir="/tmp/nonexistent_test")
        k1 = mem._data_key("agent1", "key1", "ns", "proj")
        k2 = mem._data_key("agent1", "key1", "ns", "proj")
        assert k1 == k2

    def test_data_key_different_agent_yields_different_key(self):
        from general_ludd.memory.local import LocalAgentMemory

        mem = LocalAgentMemory(cache_dir="/tmp/nonexistent_test")
        k1 = mem._data_key("a1", "k", "ns", "p")
        k2 = mem._data_key("a2", "k", "ns", "p")
        assert k1 != k2

    def test_data_key_different_namespace_yields_different_key(self):
        from general_ludd.memory.local import LocalAgentMemory

        mem = LocalAgentMemory(cache_dir="/tmp/nonexistent_test")
        k1 = mem._data_key("a", "k", "ns1", "p")
        k2 = mem._data_key("a", "k", "ns2", "p")
        assert k1 != k2

    def test_data_key_none_project_uses_global(self):
        from general_ludd.memory.local import LocalAgentMemory

        mem = LocalAgentMemory(cache_dir="/tmp/nonexistent_test")
        k = mem._data_key("a", "k", "ns", None)
        assert "__global__" in k


# ---------------------------------------------------------------------------
# functools.lru_cache — capacity eviction
# ---------------------------------------------------------------------------


class TestLRUCacheEviction:
    def test_lru_evicts_least_recently_used(self):
        call_count = 0

        @functools.lru_cache(maxsize=2)
        def fn(n: int) -> int:
            nonlocal call_count
            call_count += 1
            return n * 10

        assert fn(1) == 10
        assert fn(2) == 20
        assert call_count == 2
        assert fn(1) == 10
        assert call_count == 2  # cached
        assert fn(3) == 30
        assert call_count == 3  # evicted 2
        assert fn(2) == 20
        assert call_count == 4  # recomputed

    def test_lru_same_args_returns_cached(self):
        call_count = 0

        @functools.lru_cache(maxsize=8)
        def fn(x: str) -> str:
            nonlocal call_count
            call_count += 1
            return x.upper()

        fn("hello")
        fn("hello")
        assert call_count == 1

    def test_lru_cache_info_tracks_stats(self):
        @functools.lru_cache(maxsize=4)
        def fn(x: int) -> int:
            return x * 2

        fn(1)
        fn(1)
        fn(2)
        info = fn.cache_info()
        assert info.hits >= 1
        assert info.misses >= 2
        assert info.maxsize == 4

    def test_lru_cache_clear(self):
        call_count = 0

        @functools.lru_cache(maxsize=4)
        def fn(x: int) -> int:
            nonlocal call_count
            call_count += 1
            return x

        fn(1)
        fn(1)
        fn.cache_clear()
        fn(1)
        assert call_count == 2


# ---------------------------------------------------------------------------
# Security backlog — image cache cleanup
# ---------------------------------------------------------------------------


class TestImageCacheCleanup:
    @staticmethod
    def make_manifest(cache_dir: str, subdir: str, built_at: float):
        sub = Path(cache_dir) / subdir
        sub.mkdir(parents=True)
        (sub / "manifest.json").write_text(json.dumps({"built_at": built_at, "name": "test"}))

    def test_cleanup_removes_old_entries(self, tmp_cache_dir):
        from general_ludd.security.sandboxes.vm.image_builder import cleanup_cache

        now = time.time()
        self.make_manifest(tmp_cache_dir, "old_img", now - 200000)
        self.make_manifest(tmp_cache_dir, "new_img", now - 100)

        with patch(
            "general_ludd.security.sandboxes.vm.image_builder.CACHE_DIR",
            Path(tmp_cache_dir),
        ):
            removed = cleanup_cache(max_age_seconds=86400)
        assert removed >= 1

    def test_cleanup_zero_max_age_removes_all(self, tmp_cache_dir):
        from general_ludd.security.sandboxes.vm.image_builder import cleanup_cache

        now = time.time()
        self.make_manifest(tmp_cache_dir, "a", now)
        self.make_manifest(tmp_cache_dir, "b", now)

        with patch(
            "general_ludd.security.sandboxes.vm.image_builder.CACHE_DIR",
            Path(tmp_cache_dir),
        ):
            removed = cleanup_cache(max_age_seconds=0)
        assert removed == 2

    def test_cleanup_keeps_fresh_entries(self, tmp_cache_dir):
        from general_ludd.security.sandboxes.vm.image_builder import cleanup_cache

        now = time.time()
        self.make_manifest(tmp_cache_dir, "fresh", now)

        with patch(
            "general_ludd.security.sandboxes.vm.image_builder.CACHE_DIR",
            Path(tmp_cache_dir),
        ):
            removed = cleanup_cache(max_age_seconds=3600)
        assert removed == 0


# ---------------------------------------------------------------------------
# AdaptiveRouter — in-memory routing cache
# ---------------------------------------------------------------------------


class TestRoutingCache:
    def test_cache_valid_when_fresh(self):
        from general_ludd.scoring.router import AdaptiveRouter

        router = AdaptiveRouter.__new__(AdaptiveRouter)
        router._cache_time = None
        router._cache_ttl_seconds = 300.0
        assert router._cache_valid() is False

        from datetime import datetime

        router._cache_time = datetime.now()
        assert router._cache_valid() is True

    def test_cache_invalid_after_ttl(self):
        from datetime import datetime, timedelta

        from general_ludd.scoring.router import AdaptiveRouter

        router = AdaptiveRouter.__new__(AdaptiveRouter)
        router._cache_ttl_seconds = 0.01
        router._cache_time = datetime.now() - timedelta(seconds=10)
        assert router._cache_valid() is False

    def test_cache_key_varies_by_type_and_cost(self):
        from general_ludd.schemas.benchmark import TaskType
        from general_ludd.scoring.router import AdaptiveRouter

        router = AdaptiveRouter.__new__(AdaptiveRouter)
        k1 = router._cache_key(TaskType.BUG_FIX, max_cost_usd=1.0)
        k2 = router._cache_key(TaskType.BUG_FIX, max_cost_usd=None)
        assert k1 != k2
        k3 = router._cache_key(TaskType.CODE_REVIEW, max_cost_usd=1.0)
        assert k1 != k3
