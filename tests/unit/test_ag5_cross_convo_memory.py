"""Tests for AG.5 CrossConversationMemory — high-level session/context manager."""

from __future__ import annotations

import time

from general_ludd.memory.cross_conversation import CrossConversationStore
from general_ludd.memory.cross_convo_memory import (
    ConversationContext,
    ConversationMeta,
    CrossConversationMemory,
    WorkingMemoryItem,
)


# =========================================================== dataclass round-trips


class TestConversationMeta:
    def test_defaults(self) -> None:
        meta = ConversationMeta(conversation_id="c1")
        assert meta.conversation_id == "c1"
        assert meta.agent_id == ""
        assert meta.status == "active"
        assert meta.tags == []
        assert meta.summary == ""
        assert meta.decision_count == 0
        assert meta.outcome == "unknown"
        assert meta.ended_at is None
        assert meta.started_at > 0

    def test_to_dict_round_trip(self) -> None:
        meta = ConversationMeta(
            conversation_id="conv-1",
            agent_id="agent-x",
            started_at=1000.0,
            ended_at=2000.0,
            status="completed",
            tags=["bugfix", "urgent"],
            summary="Fixed the thing",
            decision_count=3,
            outcome="success",
        )
        d = meta.to_dict()
        restored = ConversationMeta.from_dict(d)
        assert restored.conversation_id == "conv-1"
        assert restored.agent_id == "agent-x"
        assert restored.started_at == 1000.0
        assert restored.ended_at == 2000.0
        assert restored.status == "completed"
        assert restored.tags == ["bugfix", "urgent"]
        assert restored.summary == "Fixed the thing"
        assert restored.decision_count == 3
        assert restored.outcome == "success"

    def test_from_dict_missing_fields_uses_defaults(self) -> None:
        restored = ConversationMeta.from_dict({"conversation_id": "bare"})
        assert restored.agent_id == ""
        assert restored.status == "active"
        assert restored.tags == []
        assert restored.ended_at is None


class TestWorkingMemoryItem:
    def test_to_dict_round_trip(self) -> None:
        item = WorkingMemoryItem(
            conversation_id="c1",
            key="plan",
            value={"steps": 3, "done": False},
        )
        d = item.to_dict()
        restored = WorkingMemoryItem.from_dict(d)
        assert restored.conversation_id == "c1"
        assert restored.key == "plan"
        assert restored.value == {"steps": 3, "done": False}
        assert isinstance(restored.created_at, float)
        assert isinstance(restored.updated_at, float)

    def test_from_dict_missing_fields(self) -> None:
        restored = WorkingMemoryItem.from_dict({})
        assert restored.conversation_id == ""
        assert restored.key == ""
        assert restored.value is None


# =========================================================== conversation lifecycle


class TestConversationLifecycle:
    def test_start_conversation_returns_meta(self) -> None:
        mem = CrossConversationMemory()
        meta = mem.start_conversation("sess-1", agent_id="agt")
        assert meta.conversation_id == "sess-1"
        assert meta.agent_id == "agt"
        assert meta.status == "active"
        assert meta.started_at > 0

    def test_get_conversation_after_start(self) -> None:
        mem = CrossConversationMemory()
        mem.start_conversation("sess-2", agent_id="a", tags=["t1", "t2"])
        retrieved = mem.get_conversation("sess-2")
        assert retrieved is not None
        assert retrieved.conversation_id == "sess-2"
        assert retrieved.agent_id == "a"
        assert retrieved.tags == ["t1", "t2"]

    def test_get_nonexistent_returns_none(self) -> None:
        mem = CrossConversationMemory()
        assert mem.get_conversation("ghost") is None

    def test_end_conversation_finalises(self) -> None:
        mem = CrossConversationMemory()
        mem.start_conversation("sess-3")
        result = mem.end_conversation("sess-3", summary="All done", outcome="success")
        assert result is not None
        assert result.status == "completed"
        assert result.outcome == "success"
        assert result.summary == "All done"
        assert result.ended_at is not None

    def test_end_nonexistent_returns_none(self) -> None:
        mem = CrossConversationMemory()
        assert mem.end_conversation("no-such") is None

    def test_list_conversations(self) -> None:
        mem = CrossConversationMemory()
        mem.start_conversation("a", agent_id="x")
        mem.start_conversation("b", agent_id="y")
        mem.end_conversation("b")
        all_ = mem.list_conversations()
        assert len(all_) == 2

    def test_list_filter_by_agent_id(self) -> None:
        mem = CrossConversationMemory()
        mem.start_conversation("a1", agent_id="alice")
        mem.start_conversation("a2", agent_id="bob")
        alice = mem.list_conversations(agent_id="alice")
        assert len(alice) == 1
        assert alice[0].agent_id == "alice"

    def test_list_filter_by_status(self) -> None:
        mem = CrossConversationMemory()
        mem.start_conversation("s1")
        mem.start_conversation("s2")
        mem.end_conversation("s2")
        active = mem.list_conversations(status="active")
        completed = mem.list_conversations(status="completed")
        assert len(active) == 1
        assert len(completed) == 1

    def test_delete_conversation_removes_all(self) -> None:
        mem = CrossConversationMemory()
        mem.start_conversation("del-me")
        mem.set_working_memory("del-me", "k", "v")
        assert mem.delete_conversation("del-me") is True
        assert mem.get_conversation("del-me") is None
        assert mem.get_working_memory("del-me", "k") is None

    def test_delete_nonexistent_returns_false(self) -> None:
        mem = CrossConversationMemory()
        assert mem.delete_conversation("gone") is False


