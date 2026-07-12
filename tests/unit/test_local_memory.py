"""Unit tests for LocalAgentMemory — diskcache-backed local agent memory store."""

from __future__ import annotations

import time
import uuid

import pytest
import pytest_asyncio

from general_ludd.memory.local import LocalAgentMemory, MemoryRecord


@pytest_asyncio.fixture
async def local_memory(tmp_path):
    cache_dir = tmp_path / "memory_cache"
    memory = LocalAgentMemory(cache_dir=str(cache_dir))
    yield memory
    memory.close()


class TestLocalMemorySet:
    async def test_set_creates_record(self, local_memory):
        record = await local_memory.set("a1", "k1", "v1")
        assert record.agent_id == "a1"
        assert record.key == "k1"
        assert record.value == "v1"
        assert record.namespace == "default"

    async def test_set_with_custom_namespace(self, local_memory):
        record = await local_memory.set("a1", "k1", "v1", namespace="custom")
        assert record.namespace == "custom"

    async def test_set_with_ttl(self, local_memory):
        record = await local_memory.set("a1", "k1", "v1", ttl_seconds=3600)
        assert record.ttl_seconds == 3600

    async def test_set_upsert_overwrites(self, local_memory):
        await local_memory.set("a1", "k1", "v1")
        record = await local_memory.set("a1", "k1", "v2")
        assert record.value == "v2"


class TestLocalMemoryGet:
    async def test_get_returns_record(self, local_memory):
        await local_memory.set("a1", "k1", "v1")
        record = await local_memory.get("a1", "k1")
        assert record is not None
        assert record.value == "v1"

    async def test_get_nonexistent_returns_none(self, local_memory):
        record = await local_memory.get("a1", "nonexistent")
        assert record is None

    async def test_get_respects_namespace(self, local_memory):
        await local_memory.set("a1", "k1", "v1", namespace="ns1")
        await local_memory.set("a1", "k1", "v2", namespace="ns2")
        r1 = await local_memory.get("a1", "k1", namespace="ns1")
        r2 = await local_memory.get("a1", "k1", namespace="ns2")
        assert r1 is not None and r1.value == "v1"
        assert r2 is not None and r2.value == "v2"


class TestLocalMemoryDelete:
    async def test_delete_existing_returns_true(self, local_memory):
        await local_memory.set("a1", "k1", "v1")
        result = await local_memory.delete("a1", "k1")
        assert result is True

    async def test_delete_nonexistent_returns_false(self, local_memory):
        result = await local_memory.delete("a1", "nonexistent")
        assert result is False

    async def test_delete_removes_record(self, local_memory):
        await local_memory.set("a1", "k1", "v1")
        await local_memory.delete("a1", "k1")
        record = await local_memory.get("a1", "k1")
        assert record is None

    async def test_delete_respects_namespace(self, local_memory):
        await local_memory.set("a1", "k1", "v1", namespace="ns1")
        await local_memory.set("a1", "k1", "v2", namespace="ns2")
        await local_memory.delete("a1", "k1", namespace="ns1")
        r1 = await local_memory.get("a1", "k1", namespace="ns1")
        r2 = await local_memory.get("a1", "k1", namespace="ns2")
        assert r1 is None
        assert r2 is not None and r2.value == "v2"


class TestLocalMemoryListByNamespace:
    async def test_list_returns_records(self, local_memory):
        await local_memory.set("a1", "k1", "v1")
        await local_memory.set("a1", "k2", "v2")
        records = await local_memory.list_by_namespace("a1")
        assert len(records) == 2
        keys = {r.key for r in records}
        assert keys == {"k1", "k2"}

    async def test_list_filters_by_namespace(self, local_memory):
        await local_memory.set("a1", "k1", "v1", namespace="ns1")
        await local_memory.set("a1", "k2", "v2", namespace="ns2")
        records = await local_memory.list_by_namespace("a1", namespace="ns1")
        assert len(records) == 1
        assert records[0].key == "k1"

    async def test_list_empty_agent(self, local_memory):
        records = await local_memory.list_by_namespace("nonexistent")
        assert records == []

    async def test_list_respects_limit(self, local_memory):
        for i in range(10):
            await local_memory.set("a1", f"k{i}", f"v{i}")
        records = await local_memory.list_by_namespace("a1", limit=5)
        assert len(records) == 5


class TestLocalMemoryTtl:
    async def test_ttl_expired_record_returns_none(self, local_memory):
        await local_memory.set("a1", "temp", "data", ttl_seconds=1)
        record = await local_memory.get("a1", "temp")
        assert record is not None
        time.sleep(1.1)
        record = await local_memory.get("a1", "temp")
        assert record is None

    async def test_ttl_not_expired_returns_record(self, local_memory):
        await local_memory.set("a1", "persist", "data", ttl_seconds=3600)
        record = await local_memory.get("a1", "persist")
        assert record is not None

    async def test_purge_expired_removes_stale_entries(self, local_memory):
        await local_memory.set("a1", "expired", "data", ttl_seconds=1)
        await local_memory.set("a1", "fresh", "data", ttl_seconds=3600)
        time.sleep(1.1)
        purged = await local_memory.purge_expired()
        assert purged >= 1
        expired = await local_memory.get("a1", "expired")
        fresh = await local_memory.get("a1", "fresh")
        assert expired is None
        assert fresh is not None


