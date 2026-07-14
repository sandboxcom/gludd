"""Unit tests for memory/local.py — local agent memory with diskcache."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from general_ludd.memory.local import (
    DEFAULT_CACHE_DIR,
    LocalAgentMemory,
    MemoryRecord,
)


class TestMemoryRecord:
    def test_construction_with_defaults(self) -> None:
        record = MemoryRecord(agent_id="agent1", key="key1", value="val1")
        assert record.agent_id == "agent1"
        assert record.key == "key1"
        assert record.value == "val1"
        assert record.namespace == "default"
        assert record.project_id is None
        assert record.ttl_seconds is None

    def test_construction_with_all_fields(self) -> None:
        now = time.time()
        record = MemoryRecord(
            agent_id="a1",
            key="k1",
            value="v1",
            namespace="ns",
            project_id="proj1",
            ttl_seconds=3600,
            created_at=now,
            updated_at=now + 1,
        )
        assert record.agent_id == "a1"
        assert record.namespace == "ns"
        assert record.project_id == "proj1"
        assert record.ttl_seconds == 3600
        assert record.created_at == now
        assert record.updated_at == now + 1

    def test_as_dict_roundtrip(self) -> None:
        now = time.time()
        record = MemoryRecord(
            agent_id="a1", key="k1", value="v1",
            namespace="ns", project_id="proj1",
            ttl_seconds=3600, created_at=now, updated_at=now,
        )
        data = record.as_dict()
        assert data["agent_id"] == "a1"
        assert data["key"] == "k1"
        assert data["value"] == "v1"
        assert data["namespace"] == "ns"
        assert data["project_id"] == "proj1"
        assert data["ttl_seconds"] == 3600

    def test_from_dict_with_all_fields(self) -> None:
        now = time.time()
        data: dict[str, object] = {
            "agent_id": "a1",
            "key": "k1",
            "value": "v1",
            "namespace": "ns",
            "project_id": "proj1",
            "ttl_seconds": 3600,
            "created_at": now,
            "updated_at": now,
        }
        record = MemoryRecord.from_dict(data)
        assert record.agent_id == "a1"
        assert record.key == "k1"
        assert record.namespace == "ns"
        assert record.project_id == "proj1"
        assert record.ttl_seconds == 3600

    def test_from_dict_with_minimal_data(self) -> None:
        data: dict[str, object] = {"agent_id": "a1", "key": "k1", "value": "v1"}
        record = MemoryRecord.from_dict(data)
        assert record.agent_id == "a1"
        assert record.key == "k1"
        assert record.value == "v1"
        assert record.namespace == "default"
        assert record.project_id is None

    def test_from_dict_project_id_string(self) -> None:
        data: dict[str, object] = {
            "agent_id": "a1", "key": "k1", "value": "v1", "project_id": "p1",
        }
        record = MemoryRecord.from_dict(data)
        assert record.project_id == "p1"

    def test_from_dict_project_id_non_string(self) -> None:
        data: dict[str, object] = {
            "agent_id": "a1", "key": "k1", "value": "v1", "project_id": 42,
        }
        record = MemoryRecord.from_dict(data)
        assert record.project_id is None

    def test_from_dict_ttl_seconds_numeric(self) -> None:
        data: dict[str, object] = {
            "agent_id": "a1", "key": "k1", "value": "v1", "ttl_seconds": 60,
        }
        record = MemoryRecord.from_dict(data)
        assert record.ttl_seconds == 60

    def test_from_dict_ttl_seconds_non_numeric(self) -> None:
        data: dict[str, object] = {
            "agent_id": "a1", "key": "k1", "value": "v1", "ttl_seconds": "abc",
        }
        record = MemoryRecord.from_dict(data)
        assert record.ttl_seconds is None

    def test_from_dict_created_at_as_string(self) -> None:
        data: dict[str, object] = {
            "agent_id": "a1", "key": "k1", "value": "v1", "created_at": "1000.5",
        }
        record = MemoryRecord.from_dict(data)
        assert record.created_at == 1000.5

    def test_from_dict_updated_at_as_int(self) -> None:
        data: dict[str, object] = {
            "agent_id": "a1", "key": "k1", "value": "v1", "updated_at": 2000,
        }
        record = MemoryRecord.from_dict(data)
        assert record.updated_at == 2000.0


class TestLocalAgentMemory:
    @pytest.fixture
    def store(self, tmp_path: Path) -> LocalAgentMemory:
        cache_dir = tmp_path / "test_cache"
        mem = LocalAgentMemory(str(cache_dir))
        yield mem
        mem.close()

    def test_default_cache_dir(self) -> None:
        assert DEFAULT_CACHE_DIR == ".gludd/local_memory"

    def test_construction_creates_directory(self, tmp_path: Path) -> None:
        cache_dir = tmp_path / "new_cache"
        assert not cache_dir.exists()
        mem = LocalAgentMemory(str(cache_dir))
        assert cache_dir.exists()
        mem.close()

    @pytest.mark.asyncio
    async def test_set_and_get(self, store: LocalAgentMemory) -> None:
        record = await store.set("agent1", "foo", "bar", ttl_seconds=600)
        assert record.key == "foo"
        assert record.value == "bar"

        fetched = await store.get("agent1", "foo")
        assert fetched is not None
        assert fetched.value == "bar"

    @pytest.mark.asyncio
    async def test_get_nonexistent_key(self, store: LocalAgentMemory) -> None:
        result = await store.get("agent1", "missing")
        assert result is None

    @pytest.mark.asyncio
    async def test_delete_existing(self, store: LocalAgentMemory) -> None:
        await store.set("agent1", "delkey", "value")
        deleted = await store.delete("agent1", "delkey")
        assert deleted is True

    @pytest.mark.asyncio
    async def test_delete_nonexistent(self, store: LocalAgentMemory) -> None:
        deleted = await store.delete("agent1", "no_exist")
        assert deleted is False

    @pytest.mark.asyncio
    async def test_list_by_namespace(self, store: LocalAgentMemory) -> None:
        await store.set("agent1", "key1", "v1", "ns1")
        await store.set("agent1", "key2", "v2", "ns1")
        await store.set("agent1", "key3", "v3", "ns2")
        results = await store.list_by_namespace("agent1", "ns1")
        assert len(results) == 2
        keys = {r.key for r in results}
        assert keys == {"key1", "key2"}

    @pytest.mark.asyncio
    async def test_list_by_namespace_limit(self, store: LocalAgentMemory) -> None:
        await store.set("agent1", "a", "1", "ns")
        await store.set("agent1", "b", "2", "ns")
        await store.set("agent1", "c", "3", "ns")
        results = await store.list_by_namespace("agent1", "ns", limit=2)
        assert len(results) == 2

    @pytest.mark.asyncio
    async def test_purge_expired(self, store: LocalAgentMemory) -> None:
        await store.set("agent1", "fresh", "val", ttl_seconds=600)
        await store.set("agent1", "stale", "old", ttl_seconds=0)
        # Small sleep to ensure the TTL has definitely expired
        import asyncio
        await asyncio.sleep(0.01)
        purged = await store.purge_expired()
        assert purged >= 1
        assert await store.get("agent1", "stale") is None
        assert await store.get("agent1", "fresh") is not None

    @pytest.mark.asyncio
    async def test_get_expired_returns_none(self, store: LocalAgentMemory) -> None:
        await store.set("agent1", "expiring", "val", ttl_seconds=0)
        import asyncio
        await asyncio.sleep(0.01)
        result = await store.get("agent1", "expiring")
        assert result is None

    @pytest.mark.asyncio
    async def test_set_with_project_id(self, store: LocalAgentMemory) -> None:
        record = await store.set("agent1", "k1", "v1", project_id="proj-a")
        assert record.project_id == "proj-a"

        fetched = await store.get("agent1", "k1", project_id="proj-a")
        assert fetched is not None
        assert fetched.value == "v1"

        other = await store.get("agent1", "k1", project_id="other")
        assert other is None

    @pytest.mark.asyncio
    async def test_namespace_isolation(self, store: LocalAgentMemory) -> None:
        await store.set("agent1", "shared_key", "ns1_val", "ns1")
        await store.set("agent1", "shared_key", "ns2_val", "ns2")
        r1 = await store.get("agent1", "shared_key", "ns1")
        r2 = await store.get("agent1", "shared_key", "ns2")
        assert r1 is not None and r1.value == "ns1_val"
        assert r2 is not None and r2.value == "ns2_val"

    @pytest.mark.asyncio
    async def test_set_updates_existing(self, store: LocalAgentMemory) -> None:
        await store.set("agent1", "update_key", "old_val")
        await store.set("agent1", "update_key", "new_val")
        fetched = await store.get("agent1", "update_key")
        assert fetched is not None
        assert fetched.value == "new_val"

    def test_cache_dir_property(self) -> None:
        mem = LocalAgentMemory(DEFAULT_CACHE_DIR)
        assert "local_memory" in mem.cache_dir
        mem.close()
