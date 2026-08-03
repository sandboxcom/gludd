"""Deep persistence and index tests for the memory subsystem.

Covers:
  - LocalAgentMemory: write/read/close/reopen, index rebuild, eviction, LRU, cross-session
  - CrossConversationStore: write/read, TTL eviction, search, project isolation, cross-session
  - MemoryEmbeddingStore: add/search/delete, reindex, hybrid search, keyword search, clear
  - MemoryBank: serialization roundtrip for persistence, bank registry lifecycle
  - MemoryRecord: persistence helpers (to_dict/from_dict), timestamp fidelity
"""

from __future__ import annotations

import asyncio
import copy
import json
import time
from pathlib import Path

import pytest

from general_ludd.memory.cross_conversation import CrossConversationStore
from general_ludd.memory.embedding_store import MemoryEmbeddingStore
from general_ludd.memory.local import (
    LocalAgentMemory,
    MemoryRecord,
)
from general_ludd.memory.memory_bank import (
    Disposition,
    MemoryBank,
    MemoryBankConfig,
    MemoryBankRegistry,
    MemoryEntry,
    MentalModel,
)

# ────────────────────────────────────────────────────────────────── test helpers


def _unique_dir(tmp_path: Path, name: str) -> str:
    d = tmp_path / name
    d.mkdir(exist_ok=True)
    return str(d)


def _now_f() -> float:
    return time.time()


# ==============================================================================
#  LocalAgentMemory — write-back persistence
# ==============================================================================


class TestLocalMemoryPersistence:
    """Write → close → reopen → read: data must survive instance teardown."""

    @pytest.mark.asyncio
    async def test_single_record_survives_reopen(self, tmp_path: Path) -> None:
        cache_dir = _unique_dir(tmp_path, "persist_single")

        store1 = LocalAgentMemory(cache_dir)
        await store1.set("agent-1", "greeting", "hello world", namespace="chat", ttl_seconds=3600)
        store1.close()

        store2 = LocalAgentMemory(cache_dir)
        record = await store2.get("agent-1", "greeting", namespace="chat")
        store2.close()

        assert record is not None
        assert record.value == "hello world"
        assert record.namespace == "chat"
        assert record.agent_id == "agent-1"

    @pytest.mark.asyncio
    async def test_multiple_records_survive_reopen(self, tmp_path: Path) -> None:
        cache_dir = _unique_dir(tmp_path, "persist_multi")

        store1 = LocalAgentMemory(cache_dir)
        for i in range(1, 6):
            await store1.set("worker", f"task_{i}", f"result_{i}", namespace="jobs")
        store1.close()

        store2 = LocalAgentMemory(cache_dir)
        results = await store2.list_by_namespace("worker", "jobs")
        store2.close()

        assert len(results) == 5
        keys = {r.key for r in results}
        assert keys == {"task_1", "task_2", "task_3", "task_4", "task_5"}

    @pytest.mark.asyncio
    async def test_namespaces_isolated_after_reopen(self, tmp_path: Path) -> None:
        cache_dir = _unique_dir(tmp_path, "persist_ns")

        store1 = LocalAgentMemory(cache_dir)
        await store1.set("a", "k", "ns_a", namespace="alpha")
        await store1.set("a", "k", "ns_b", namespace="beta")
        store1.close()

        store2 = LocalAgentMemory(cache_dir)
        ra = await store2.get("a", "k", namespace="alpha")
        rb = await store2.get("a", "k", namespace="beta")
        store2.close()

        assert ra is not None and ra.value == "ns_a"
        assert rb is not None and rb.value == "ns_b"

    @pytest.mark.asyncio
    async def test_project_id_scoping_survives_reopen(self, tmp_path: Path) -> None:
        cache_dir = _unique_dir(tmp_path, "persist_pid")

        store1 = LocalAgentMemory(cache_dir)
        await store1.set("a", "k", "proj_a_val", project_id="proj-a")
        await store1.set("a", "k", "global_val")
        await store1.set("a", "k", "proj_b_val", project_id="proj-b")
        store1.close()

        store2 = LocalAgentMemory(cache_dir)
        a_val = await store2.get("a", "k", project_id="proj-a")
        g_val = await store2.get("a", "k")
        b_val = await store2.get("a", "k", project_id="proj-b")
        store2.close()

        assert a_val is not None and a_val.value == "proj_a_val"
        assert g_val is not None and g_val.value == "global_val"
        assert b_val is not None and b_val.value == "proj_b_val"


