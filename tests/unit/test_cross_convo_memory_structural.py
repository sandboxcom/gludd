"""Structural tests for memory/cross_convo_memory.py — ConversationMeta, WorkingMemoryItem, CrossConversationMemory."""

from __future__ import annotations

import time

from general_ludd.memory.cross_conversation import CrossConversationStore
from general_ludd.memory.cross_convo_memory import (
    ConversationContext,
    ConversationMeta,
    CrossConversationMemory,
    WorkingMemoryItem,
)


class TestConversationMeta:
    def test_defaults(self):
        meta = ConversationMeta(conversation_id="conv-1")
        assert meta.conversation_id == "conv-1"
        assert meta.agent_id == ""
        assert meta.started_at > 0
        assert meta.ended_at is None
        assert meta.status == "active"
        assert meta.tags == []
        assert meta.summary == ""
        assert meta.decision_count == 0
        assert meta.outcome == "unknown"

    def test_explicit_fields(self):
        now = time.time()
        meta = ConversationMeta(
            conversation_id="conv-2",
            agent_id="agent-42",
            started_at=now - 3600,
            ended_at=now,
            status="completed",
            tags=["bugfix", "python"],
            summary="Fixed the bug",
            decision_count=5,
            outcome="success",
        )
        assert meta.conversation_id == "conv-2"
        assert meta.agent_id == "agent-42"
        assert meta.status == "completed"
        assert meta.tags == ["bugfix", "python"]
        assert meta.summary == "Fixed the bug"
        assert meta.decision_count == 5
        assert meta.outcome == "success"

    def test_to_dict(self):
        meta = ConversationMeta(conversation_id="conv-3", agent_id="a1", tags=["t1"])
        d = meta.to_dict()
        assert d["conversation_id"] == "conv-3"
        assert d["agent_id"] == "a1"
        assert d["tags"] == ["t1"]
        assert d["status"] == "active"
        assert "started_at" in d

    def test_from_dict(self):
        data = {
            "conversation_id": "conv-4",
            "agent_id": "a2",
            "started_at": 1000.0,
            "ended_at": 2000.0,
            "status": "completed",
            "tags": ["x"],
            "summary": "done",
            "decision_count": 3,
            "outcome": "success",
        }
        meta = ConversationMeta.from_dict(data)
        assert meta.conversation_id == "conv-4"
        assert meta.started_at == 1000.0
        assert meta.ended_at == 2000.0
        assert meta.status == "completed"

    def test_from_dict_none_ended_at(self):
        data = {"conversation_id": "c5", "started_at": 500.0, "ended_at": None}
        meta = ConversationMeta.from_dict(data)
        assert meta.ended_at is None

    def test_roundtrip(self):
        meta = ConversationMeta(
            conversation_id="c-round", agent_id="a", tags=["t1", "t2"], decision_count=2
        )
        d = meta.to_dict()
        restored = ConversationMeta.from_dict(d)
        assert restored.conversation_id == meta.conversation_id
        assert restored.agent_id == meta.agent_id
        assert restored.tags == meta.tags
        assert restored.decision_count == meta.decision_count


class TestWorkingMemoryItem:
    def test_defaults(self):
        item = WorkingMemoryItem(conversation_id="c1", key="k1", value="v1")
        assert item.conversation_id == "c1"
        assert item.key == "k1"
        assert item.value == "v1"
        assert item.created_at > 0
        assert item.updated_at > 0

    def test_complex_value(self):
        value = {"nested": [1, 2, 3], "flag": True}
        item = WorkingMemoryItem(conversation_id="c2", key="config", value=value)
        assert item.value == value

    def test_to_dict(self):
        item = WorkingMemoryItem(conversation_id="c3", key="k", value="v")
        d = item.to_dict()
        assert d["conversation_id"] == "c3"
        assert d["key"] == "k"
        assert d["value"] == "v"

    def test_from_dict(self):
        data = {"conversation_id": "c4", "key": "k4", "value": 42, "created_at": 100.0, "updated_at": 200.0}
        item = WorkingMemoryItem.from_dict(data)
        assert item.conversation_id == "c4"
        assert item.key == "k4"
        assert item.value == 42
        assert item.created_at == 100.0
        assert item.updated_at == 200.0

    def test_roundtrip(self):
        item = WorkingMemoryItem(conversation_id="c5", key="key5", value=[1, 2, 3])
        d = item.to_dict()
        restored = WorkingMemoryItem.from_dict(d)
        assert restored.conversation_id == item.conversation_id
        assert restored.key == item.key
        assert restored.value == item.value


class TestConversationContext:
    def test_defaults(self):
        meta = ConversationMeta(conversation_id="ctx-1")
        ctx = ConversationContext(meta=meta)
        assert ctx.meta is meta
        assert ctx.working_memory == {}
        assert ctx.summary == ""

    def test_with_working_memory(self):
        meta = ConversationMeta(conversation_id="ctx-2")
        wm = {"key1": "val1", "key2": 42}
        ctx = ConversationContext(meta=meta, working_memory=wm, summary="test summary")
        assert ctx.working_memory == wm
        assert ctx.summary == "test summary"


