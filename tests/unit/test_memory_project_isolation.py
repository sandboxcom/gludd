"""Tests for memory project isolation — cross-project bleed prevention."""

from __future__ import annotations

import pytest

from general_ludd.memory.cross_conversation import CrossConversationStore
from general_ludd.memory.cross_convo_memory import (
    ConversationContext,
    ConversationMeta,
    CrossConversationMemory,
    WorkingMemoryItem,
)
from general_ludd.memory.local import LocalAgentMemory, MemoryRecord


# ----------------------------------------------------------------- Lexical ----
class TestMemoryRecordProjectIsolation:
    def test_memory_record_stores_project_id(self) -> None:
        record = MemoryRecord(
            agent_id="agent1", key="k1", value="v1", project_id="proj-a",
        )
        assert record.project_id == "proj-a"

    def test_memory_record_default_project_id_is_none(self) -> None:
        record = MemoryRecord(agent_id="agent1", key="k1", value="v1")
        assert record.project_id is None

    def test_memory_record_project_id_in_as_dict(self) -> None:
        record = MemoryRecord(
            agent_id="a1", key="k1", value="v1", project_id="proj-a",
        )
        data = record.as_dict()
        assert data["project_id"] == "proj-a"

    def test_memory_record_project_id_roundtrip(self) -> None:
        record = MemoryRecord(
            agent_id="a1", key="k1", value="v1", project_id="proj-a",
        )
        rehydrated = MemoryRecord.from_dict(record.as_dict())
        assert rehydrated.project_id == "proj-a"


# ------------------------------------------------------------- LocalAgentMemory
class TestLocalAgentMemoryProjectIsolation:
    @pytest.mark.asyncio
    async def test_different_projects_separate_keys(self) -> None:
        mem = LocalAgentMemory(cache_dir=".gludd/test_local_proj_iso")
        try:
            await mem.set("agent1", "k1", "v-proj-a", project_id="proj-a")
            await mem.set("agent1", "k1", "v-proj-b", project_id="proj-b")
            await mem.set("agent1", "k1", "v-global")

            r_a = await mem.get("agent1", "k1", project_id="proj-a")
            r_b = await mem.get("agent1", "k1", project_id="proj-b")
            r_g = await mem.get("agent1", "k1")

            assert r_a is not None and r_a.value == "v-proj-a"
            assert r_b is not None and r_b.value == "v-proj-b"
            assert r_g is not None and r_g.value == "v-global"
        finally:
            mem.close()
            import shutil

            shutil.rmtree(".gludd/test_local_proj_iso", ignore_errors=True)

    @pytest.mark.asyncio
    async def test_project_a_cannot_read_project_b_data(self) -> None:
        mem = LocalAgentMemory(cache_dir=".gludd/test_local_proj_iso_b")
        try:
            await mem.set("agent1", "secret", "data-for-b", project_id="proj-b")
            r = await mem.get("agent1", "secret", project_id="proj-a")
            assert r is None
        finally:
            mem.close()
            import shutil

            shutil.rmtree(".gludd/test_local_proj_iso_b", ignore_errors=True)

    @pytest.mark.asyncio
    async def test_list_by_namespace_scoped_to_project(self) -> None:
        mem = LocalAgentMemory(cache_dir=".gludd/test_local_proj_iso_c")
        try:
            await mem.set("agent1", "a", "x", project_id="proj-a")
            await mem.set("agent1", "b", "y", project_id="proj-b")
            await mem.set("agent1", "c", "z", project_id="proj-a")

            items_a = await mem.list_by_namespace("agent1", project_id="proj-a")
            items_b = await mem.list_by_namespace("agent1", project_id="proj-b")

            assert len(items_a) == 2
            assert {r.key for r in items_a} == {"a", "c"}
            assert len(items_b) == 1
            assert {r.key for r in items_b} == {"b"}
        finally:
            mem.close()
            import shutil

            shutil.rmtree(".gludd/test_local_proj_iso_c", ignore_errors=True)

    @pytest.mark.asyncio
    async def test_delete_project_scoped(self) -> None:
        mem = LocalAgentMemory(cache_dir=".gludd/test_local_proj_iso_d")
        try:
            await mem.set("agent1", "k", "shared-key", project_id="proj-a")
            await mem.set("agent1", "k", "shared-key", project_id="proj-b")

            deleted = await mem.delete("agent1", "k", project_id="proj-a")
            assert deleted is True

            assert await mem.get("agent1", "k", project_id="proj-a") is None
            assert await mem.get("agent1", "k", project_id="proj-b") is not None
        finally:
            mem.close()
            import shutil

            shutil.rmtree(".gludd/test_local_proj_iso_d", ignore_errors=True)