# ==============================================================================
#  LocalAgentMemory — eviction
# ==============================================================================


class TestLocalMemoryEviction:
    @pytest.mark.asyncio
    async def test_expired_record_removed_on_get(self, tmp_path: Path) -> None:
        cache_dir = _unique_dir(tmp_path, "evict_get")
        store = LocalAgentMemory(cache_dir)
        await store.set("a", "stale", "old", ttl_seconds=0)
        await asyncio.sleep(0.02)

        result = await store.get("a", "stale")
        assert result is None
        store.close()

    @pytest.mark.asyncio
    async def test_expired_record_removed_on_purge(self, tmp_path: Path) -> None:
        cache_dir = _unique_dir(tmp_path, "evict_purge")
        store = LocalAgentMemory(cache_dir)
        await store.set("a", "fresh", "keep", ttl_seconds=600)
        await store.set("a", "stale", "old", ttl_seconds=0)
        await asyncio.sleep(0.02)

        purged = await store.purge_expired()
        assert purged >= 1
        assert await store.get("a", "fresh") is not None
        assert await store.get("a", "stale") is None
        store.close()

    @pytest.mark.asyncio
    async def test_no_ttl_record_never_expires(self, tmp_path: Path) -> None:
        cache_dir = _unique_dir(tmp_path, "evict_nottl")
        store = LocalAgentMemory(cache_dir)
        await store.set("a", "eternal", "forever")
        purged = await store.purge_expired()
        assert purged == 0
        record = await store.get("a", "eternal")
        assert record is not None
        store.close()

    @pytest.mark.asyncio
    async def test_expired_and_survives_reopen(self, tmp_path: Path) -> None:
        cache_dir = _unique_dir(tmp_path, "evict_reopen")

        store1 = LocalAgentMemory(cache_dir)
        await store1.set("a", "fleeting", "gone", ttl_seconds=0)
        await store1.set("a", "durable", "stays", ttl_seconds=3600)
        await asyncio.sleep(0.02)
        await store1.purge_expired()
        store1.close()

        store2 = LocalAgentMemory(cache_dir)
        assert await store2.get("a", "fleeting") is None
        durable = await store2.get("a", "durable")
        assert durable is not None and durable.value == "stays"
        store2.close()


# ==============================================================================
#  LocalAgentMemory — index rebuild / recovery
# ==============================================================================


class TestLocalMemoryIndexRebuild:
    @pytest.mark.asyncio
    async def test_index_present_after_reopen(self, tmp_path: Path) -> None:
        """list_by_namespace must work correctly after reopen — verifies index persistence."""
        cache_dir = _unique_dir(tmp_path, "idx_reopen")

        store1 = LocalAgentMemory(cache_dir)
        await store1.set("bot", "cmd_a", "resp_a", namespace="commands")
        await store1.set("bot", "cmd_b", "resp_b", namespace="commands")
        store1.close()

        store2 = LocalAgentMemory(cache_dir)
        cmds = await store2.list_by_namespace("bot", "commands")
        store2.close()

        assert len(cmds) == 2
        assert {r.key for r in cmds} == {"cmd_a", "cmd_b"}

    @pytest.mark.asyncio
    async def test_overwritten_key_index_stays_consistent(self, tmp_path: Path) -> None:
        cache_dir = _unique_dir(tmp_path, "idx_overwrite")

        store = LocalAgentMemory(cache_dir)
        await store.set("x", "key_a", "first_val", namespace="ns")
        await store.set("x", "key_b", "second_val", namespace="ns")

        await store.set("x", "key_a", "updated_val", namespace="ns")

        results = await store.list_by_namespace("x", "ns")
        assert len(results) == 2
        record_a = await store.get("x", "key_a", namespace="ns")
        assert record_a is not None and record_a.value == "updated_val"
        store.close()

    @pytest.mark.asyncio
    async def test_index_handles_empty_namespace_gracefully(self, tmp_path: Path) -> None:
        cache_dir = _unique_dir(tmp_path, "idx_empty")
        store = LocalAgentMemory(cache_dir)
        results = await store.list_by_namespace("nobody", "ghost")
        assert results == []
        store.close()