# =========================================================== working memory


class TestWorkingMemory:
    def test_set_get_round_trip(self) -> None:
        mem = CrossConversationMemory()
        mem.start_conversation("wm-1")
        mem.set_working_memory("wm-1", "goal", "finish the task")
        val = mem.get_working_memory("wm-1", "goal")
        assert val == "finish the task"

    def test_get_missing_returns_none(self) -> None:
        mem = CrossConversationMemory()
        mem.start_conversation("wm-2")
        assert mem.get_working_memory("wm-2", "nonexistent") is None

    def test_working_memory_scoped_to_conversation(self) -> None:
        mem = CrossConversationMemory()
        mem.start_conversation("wm-a")
        mem.start_conversation("wm-b")
        mem.set_working_memory("wm-a", "x", "alpha")
        mem.set_working_memory("wm-b", "x", "beta")
        assert mem.get_working_memory("wm-a", "x") == "alpha"
        assert mem.get_working_memory("wm-b", "x") == "beta"

    def test_get_all_working_memory(self) -> None:
        mem = CrossConversationMemory()
        mem.start_conversation("wm-3")
        mem.set_working_memory("wm-3", "a", 1)
        mem.set_working_memory("wm-3", "b", 2)
        all_ = mem.get_all_working_memory("wm-3")
        assert all_ == {"a": 1, "b": 2}

    def test_get_all_empty_returns_empty_dict(self) -> None:
        mem = CrossConversationMemory()
        mem.start_conversation("wm-4")
        assert mem.get_all_working_memory("wm-4") == {}

    def test_delete_working_memory(self) -> None:
        mem = CrossConversationMemory()
        mem.start_conversation("wm-5")
        mem.set_working_memory("wm-5", "temp", "val")
        assert mem.delete_working_memory("wm-5", "temp") is True
        assert mem.get_working_memory("wm-5", "temp") is None

    def test_delete_nonexistent_working_memory(self) -> None:
        mem = CrossConversationMemory()
        mem.start_conversation("wm-6")
        assert mem.delete_working_memory("wm-6", "ghost") is False

    def test_clear_working_memory(self) -> None:
        mem = CrossConversationMemory()
        mem.start_conversation("wm-7")
        mem.set_working_memory("wm-7", "x", 1)
        mem.set_working_memory("wm-7", "y", 2)
        mem.set_working_memory("wm-7", "z", 3)
        purged = mem.clear_working_memory("wm-7")
        assert purged == 3
        assert mem.get_all_working_memory("wm-7") == {}

    def test_clear_empty_returns_zero(self) -> None:
        mem = CrossConversationMemory()
        mem.start_conversation("wm-8")
        assert mem.clear_working_memory("wm-8") == 0

    def test_overwrite_updates_value(self) -> None:
        mem = CrossConversationMemory()
        mem.start_conversation("wm-9")
        mem.set_working_memory("wm-9", "counter", 1)
        mem.set_working_memory("wm-9", "counter", 99)
        assert mem.get_working_memory("wm-9", "counter") == 99