# -------------------------------------------------------- CrossConversationStore
class TestCrossConversationStoreProjectIsolation:
    def test_put_and_get_with_project_id(self) -> None:
        store = CrossConversationStore()
        store.put("k1", {"val": 42}, namespace=("ns",), project_id="proj-a")
        result = store.get("k1", namespace=("ns",), project_id="proj-a")
        assert result is not None
        assert result["value"] == {"val": 42}
        assert result["project_id"] == "proj-a"

    def test_different_project_cannot_read(self) -> None:
        store = CrossConversationStore()
        store.put("k1", {"secret": "data"}, namespace=("ns",), project_id="proj-b")

        result = store.get("k1", namespace=("ns",), project_id="proj-a")
        assert result is None

    def test_search_filters_by_project(self) -> None:
        store = CrossConversationStore()
        store.put("k1", {"v": 1}, namespace=("ns",), project_id="proj-a")
        store.put("k2", {"v": 2}, namespace=("ns",), project_id="proj-b")

        results_a = store.search(namespace_prefix=("ns",), project_id="proj-a")
        results_b = store.search(namespace_prefix=("ns",), project_id="proj-b")

        assert len(results_a) == 1
        assert results_a[0]["key"] == "k1"
        assert len(results_b) == 1
        assert results_b[0]["key"] == "k2"

    def test_search_no_project_id_returns_all(self) -> None:
        store = CrossConversationStore()
        store.put("k1", {"v": 1}, namespace=("ns",), project_id="proj-a")
        store.put("k2", {"v": 2}, namespace=("ns",), project_id="proj-b")

        results = store.search(namespace_prefix=("ns",))
        assert len(results) == 2

    def test_delete_respects_project_id(self) -> None:
        store = CrossConversationStore()
        store.put("k1", {"v": 1}, namespace=("ns",), project_id="proj-a")
        store.put("k1", {"v": 1}, namespace=("ns",), project_id="proj-b")

        deleted = store.delete("k1", namespace=("ns",), project_id="proj-a")
        assert deleted is True
        assert store.get("k1", namespace=("ns",), project_id="proj-a") is None
        assert store.get("k1", namespace=("ns",), project_id="proj-b") is not None

    def test_put_stores_project_id_in_ephemeral(self) -> None:
        store = CrossConversationStore()
        store.put("k1", {"v": 1}, project_id="proj-x")
        sk = store._store_key(("default",), "k1")
        entry = store._ephemeral.get(sk)
        assert entry is not None
        assert entry["project_id"] == "proj-x"

    def test_get_no_project_for_global_data(self) -> None:
        store = CrossConversationStore()
        store.put("k1", {"v": 1}, project_id=None)
        result_a = store.get("k1", project_id="proj-a")
        assert result_a is not None

    def test_get_none_project_for_scoped_data_returns_none(self) -> None:
        store = CrossConversationStore()
        store.put("k1", {"v": 1}, project_id="proj-b")
        result_a = store.get("k1", project_id="proj-a")
        assert result_a is None


# -------------------------------------------------------- CrossConversationMemory
class TestConversationMetaProjectIsolation:
    def test_meta_stores_project_id(self) -> None:
        meta = ConversationMeta(
            conversation_id="conv1", agent_id="a1", project_id="proj-a",
        )
        assert meta.project_id == "proj-a"

    def test_meta_to_dict_includes_project_id(self) -> None:
        meta = ConversationMeta(
            conversation_id="conv1", agent_id="a1", project_id="proj-a",
        )
        d = meta.to_dict()
        assert d["project_id"] == "proj-a"

    def test_meta_from_dict_roundtrips_project_id(self) -> None:
        meta = ConversationMeta(
            conversation_id="conv1", agent_id="a1", project_id="proj-a",
        )
        rehydrated = ConversationMeta.from_dict(meta.to_dict())
        assert rehydrated.project_id == "proj-a"

    def test_meta_from_dict_no_project_id(self) -> None:
        data = {"conversation_id": "c1", "agent_id": "a1"}
        meta = ConversationMeta.from_dict(data)
        assert meta.project_id is None