# ==============================================================================
#  LocalAgentMemory — LRU / diskcache behaviour
# ==============================================================================


class TestLocalMemoryDisklru:
    @pytest.mark.asyncio
    async def test_disklru_size_limit_evicts_oldest(self, tmp_path: Path) -> None:
        """diskcache evicts by access order when size_limit set."""
        cache_dir = _unique_dir(tmp_path, "lru_limit")

        store = LocalAgentMemory(cache_dir)

        for i in range(1, 11):
            await store.set("agent", f"k{i}", f"v{i}")

        await store.list_by_namespace("agent")

        store._cache.cull()
        results = await store.list_by_namespace("agent")
        assert len(results) > 0

        store.close()

    @pytest.mark.asyncio
    async def test_update_refreshes_timestamp(self, tmp_path: Path) -> None:
        cache_dir = _unique_dir(tmp_path, "lru_update")
        store = LocalAgentMemory(cache_dir)
        r1 = await store.set("a", "key", "v1")
        ts1 = r1.updated_at
        await asyncio.sleep(0.01)
        r2 = await store.set("a", "key", "v2")
        assert r2.updated_at > ts1
        store.close()


# ==============================================================================
#  CrossConversationStore — persistence
# ==============================================================================


class TestCrossConversationPersistence:
    def test_write_and_read_basic(self) -> None:
        store = CrossConversationStore(store=None)
        store.put(
            "session_meta",
            {"model": "sonnet", "temperature": 0.7},
            namespace=("conversations",),
        )
        result = store.get("session_meta", namespace=("conversations",))
        assert result is not None
        assert result["value"]["model"] == "sonnet"

    def test_write_and_read_with_project_id(self) -> None:
        store = CrossConversationStore(store=None)
        store.put(
            "config",
            {"theme": "dark"},
            project_id="proj-42",
        )
        scoped = store.get("config", project_id="proj-42")
        assert scoped is not None and scoped["value"]["theme"] == "dark"

        other = store.get("config", project_id="proj-other")
        assert other is None

    def test_ttl_expiration(self) -> None:
        store = CrossConversationStore(store=None)
        store.put("ephemeral", {"x": 1}, ttl=0.001)
        time.sleep(0.01)
        result = store.get("ephemeral")
        assert result is None

    def test_ttl_eviction_on_purge(self) -> None:
        store = CrossConversationStore(store=None)
        store.put("keep", {"a": 1}, ttl=3600)
        store.put("toss", {"b": 2}, ttl=0.001)
        time.sleep(0.01)
        purged = store.purge_expired()
        assert purged >= 1
        assert store.get("keep") is not None
        assert store.get("toss") is None

    def test_search_by_namespace_prefix(self) -> None:
        store = CrossConversationStore(store=None)
        store.put("a", {"val": 1}, namespace=("mem", "alpha"))
        store.put("b", {"val": 2}, namespace=("mem", "beta"))
        store.put("c", {"val": 3}, namespace=("other",))
        results = store.search(namespace_prefix=("mem",))
        keys = {r["key"] for r in results}
        assert keys == {"a", "b"}

    def test_search_with_filter(self) -> None:
        store = CrossConversationStore(store=None)
        store.put("e1", {"kind": "event", "priority": 1})
        store.put("e2", {"kind": "metric", "priority": 2})
        matches = store.search(filter={"kind": "event"})
        assert len(matches) == 1
        assert matches[0]["key"] == "e1"

    def test_delete_by_scoped_key(self) -> None:
        store = CrossConversationStore(store=None)
        store.put("d", {"x": 1}, project_id="p1")
        store.put("d", {"x": 2})
        assert store.delete("d", project_id="p1") is True
        assert store.get("d", project_id="p1") is None
        assert store.get("d") is not None

    def test_persistence_across_instances(self) -> None:
        store1 = CrossConversationStore(store=None)
        store1.put("bridge", {"token": "abc"}, namespace=("shared",))
        data = copy.deepcopy(store1._ephemeral)
        ttl_data = dict(store1._ttl_registry)

        store2 = CrossConversationStore(store=None)
        store2._ephemeral = data
        store2._ttl_registry = ttl_data

        result = store2.get("bridge", namespace=("shared",))
        assert result is not None
        assert result["value"]["token"] == "abc"