# =========================================================== context injection


class TestContextManagement:
    def test_get_context_returns_full_bundle(self) -> None:
        mem = CrossConversationMemory()
        mem.start_conversation("ctx-1", agent_id="a", tags=["t1"])
        mem.set_working_memory("ctx-1", "plan", "do X")
        ctx = mem.get_context("ctx-1")
        assert ctx is not None
        assert ctx.meta.conversation_id == "ctx-1"
        assert ctx.meta.agent_id == "a"
        assert ctx.working_memory == {"plan": "do X"}
        assert ctx.summary == ""

    def test_get_context_with_summary(self) -> None:
        mem = CrossConversationMemory()
        mem.start_conversation("ctx-2")
        mem.end_conversation("ctx-2", summary="Built feature Y")
        ctx = mem.get_context("ctx-2")
        assert ctx is not None
        assert ctx.summary == "Built feature Y"

    def test_get_context_nonexistent_returns_none(self) -> None:
        mem = CrossConversationMemory()
        assert mem.get_context("no-ctx") is None

    def test_import_context_finds_relevant(self) -> None:
        mem = CrossConversationMemory()
        mem.start_conversation("prev-1", tags=["bugfix", "api"])
        mem.end_conversation("prev-1", summary="Fixed API bug")
        mem.start_conversation("prev-2", tags=["feature", "ui"])
        mem.end_conversation("prev-2", summary="Added UI feature")
        results = mem.import_context("current", similar_terms="api bug")
        assert len(results) >= 1
        assert any("api" in str(r.get("tags", [])) for r in results)

    def test_import_context_empty_terms_returns_empty(self) -> None:
        mem = CrossConversationMemory()
        mem.start_conversation("p1")
        mem.end_conversation("p1", summary="Did stuff")
        results = mem.import_context("c", similar_terms="")
        assert results == []

    def test_import_context_excludes_self(self) -> None:
        mem = CrossConversationMemory()
        mem.start_conversation("self", tags=["self"])
        mem.end_conversation("self", summary="Self summary")
        results = mem.import_context("self", similar_terms="self summary")
        for r in results:
            assert r["conversation_id"] != "self"


# =========================================================== summaries


class TestSummaries:
    def test_get_summary_after_end(self) -> None:
        mem = CrossConversationMemory()
        mem.start_conversation("sum-1")
        mem.end_conversation("sum-1", summary="Deployed v2")
        assert mem.get_summary("sum-1") == "Deployed v2"

    def test_get_summary_nonexistent_returns_none(self) -> None:
        mem = CrossConversationMemory()
        assert mem.get_summary("no-sum") is None

    def test_end_without_summary_leaves_none(self) -> None:
        mem = CrossConversationMemory()
        mem.start_conversation("sum-2")
        mem.end_conversation("sum-2")
        assert mem.get_summary("sum-2") is None

    def test_search_summaries_text_match(self) -> None:
        mem = CrossConversationMemory()
        mem.start_conversation("s-a")
        mem.end_conversation("s-a", summary="Refactored auth module")
        mem.start_conversation("s-b")
        mem.end_conversation("s-b", summary="Added rate limiting")
        results = mem.search_summaries("auth")
        assert len(results) == 1
        assert results[0]["conversation_id"] == "s-a"
        assert results[0]["match_type"] == "text"

    def test_search_summaries_tag_match(self) -> None:
        mem = CrossConversationMemory()
        mem.start_conversation("s-c", tags=["security"])
        mem.end_conversation("s-c", summary="Audited deps")
        results = mem.search_summaries("security")
        assert len(results) >= 1

    def test_search_summaries_no_match(self) -> None:
        mem = CrossConversationMemory()
        mem.start_conversation("s-d")
        mem.end_conversation("s-d", summary="Cleanup")
        results = mem.search_summaries("xyzzy")
        assert results == []


# =========================================================== decision log