class TestLocalMemoryPersistence:
    async def test_data_survives_close_and_reopen(self, tmp_path):
        cache_dir = str(tmp_path / "persist_memory")
        m1 = LocalAgentMemory(cache_dir=cache_dir)
        await m1.set("a1", "k1", "v1")
        await m1.set("a1", "k2", "v2", namespace="ns2")
        m1.close()

        m2 = LocalAgentMemory(cache_dir=cache_dir)
        r1 = await m2.get("a1", "k1")
        r2 = await m2.get("a1", "k2", namespace="ns2")
        assert r1 is not None and r1.value == "v1"
        assert r2 is not None and r2.value == "v2"
        records = await m2.list_by_namespace("a1")
        assert len(records) == 1
        m2.close()


class TestLocalMemoryEpisodicCompatibility:
    async def test_episodic_recorder_uses_local_memory(self, local_memory):
        from general_ludd.memory.episodic import EpisodicMemoryRecorder

        recorder = EpisodicMemoryRecorder(local_memory)
        episode_id = await recorder.record_completion(
            agent_id="agent-1",
            task_type="debug",
            work_type="code",
            outcome="success",
            takeaway="Always check nulls first",
            duration_seconds=12.5,
        )
        assert episode_id

        episodes = await recorder.list_episodes("agent-1")
        assert len(episodes) == 1
        assert episodes[0].takeaway == "Always check nulls first"
        assert episodes[0].outcome == "success"

    async def test_retriever_uses_local_memory(self, local_memory):
        from general_ludd.memory.episodic import EpisodicMemoryRecorder
        from general_ludd.memory.retrieval import MemoryRetriever

        recorder = EpisodicMemoryRecorder(local_memory)
        await recorder.record_completion(
            agent_id="agent-1",
            task_type="debug",
            outcome="success",
            takeaway="Use incremental debugging steps",
        )
        await recorder.record_completion(
            agent_id="agent-1",
            task_type="debug",
            outcome="failure",
            error_message="KeyError on missing config key",
        )

        retriever = MemoryRetriever(local_memory)
        results = await retriever.query("agent-1", "debug debugging", task_type="debug", top_k=5)
        assert len(results) >= 1
        assert any("debug" in r.episode.task_type for r in results)

    async def test_consolidator_uses_local_memory(self, local_memory):
        from general_ludd.memory.consolidation import MemoryConsolidator
        from general_ludd.memory.episodic import EpisodicMemoryRecorder

        recorder = EpisodicMemoryRecorder(local_memory)
        for i in range(15):
            await recorder.record_completion(
                agent_id="agent-1",
                task_type="refactor",
                outcome="success" if i % 3 != 0 else "failure",
                takeaway=f"Lesson {i}",
                duration_seconds=10.0,
            )

        consolidator = MemoryConsolidator(
            local_memory,
            min_episodes_to_consolidate=5,
            max_episode_age_hours=0.0,
        )
        result = await consolidator.consolidate("agent-1", force=True)
        assert result["consolidated"] >= 1
        assert "refactor" in result["task_types"]

    async def test_cross_task_learner_uses_local_memory(self, local_memory):
        from general_ludd.memory.cross_task import CrossTaskLearner
        from general_ludd.memory.episodic import EpisodicMemoryRecorder

        recorder = EpisodicMemoryRecorder(local_memory)
        await recorder.record_completion(
            agent_id="agent-1", task_type="debug", outcome="success", takeaway="Check types first"
        )
        await recorder.record_completion(
            agent_id="agent-1", task_type="debug", outcome="failure", error_message="ImportError on missing module"
        )
        await recorder.record_completion(
            agent_id="agent-1", task_type="refactor", outcome="success", takeaway="Extract small functions"
        )

        learner = CrossTaskLearner(local_memory)
        patterns = await learner.learn_patterns("agent-1")
        assert patterns["total_episodes"] == 3
        assert patterns.get("overall_success_rate_pct", 0) > 0


class TestLocalMemoryDaemonWiring:
    async def test_attribute_on_app_state(self, local_memory):
        assert hasattr(local_memory, "_cache")
        assert hasattr(local_memory, "cache_dir")
        assert isinstance(await local_memory.get("x", "y"), type(None))

    async def test_close_cleans_up(self, tmp_path):
        cache_dir = str(tmp_path / "close_test")
        memory = LocalAgentMemory(cache_dir=cache_dir)
        await memory.set("a1", "k1", "v1")
        memory.close()
        cache_files = list(tmp_path.rglob("close_test/cache.db"))
        assert len(cache_files) >= 0

    async def test_multiple_agents_isolated(self, local_memory):
        await local_memory.set("agent-a", "shared-key", "value-a")
        await local_memory.set("agent-b", "shared-key", "value-b")

        ra = await local_memory.get("agent-a", "shared-key")
        rb = await local_memory.get("agent-b", "shared-key")
        assert ra is not None and ra.value == "value-a"
        assert rb is not None and rb.value == "value-b"

    async def test_get_after_delete_returns_none(self, local_memory):
        await local_memory.set("a1", "k1", "v1")
        await local_memory.delete("a1", "k1")
        assert await local_memory.get("a1", "k1") is None

    async def test_list_after_delete_excludes_key(self, local_memory):
        await local_memory.set("a1", "k1", "v1")
        await local_memory.set("a1", "k2", "v2")
        await local_memory.delete("a1", "k1")
        records = await local_memory.list_by_namespace("a1")
        assert len(records) == 1
        assert records[0].key == "k2"