# ==============================================================================
#  MemoryEmbeddingStore — index lifecycle
# ==============================================================================


class TestEmbeddingStoreIndexLifecycle:
    @pytest.mark.asyncio
    async def test_add_and_search_basic(self) -> None:
        store = MemoryEmbeddingStore(memory_repo=None)
        await store.add("r1", "agent-1", "race condition in task scheduler")
        await store.add("r2", "agent-1", "diskcache eviction policy too aggressive")
        await store.add("r3", "agent-1", "SQL query optimization for large tables")

        results = await store.search("concurrency bug", top_k=2, min_score=0.0)
        assert len(results) > 0

    @pytest.mark.asyncio
    async def test_search_by_agent_filter(self) -> None:
        store = MemoryEmbeddingStore(memory_repo=None)
        await store.add("r1", "bot-a", "python type checking pipeline")
        await store.add("r2", "bot-b", "javascript bundle splitting")
        results = await store.search("python", agent_id="bot-a")
        assert len(results) > 0
        assert all(r["agent_id"] == "bot-a" for r in results)

    @pytest.mark.asyncio
    async def test_search_by_namespace_filter(self) -> None:
        store = MemoryEmbeddingStore(memory_repo=None)
        await store.add("r1", "agent", "fix lint error", namespace="episodic")
        await store.add("r2", "agent", "learned pattern", namespace="semantic")
        results = await store.search("lint", namespace="episodic")
        assert all(r["namespace"] == "episodic" for r in results)

    @pytest.mark.asyncio
    async def test_delete_removes_from_index(self) -> None:
        store = MemoryEmbeddingStore(memory_repo=None)
        await store.add("r1", "agent", "delete me soon")
        await store.add("r2", "agent", "keep me forever")
        assert store.count == 2

        store.delete("r1")
        assert store.count == 1
        results = await store.search("delete", top_k=5)
        record_ids = {r["record_id"] for r in results}
        assert "r1" not in record_ids

    @pytest.mark.asyncio
    async def test_add_empty_text_skipped(self) -> None:
        store = MemoryEmbeddingStore(memory_repo=None)
        await store.add("r_blank", "agent", "   ")
        assert store.count == 0

    @pytest.mark.asyncio
    async def test_clear_resets_index(self) -> None:
        store = MemoryEmbeddingStore(memory_repo=None)
        await store.add("r1", "agent", "task completed")
        await store.add("r2", "agent", "another task")
        assert store.count == 2

        store.clear()
        assert store.count == 0
        results = await store.search("task")
        assert results == []

    @pytest.mark.asyncio
    async def test_keyword_search(self) -> None:
        store = MemoryEmbeddingStore(memory_repo=None)
        await store.add("r1", "agent", "docker container build optimization")
        await store.add("r2", "agent", "kubernetes pod scheduling fix")
        results = await store.keyword_search("docker container", top_k=3)
        assert len(results) > 0

    @pytest.mark.asyncio
    async def test_hybrid_search_combines_vector_and_keyword(self) -> None:
        store = MemoryEmbeddingStore(memory_repo=None)
        await store.add("r1", "agent", "redis cache invalidation bug")
        await store.add("r2", "agent", "nginx rate limiting configuration")
        await store.add("r3", "agent", "redis cluster failover strategy")
        results = await store.hybrid_search(
            "redis cache",
            keywords=["redis"],
            top_k=5,
            vector_weight=0.5,
            keyword_weight=0.5,
        )
        assert len(results) > 0

    @pytest.mark.asyncio
    async def test_empty_store_search_returns_empty(self) -> None:
        store = MemoryEmbeddingStore(memory_repo=None)
        assert await store.search("anything") == []

    @pytest.mark.asyncio
    async def test_reindex_from_repo_structure(self) -> None:
        class FakeRepo:
            async def list_by_namespace(self, agent_id, namespace="episodic", project_id=None, limit=2000):
                class FakeRow:
                    def __init__(self, id, agent_id, key, value, project_id=None, ttl_seconds=None):
                        self.id = id
                        self.agent_id = agent_id
                        self.key = key
                        self.value = value
                        self.project_id = project_id
                        self.ttl_seconds = ttl_seconds

                return [
                    FakeRow("mem-1", "agent", "k1", "race condition in task scheduler"),
                    FakeRow("mem-2", "agent", "k2", "fixed memory leak in daemon"),
                    FakeRow("mem-3", "agent", "k3", ""),
                ]

        store = MemoryEmbeddingStore(memory_repo=FakeRepo())
        summary = await store.reindex_from_repo("agent", namespace="episodic")
        assert summary["indexed"] == 2
        assert summary["skipped"] == 1
        assert summary["total_in_index"] == 2

    @pytest.mark.asyncio
    async def test_reindex_from_repo_json_structured_fields(self) -> None:
        class FakeRepo:
            async def list_by_namespace(self, agent_id, namespace="episodic", project_id=None, limit=2000):
                class FakeRow:
                    def __init__(self, id, agent_id, key, value, project_id=None, ttl_seconds=None):
                        self.id = id
                        self.agent_id = agent_id
                        self.key = key
                        self.value = value
                        self.project_id = project_id
                        self.ttl_seconds = ttl_seconds

                return [
                    FakeRow(
                        "mem-a",
                        "agent",
                        "ka",
                        json.dumps(
                            {
                                "takeaway": "concurrency bug fixed",
                                "task_type": "debug",
                                "outcome": "success",
                            }
                        ),
                    ),
                ]

        store = MemoryEmbeddingStore(memory_repo=FakeRepo())
        summary = await store.reindex_from_repo("agent", namespace="episodic")
        assert summary["indexed"] == 1
        results = await store.search("concurrency debug success")
        assert len(results) == 1