class TestDecisionLog:
    def test_record_decision_increments_count(self) -> None:
        mem = CrossConversationMemory()
        mem.start_conversation("dec-1")
        mem.record_decision("dec-1", "Use PostgreSQL")
        meta = mem.get_conversation("dec-1")
        assert meta is not None
        assert meta.decision_count == 1

    def test_get_decisions_returns_sorted(self) -> None:
        mem = CrossConversationMemory()
        mem.start_conversation("dec-2")
        mem.record_decision("dec-2", "First")
        time.sleep(0.002)
        mem.record_decision("dec-2", "Second", reasoning="because")
        decisions = mem.get_decisions("dec-2")
        assert len(decisions) == 2
        assert decisions[0]["decision"] == "First"
        assert decisions[1]["decision"] == "Second"
        assert decisions[1]["reasoning"] == "because"

    def test_get_decisions_empty(self) -> None:
        mem = CrossConversationMemory()
        mem.start_conversation("dec-3")
        assert mem.get_decisions("dec-3") == []

    def test_get_decisions_respects_limit(self) -> None:
        mem = CrossConversationMemory()
        mem.start_conversation("dec-4")
        for i in range(5):
            mem.record_decision("dec-4", f"Decision {i}")
        assert len(mem.get_decisions("dec-4", limit=2)) == 2


# =========================================================== graceful degradation


class TestGracefulDegradation:
    def test_works_without_explicit_store(self) -> None:
        mem = CrossConversationMemory()
        mem.start_conversation("gd-1", agent_id="test")
        mem.set_working_memory("gd-1", "k", "v")
        ctx = mem.get_context("gd-1")
        assert ctx is not None
        assert ctx.working_memory == {"k": "v"}

    def test_accepts_explicit_store(self) -> None:
        backend = CrossConversationStore()
        mem = CrossConversationMemory(store=backend)
        mem.start_conversation("gd-2")
        mem.set_working_memory("gd-2", "x", 42)
        assert mem.get_working_memory("gd-2", "x") == 42

    def test_available_property(self) -> None:
        mem = CrossConversationMemory()
        assert mem.available is True

    def test_purge_expired_delegates(self) -> None:
        mem = CrossConversationMemory()
        mem.start_conversation("gd-3")
        mem.set_working_memory("gd-3", "ephemeral", "gone", ttl=0.001)
        time.sleep(0.01)
        purged = mem.purge_expired()
        assert purged >= 1
        assert mem.get_working_memory("gd-3", "ephemeral") is None


# ============================================================ edge cases


class TestEdgeCases:
    def test_multiple_conversations_isolation(self) -> None:
        mem = CrossConversationMemory()
        for i in range(5):
            mem.start_conversation(f"iso-{i}")
            mem.set_working_memory(f"iso-{i}", "id", i)
        for i in range(5):
            assert mem.get_working_memory(f"iso-{i}", "id") == i
        assert len(mem.list_conversations()) == 5

    def test_conversation_id_with_special_chars(self) -> None:
        mem = CrossConversationMemory()
        cid = "session:2024-01-01/user@host"
        mem.start_conversation(cid)
        meta = mem.get_conversation(cid)
        assert meta is not None
        assert meta.conversation_id == cid

    def test_restart_conversation_overwrites_meta(self) -> None:
        mem = CrossConversationMemory()
        mem.start_conversation("restart", agent_id="v1")
        mem.start_conversation("restart", agent_id="v2")
        meta = mem.get_conversation("restart")
        assert meta is not None
        assert meta.agent_id == "v2"
        assert meta.status == "active"

    def test_context_includes_empty_working_memory(self) -> None:
        mem = CrossConversationMemory()
        mem.start_conversation("empty-wm")
        ctx = mem.get_context("empty-wm")
        assert ctx is not None
        assert ctx.working_memory == {}

    def test_list_conversations_respects_limit(self) -> None:
        mem = CrossConversationMemory()
        for i in range(100):
            mem.start_conversation(f"many-{i}")
        results = mem.list_conversations(limit=10)
        assert len(results) <= 10

    def test_conversation_meta_fields_after_rehydration(self) -> None:
        mem = CrossConversationMemory()
        mem.start_conversation("rehydrate", agent_id="a1", tags=["t1"])
        mem.end_conversation("rehydrate", summary="Done", outcome="success")
        retrieved = mem.get_conversation("rehydrate")
        assert retrieved is not None
        assert retrieved.agent_id == "a1"
        assert retrieved.status == "completed"
        assert retrieved.outcome == "success"
        assert retrieved.summary == "Done"
        assert retrieved.tags == ["t1"]