class TestWorkingMemoryItemProjectIsolation:
    def test_item_stores_project_id(self) -> None:
        item = WorkingMemoryItem(
            conversation_id="c1", key="k", value="v", project_id="proj-a",
        )
        assert item.project_id == "proj-a"

    def test_item_to_dict_includes_project_id(self) -> None:
        item = WorkingMemoryItem(
            conversation_id="c1", key="k", value="v", project_id="proj-a",
        )
        d = item.to_dict()
        assert d["project_id"] == "proj-a"

    def test_item_from_dict_roundtrips_project_id(self) -> None:
        item = WorkingMemoryItem(
            conversation_id="c1", key="k", value="v", project_id="proj-a",
        )
        rehydrated = WorkingMemoryItem.from_dict(item.to_dict())
        assert rehydrated.project_id == "proj-a"


class TestCrossConversationMemoryProjectIsolation:
    def test_start_conversation_with_project_id(self) -> None:
        mem = CrossConversationMemory()
        meta = mem.start_conversation("conv1", project_id="proj-a")
        assert meta.project_id == "proj-a"

    def test_conversation_isolation_between_projects(self) -> None:
        mem = CrossConversationMemory()
        mem.start_conversation("conv-a", project_id="proj-a")
        mem.start_conversation("conv-b", project_id="proj-b")
        mem.start_conversation("conv-global")

        convs_a = mem.list_conversations(project_id="proj-a")
        convs_b = mem.list_conversations(project_id="proj-b")

        assert len(convs_a) == 1
        assert convs_a[0].conversation_id == "conv-a"
        assert len(convs_b) == 1
        assert convs_b[0].conversation_id == "conv-b"

    def test_list_conversations_no_project_returns_all(self) -> None:
        mem = CrossConversationMemory()
        mem.start_conversation("c1", project_id="proj-a")
        mem.start_conversation("c2", project_id="proj-b")

        all_convs = mem.list_conversations()
        assert len(all_convs) == 2

    def test_set_working_memory_with_project_id(self) -> None:
        mem = CrossConversationMemory()
        item = mem.set_working_memory("conv1", "k1", "v1", project_id="proj-a")
        assert item.project_id == "proj-a"

    def test_import_context_respects_project(self) -> None:
        mem = CrossConversationMemory()
        mem.start_conversation("shared-conv", project_id="proj-a")
        mem.set_working_memory("shared-conv", "desc", "security review", project_id="proj-a")

        start_conv_b = mem.start_conversation("other-conv", project_id="proj-b")
        contexts = mem.import_context(
            start_conv_b.conversation_id, similar_terms="security", project_id="proj-b",
        )
        assert len(contexts) == 0

    def test_import_context_finds_same_project(self) -> None:
        mem = CrossConversationMemory()
        mem.start_conversation("ref-conv", project_id="proj-a")
        mem.set_working_memory("ref-conv", "desc", "database migration", project_id="proj-a")

        current = mem.start_conversation("current", project_id="proj-a")
        contexts = mem.import_context(
            current.conversation_id, similar_terms="migration", project_id="proj-a",
        )
        assert len(contexts) == 1
        assert contexts[0]["conversation_id"] == "ref-conv"

    def test_end_conversation_preserves_project_id(self) -> None:
        mem = CrossConversationMemory()
        mem.start_conversation("conv1", project_id="proj-a")
        meta = mem.end_conversation("conv1", summary="done", outcome="success")
        assert meta is not None
        assert meta.project_id == "proj-a"

    def test_get_context_includes_project_id(self) -> None:
        mem = CrossConversationMemory()
        mem.start_conversation("conv1", project_id="proj-a")
        ctx = mem.get_context("conv1")
        assert ctx is not None
        assert ctx.project_id == "proj-a"

    def test_search_summaries_by_project(self) -> None:
        mem = CrossConversationMemory()
        mem.start_conversation("conv-a", project_id="proj-a")
        mem.end_conversation("conv-a", summary="api fix applied", outcome="success")
        mem.start_conversation("conv-b", project_id="proj-b")
        mem.end_conversation("conv-b", summary="api refactor done", outcome="success")

        results_a = mem.search_summaries("api", project_id="proj-a")
        results_b = mem.search_summaries("api", project_id="proj-b")

        assert len(results_a) == 1
        assert results_a[0]["conversation_id"] == "conv-a"
        assert len(results_b) == 1
        assert results_b[0]["conversation_id"] == "conv-b"


class TestConversationContextProjectIsolation:
    def test_context_stores_project_id(self) -> None:
        meta = ConversationMeta(conversation_id="c1", project_id="proj-a")
        ctx = ConversationContext(meta=meta, working_memory={}, project_id="proj-a")
        assert ctx.project_id == "proj-a"