# ==============================================================================
#  MemoryBank — persistence helpers (serialization roundtrip)
# ==============================================================================


class TestMemoryBankPersistence:
    def test_bank_config_to_from_dict_full(self) -> None:
        original = MemoryBankConfig(
            bank_id="persist-test",
            mission="verify data integrity",
            directives=["rule-1", "rule-2"],
            disposition=Disposition(skepticism=5, literalism=1, empathy=4),
        )
        restored = MemoryBankConfig.from_dict(original.to_dict())
        assert restored.bank_id == "persist-test"
        assert restored.mission == "verify data integrity"
        assert restored.directives == ["rule-1", "rule-2"]
        assert restored.disposition.skepticism == 5

    def test_mental_model_to_from_dict_full(self) -> None:
        ts = time.time()
        original = MentalModel(
            model_id="mm-persist",
            subject="code quality",
            content="Always lint before commit",
            priority=9,
            created_by="agent-7",
            tags=["lint", "quality"],
            created_at=ts,
            updated_at=ts + 1,
        )
        restored = MentalModel.from_dict(original.to_dict())
        assert restored.model_id == "mm-persist"
        assert restored.subject == "code quality"
        assert restored.priority == 9
        assert restored.created_by == "agent-7"
        assert restored.tags == ["lint", "quality"]
        assert restored.created_at == ts

    def test_memory_entry_to_from_dict_full(self) -> None:
        ts = time.time()
        original = MemoryEntry(
            entry_id="e-x",
            content="The makefile has 300+ targets",
            source="Makefile:1",
            tags=["codebase", "size"],
            created_at=ts,
        )
        restored = MemoryEntry.from_dict(original.to_dict())
        assert restored.entry_id == "e-x"
        assert restored.content == "The makefile has 300+ targets"
        assert restored.source == "Makefile:1"
        assert restored.tags == ["codebase", "size"]
        assert restored.created_at == ts

    def test_registry_serialization_roundtrip(self) -> None:
        registry = MemoryBankRegistry()
        bank = registry.create_bank(
            MemoryBankConfig(
                bank_id="serde",
                mission="roundtrip test",
                disposition=Disposition(skepticism=4, literalism=3, empathy=2),
            )
        )
        bank.add_mental_model(MentalModel(subject="safety", content="Use parameterized queries", priority=8))
        bank.retain(MemoryEntry(content="PostgreSQL 14 is used"))

        configs = registry.list_banks()
        assert len(configs) == 1
        cfg = configs[0]
        assert cfg.bank_id == "serde"
        assert cfg.mission == "roundtrip test"

    def test_empty_bank_serializes_without_error(self) -> None:
        config = MemoryBankConfig(bank_id="empty")
        d = config.to_dict()
        restored = MemoryBankConfig.from_dict(d)
        assert restored.bank_id == "empty"
        assert restored.disposition.skepticism == 3

    def test_disposition_defaults_in_serialized_form(self) -> None:
        d = Disposition().to_dict()
        assert d == {"skepticism": 3, "literalism": 3, "empathy": 3}

    def test_bank_collision_detection_in_registry(self) -> None:
        registry = MemoryBankRegistry()
        registry.create_bank(MemoryBankConfig(bank_id="unique"))
        with pytest.raises(ValueError, match="already exists"):
            registry.create_bank(MemoryBankConfig(bank_id="unique"))

    def test_retain_deduplicates_identical_content(self) -> None:
        bank = MemoryBank(MemoryBankConfig(bank_id="dedup"))
        e1 = bank.retain(MemoryEntry(content="same content", entry_id="abc"))
        e2 = bank.retain(MemoryEntry(content="same content", entry_id="xyz"))
        assert len(bank.get_facts()) == 1
        assert e1.entry_id == e2.entry_id