class TestCrossConversationMemory:
    def test_init_with_default_store(self):
        mem = CrossConversationMemory()
        assert mem.available is True

    def test_init_with_explicit_store(self):
        store = CrossConversationStore()
        mem = CrossConversationMemory(store=store)
        assert mem.available is True

    def test_start_conversation(self):
        mem = CrossConversationMemory()
        meta = mem.start_conversation("conv-start", agent_id="a1", tags=["test"])
        assert meta.conversation_id == "conv-start"
        assert meta.agent_id == "a1"
        assert meta.tags == ["test"]
        assert meta.status == "active"

    def test_end_conversation(self):
        mem = CrossConversationMemory()
        mem.start_conversation("conv-end")
        result = mem.end_conversation("conv-end", summary="done", outcome="success")
        assert result is not None
        assert result.status == "completed"
        assert result.outcome == "success"
        assert result.ended_at is not None
        assert "done" in result.summary

    def test_end_nonexistent_conversation(self):
        mem = CrossConversationMemory()
        result = mem.end_conversation("nonexistent")
        assert result is None

    def test_get_conversation(self):
        mem = CrossConversationMemory()
        mem.start_conversation("conv-get", agent_id="a99")
        meta = mem.get_conversation("conv-get")
        assert meta is not None
        assert meta.agent_id == "a99"

    def test_get_nonexistent_conversation(self):
        mem = CrossConversationMemory()
        assert mem.get_conversation("missing") is None

    def test_list_conversations(self):
        mem = CrossConversationMemory()
        mem.start_conversation("c1", agent_id="a1")
        mem.start_conversation("c2", agent_id="a2")
        mem.end_conversation("c2", summary="done")
        all_c = mem.list_conversations()
        assert len(all_c) >= 2

        active = mem.list_conversations(status="active")
        assert all(c.status == "active" for c in active)

        by_agent = mem.list_conversations(agent_id="a1")
        assert all(c.agent_id == "a1" for c in by_agent)

    def test_delete_conversation(self):
        mem = CrossConversationMemory()
        mem.start_conversation("conv-del")
        assert mem.delete_conversation("conv-del") is True
        assert mem.get_conversation("conv-del") is None

    def test_get_context(self):
        mem = CrossConversationMemory()
        mem.start_conversation("conv-ctx")
        mem.set_working_memory("conv-ctx", "plan", "deploy to prod")
        ctx = mem.get_context("conv-ctx")
        assert ctx is not None
        assert ctx.meta.conversation_id == "conv-ctx"
        assert ctx.working_memory.get("plan") == "deploy to prod"

    def test_get_context_nonexistent(self):
        mem = CrossConversationMemory()
        assert mem.get_context("no-such") is None

    def test_set_and_get_working_memory(self):
        mem = CrossConversationMemory()
        mem.start_conversation("conv-wm")
        mem.set_working_memory("conv-wm", "status", "in_progress")
        assert mem.get_working_memory("conv-wm", "status") == "in_progress"

    def test_get_working_memory_missing(self):
        mem = CrossConversationMemory()
        mem.start_conversation("conv-wm2")
        assert mem.get_working_memory("conv-wm2", "no-key") is None

    def test_get_all_working_memory(self):
        mem = CrossConversationMemory()
        mem.start_conversation("conv-all")
        mem.set_working_memory("conv-all", "k1", "v1")
        mem.set_working_memory("conv-all", "k2", 42)
        all_wm = mem.get_all_working_memory("conv-all")
        assert all_wm.get("k1") == "v1"
        assert all_wm.get("k2") == 42

    def test_delete_working_memory(self):
        mem = CrossConversationMemory()
        mem.start_conversation("conv-del-wm")
        mem.set_working_memory("conv-del-wm", "temp", "data")
        assert mem.delete_working_memory("conv-del-wm", "temp") is True

    def test_clear_working_memory(self):
        mem = CrossConversationMemory()
        mem.start_conversation("conv-clear")
        mem.set_working_memory("conv-clear", "a", 1)
        mem.set_working_memory("conv-clear", "b", 2)
        count = mem.clear_working_memory("conv-clear")
        assert count == 2
        assert mem.get_all_working_memory("conv-clear") == {}

    def test_record_decision(self):
        mem = CrossConversationMemory()
        mem.start_conversation("conv-dec")
        item = mem.record_decision("conv-dec", "Use PostgreSQL", "better for ORM")
        assert item.conversation_id == "conv-dec"
        decisions = mem.get_decisions("conv-dec")
        assert len(decisions) >= 1

    def test_get_decisions_empty(self):
        mem = CrossConversationMemory()
        mem.start_conversation("conv-no-dec")
        decisions = mem.get_decisions("conv-no-dec")
        assert decisions == []

    def test_search_summaries(self):
        mem = CrossConversationMemory()
        mem.start_conversation("conv-s1", tags=["deploy"])
        mem.end_conversation("conv-s1", summary="Deployed to staging")
        results = mem.search_summaries("deploy")
        assert len(results) >= 1

    def test_search_summaries_no_match(self):
        mem = CrossConversationMemory()
        results = mem.search_summaries("xyzzy_no_match_xyzzy")
        assert results == []

    def test_import_context(self):
        mem = CrossConversationMemory()
        mem.start_conversation("conv-import", tags=["api"])
        mem.end_conversation("conv-import", summary="Built REST API")
        contexts = mem.import_context("conv-current", similar_terms="api rest")
        assert len(contexts) >= 1

    def test_purge_expired(self):
        mem = CrossConversationMemory()
        assert isinstance(mem.purge_expired(), int)