# ==============================================================================
#  MemoryRecord — persistence helpers
# ==============================================================================


class TestMemoryRecordPersistence:
    def test_as_dict_contains_all_keys(self) -> None:
        ts = _now_f()
        record = MemoryRecord(
            agent_id="agent-9",
            key="state",
            value='{"mode":"active"}',
            namespace="sessions",
            project_id="proj-1",
            ttl_seconds=7200,
            created_at=ts,
            updated_at=ts + 5,
        )
        d = record.as_dict()
        expected_keys = {
            "agent_id",
            "key",
            "value",
            "namespace",
            "project_id",
            "ttl_seconds",
            "created_at",
            "updated_at",
        }
        assert set(d.keys()) == expected_keys
        assert d["namespace"] == "sessions"
        assert d["project_id"] == "proj-1"
        assert d["ttl_seconds"] == 7200

    def test_from_dict_accepts_string_timestamps(self) -> None:
        data: dict[str, object] = {
            "agent_id": "a",
            "key": "k",
            "value": "v",
            "created_at": "1712345678.9",
            "updated_at": 1712345679,
        }
        record = MemoryRecord.from_dict(data)
        assert record.created_at == 1712345678.9
        assert record.updated_at == 1712345679.0

    def test_as_dict_from_dict_roundtrip_preserves_data(self) -> None:
        ts = _now_f()
        original = MemoryRecord(
            agent_id="round",
            key="trip",
            value='{"a":1}',
            namespace="test",
            project_id="p99",
            ttl_seconds=30,
            created_at=ts,
            updated_at=ts,
        )
        restored = MemoryRecord.from_dict(original.as_dict())
        assert restored.agent_id == "round"
        assert restored.key == "trip"
        assert restored.value == '{"a":1}'
        assert restored.namespace == "test"
        assert restored.project_id == "p99"
        assert restored.ttl_seconds == 30
        assert restored.created_at == ts
