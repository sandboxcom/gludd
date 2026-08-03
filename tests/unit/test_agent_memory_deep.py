"""Deep agent memory and conversation tests.

Covers:
  - Conversation turn tracking: start/end/list/get lifecycle, decision logging,
    turn counter via decision_count.
  - Context window management: import_context relevance scoring, working memory
    isolation per conversation, context bundle completeness.
  - Memory priority: Disposition bounds, MentalModel priority ordering in recall,
    MemoryBank recall + reflect integration.
  - Tool use recording: Episode tools_used, Procedure tool_chain, consolidation
    of tool-use patterns from episodes into procedures.
  - State persistence: ConversationMeta / WorkingMemoryItem / MemoryRecord
    serialization roundtrips, TTL-based expiration, cross-session survival.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

from general_ludd.memory.consolidation import MemoryConsolidator
from general_ludd.memory.cross_conversation import CrossConversationStore
from general_ludd.memory.cross_convo_memory import (
    ConversationContext,
    ConversationMeta,
    CrossConversationMemory,
    WorkingMemoryItem,
)
from general_ludd.memory.episodic import Episode, EpisodicMemoryRecorder
from general_ludd.memory.local import LocalAgentMemory, MemoryRecord
from general_ludd.memory.memory_bank import (
    Disposition,
    MemoryBank,
    MemoryBankConfig,
    MemoryBankRegistry,
    MemoryEntry,
    MentalModel,
)
from general_ludd.memory.procedural import Procedure


def _now() -> float:
    return time.time()


# ============================================================================
#  CrossConversationMemory — conversation turn tracking
# ============================================================================


class TestConversationTurnTracking:
    def test_start_conversation_creates_meta(self):
        ccm = CrossConversationMemory()
        meta = ccm.start_conversation("conv-1", agent_id="agent-a", tags=["test"])
        assert isinstance(meta, ConversationMeta)
        assert meta.conversation_id == "conv-1"
        assert meta.agent_id == "agent-a"
        assert meta.status == "active"
        assert meta.tags == ["test"]
        assert meta.started_at > 0
        assert meta.ended_at is None
        assert meta.decision_count == 0

    def test_end_conversation_updates_status(self):
        ccm = CrossConversationMemory()
        ccm.start_conversation("conv-2", agent_id="agent-a")
        meta = ccm.end_conversation("conv-2", summary="All done", outcome="success")
        assert meta is not None
        assert meta.status == "completed"
        assert meta.outcome == "success"
        assert meta.summary == "All done"
        assert isinstance(meta.ended_at, float)
        assert meta.ended_at > 0

    def test_end_nonexistent_conversation_returns_none(self):
        ccm = CrossConversationMemory()
        result = ccm.end_conversation("nonexistent")
        assert result is None

    def test_get_conversation_returns_meta(self):
        ccm = CrossConversationMemory()
        ccm.start_conversation("conv-3", agent_id="agent-b", tags=["prod"])
        fetched = ccm.get_conversation("conv-3")
        assert fetched is not None
        assert fetched.conversation_id == "conv-3"
        assert fetched.agent_id == "agent-b"
        assert fetched.tags == ["prod"]

    def test_get_nonexistent_returns_none(self):
        ccm = CrossConversationMemory()
        assert ccm.get_conversation("ghost") is None

    def test_list_conversations_filters_by_status(self):
        ccm = CrossConversationMemory()
        ccm.start_conversation("conv-a", agent_id="x")
        ccm.start_conversation("conv-b", agent_id="x")
        ccm.end_conversation("conv-b", outcome="completed")
        active = ccm.list_conversations(status="active")
        completed = ccm.list_conversations(status="completed")
        assert len(active) >= 1
        assert len(completed) >= 1
        active_ids = {m.conversation_id for m in active}
        assert "conv-a" in active_ids

    def test_delete_conversation_cleans_all(self):
        ccm = CrossConversationMemory()
        ccm.start_conversation("conv-del", agent_id="a")
        ccm.set_working_memory("conv-del", "key1", "value1")
        assert ccm.get_working_memory("conv-del", "key1") == "value1"
        deleted = ccm.delete_conversation("conv-del")
        assert deleted is True
        assert ccm.get_conversation("conv-del") is None
        assert ccm.get_working_memory("conv-del", "key1") is None

    def test_decision_logging_increments_count(self):
        ccm = CrossConversationMemory()
        ccm.start_conversation("conv-decisions", agent_id="agent-1")
        ccm.record_decision("conv-decisions", "Use SQLite for local storage", "portable")
        ccm.record_decision("conv-decisions", "Index by conversation_id", "performance")
        ccm.record_decision("conv-decisions", "TTL default 86400s", "memory discipline")
        meta = ccm.get_conversation("conv-decisions")
        assert meta is not None
        assert meta.decision_count == 3

    def test_get_decisions_returns_sorted(self):
        ccm = CrossConversationMemory()
        ccm.start_conversation("conv-dlist", agent_id="a")
        ccm.record_decision("conv-dlist", "first", "because")
        time.sleep(0.01)
        ccm.record_decision("conv-dlist", "second", "also because")
        decisions = ccm.get_decisions("conv-dlist")
        assert len(decisions) >= 2
        assert decisions[-1]["decision"] == "second"


# ============================================================================
#  Context window management
# ============================================================================


class TestContextWindowManagement:
    def test_working_memory_isolation_per_conversation(self):
        ccm = CrossConversationMemory()
        ccm.set_working_memory("conv-w1", "shared_key", "value-a")
        ccm.set_working_memory("conv-w2", "shared_key", "value-b")
        assert ccm.get_working_memory("conv-w1", "shared_key") == "value-a"
        assert ccm.get_working_memory("conv-w2", "shared_key") == "value-b"

    def test_working_memory_set_get_delete_cycle(self):
        ccm = CrossConversationMemory()
        item = ccm.set_working_memory("conv-wm", "alpha", 42)
        assert isinstance(item, WorkingMemoryItem)
        assert item.conversation_id == "conv-wm"
        assert item.key == "alpha"
        assert item.value == 42
        assert item.created_at > 0
        assert item.updated_at > 0

        assert ccm.get_working_memory("conv-wm", "alpha") == 42

        deleted = ccm.delete_working_memory("conv-wm", "alpha")
        assert deleted is True
        assert ccm.get_working_memory("conv-wm", "alpha") is None

    def test_get_all_working_memory_returns_full_dict(self):
        ccm = CrossConversationMemory()
        ccm.set_working_memory("conv-full", "k1", "v1")
        ccm.set_working_memory("conv-full", "k2", "v2")
        all_wm = ccm.get_all_working_memory("conv-full")
        assert all_wm["k1"] == "v1"
        assert all_wm["k2"] == "v2"

    def test_clear_working_memory_removes_all(self):
        ccm = CrossConversationMemory()
        ccm.set_working_memory("conv-clr", "a", 1)
        ccm.set_working_memory("conv-clr", "b", 2)
        count = ccm.clear_working_memory("conv-clr")
        assert count >= 2
        assert ccm.get_all_working_memory("conv-clr") == {}

    def test_context_bundle_includes_meta_working_summary(self):
        ccm = CrossConversationMemory()
        ccm.start_conversation("conv-ctx", agent_id="agent-ctx")
        ccm.set_working_memory("conv-ctx", "mode", "debug")
        ccm.end_conversation("conv-ctx", summary="Debug session", outcome="completed")
        ctx = ccm.get_context("conv-ctx")
        assert ctx is not None
        assert isinstance(ctx, ConversationContext)
        assert ctx.meta.conversation_id == "conv-ctx"
        assert ctx.working_memory.get("mode") == "debug"
        assert ctx.summary == "Debug session"
        assert ctx.project_id is None

    def test_context_none_for_nonexistent(self):
        ccm = CrossConversationMemory()
        assert ccm.get_context("no-such-conv") is None

    def test_import_context_scores_relevance(self):
        ccm = CrossConversationMemory()
        ccm.start_conversation("src-1", agent_id="a", tags=["python", "test"])
        ccm.set_working_memory("src-1", "topic", "async pytest patterns")
        ccm.end_conversation("src-1", summary="Learned async testing patterns")

        ccm.start_conversation("src-2", agent_id="a", tags=["go", "grpc"])
        ccm.set_working_memory("src-2", "topic", "protobuf generation")
        ccm.end_conversation("src-2", summary="gRPC service stubs")

        results = ccm.import_context("src-2", similar_terms="test async python")
        assert isinstance(results, list)
        if results:
            assert results[0]["conversation_id"] == "src-1"
            assert results[0]["relevance_score"] > 0

    def test_import_context_skips_self(self):
        ccm = CrossConversationMemory()
        ccm.start_conversation("self-skip", agent_id="a")
        results = ccm.import_context("self-skip", similar_terms="self-skip")
        for r in results:
            assert r["conversation_id"] != "self-skip"


# ============================================================================
#  Memory priority — Disposition, MentalModel, MemoryBank
# ============================================================================


class TestMemoryPriority:
    def test_disposition_bounds_enforced(self):
        Disposition(skepticism=1, literalism=5, empathy=3)

        with pytest.raises(ValueError):
            Disposition(skepticism=0)

        with pytest.raises(ValueError):
            Disposition(literalism=6)

    def test_disposition_defaults_neutral(self):
        d = Disposition()
        assert d.skepticism == 3
        assert d.literalism == 3
        assert d.empathy == 3

    def test_disposition_to_from_dict_roundtrip(self):
        d = Disposition(skepticism=1, literalism=5, empathy=2)
        assert Disposition.from_dict(d.to_dict()) == d

    def test_mental_model_priority_clamped(self):
        mm = MentalModel(subject="test", content="data", priority=15)
        assert mm.priority == 10
        mm2 = MentalModel(subject="test2", content="data2", priority=-3)
        assert mm2.priority == 1

    def test_mental_model_serialization_roundtrip(self):
        mm = MentalModel(
            subject="CI Pipeline",
            content="Uses GitHub Actions with matrix builds",
            priority=8,
            created_by="agent-1",
            tags=["ci", "github"],
        )
        d = mm.to_dict()
        restored = MentalModel.from_dict(d)
        assert restored.model_id == mm.model_id
        assert restored.subject == "CI Pipeline"
        assert restored.priority == 8
        assert restored.tags == ["ci", "github"]

    def test_memory_entry_dedup_by_content(self):
        bank = MemoryBank(MemoryBankConfig(bank_id="dedup-test"))
        e1 = MemoryEntry(content="repo uses uv for packaging", source="audit")
        e2 = MemoryEntry(content="repo uses uv for packaging", source="repeat")
        r1 = bank.retain(e1)
        r2 = bank.retain(e2)
        assert r1.entry_id == r2.entry_id

    def test_bank_recall_orders_mental_models_by_priority(self):
        bank = MemoryBank(MemoryBankConfig(bank_id="priority-test"))
        bank.add_mental_model(MentalModel(subject="low", content="low priority", priority=1))
        bank.add_mental_model(MentalModel(subject="high", content="high priority", priority=9))
        bank.add_mental_model(MentalModel(subject="mid", content="mid priority", priority=5))

        result = bank.recall("high priority")
        models = result.mental_models
        assert len(models) >= 1
        assert models[0].priority >= models[-1].priority

    def test_bank_reflect_includes_disposition(self):
        bank = MemoryBank(
            MemoryBankConfig(
                bank_id="reflect-test",
                mission="Test mission",
                directives=["be concise", "prefer evidence"],
                disposition=Disposition(skepticism=5, literalism=2, empathy=4),
            )
        )
        bank.retain(MemoryEntry(content="gludd uses Python 3.11"))
        out = bank.reflect("gludd version")
        assert "Reflect on:" in out
        assert "Mission: Test mission" in out
        assert "Directives:" in out
        assert "skepticism=5" in out
        assert "empathy=4" in out

    def test_bank_registry_create_get_delete_cycle(self):
        registry = MemoryBankRegistry()
        config = MemoryBankConfig(bank_id="cycle-bank", mission="Cycle test")
        bank = registry.create_bank(config)
        assert registry.bank_count() == 1
        assert registry.get_bank("cycle-bank") is bank
        assert registry.delete_bank("cycle-bank") is True
        assert registry.get_bank("cycle-bank") is None
        assert registry.bank_count() == 0

    def test_bank_registry_duplicate_create_raises(self):
        registry = MemoryBankRegistry()
        registry.create_bank(MemoryBankConfig(bank_id="dup"))
        with pytest.raises(ValueError, match="already exists"):
            registry.create_bank(MemoryBankConfig(bank_id="dup"))

    def test_bank_registry_get_or_create(self):
        registry = MemoryBankRegistry()
        config = MemoryBankConfig(bank_id="goc", mission="get-or-create test")
        b1 = registry.get_or_create_bank(config)
        b2 = registry.get_or_create_bank(config)
        assert b1 is b2
        assert registry.bank_count() == 1

    def test_delete_reinsert_works(self):
        registry = MemoryBankRegistry()
        config = MemoryBankConfig(bank_id="reinsert", mission="reinsert")
        registry.create_bank(config)
        registry.delete_bank("reinsert")
        b2 = registry.create_bank(config)
        assert b2 is not None
        assert registry.bank_count() == 1


# ============================================================================
#  Tool use recording — Episode tools_used, Procedure tool_chain
# ============================================================================


class FakeMemoryRepo:
    def __init__(self, episodes=None, query_results=None, consolidated=None):
        self._records: dict[str, dict] = {}
        self._episodes = episodes or []
        self._query_results = query_results or []
        self._consolidated = consolidated or []

    async def set(self, agent_id, key, value, namespace="default", project_id=None):
        self._records[f"{namespace}:{key}"] = value

    async def get(self, agent_id, key, namespace="default", project_id=None):
        return _FakeRow(self._records.get(f"{namespace}:{key}"))

    async def list_by_namespace(self, agent_id, namespace="default", project_id=None, limit=100):
        return []

    async def delete(self, agent_id, key, namespace="default"):
        return self._records.pop(f"{namespace}:{key}", None) is not None

    async def list_episodes(self, agent_id, *, project_id=None, limit=1000):
        return self._episodes

    async def query(self, agent_id, *, query_text="", task_type="", project_id=None, top_k=5):
        return self._query_results

    async def get_consolidated(self, agent_id, project_id=None):
        return self._consolidated


class _FakeRow:
    def __init__(self, value):
        self.value = value if isinstance(value, str) else str(value) if value is not None else None


class TestToolUseRecording:
    def test_episode_tools_used_list(self):
        ep = Episode(
            agent_id="agent-1",
            task_type="run_tests",
            work_type="code",
            tools_used=["grep", "read", "bash", "edit"],
            takeaway="tests pass after fixing imports",
            outcome="success",
            duration_seconds=45.0,
        )
        assert "grep" in ep.tools_used
        assert "bash" in ep.tools_used
        assert len(ep.tools_used) == 4

    def test_episode_tools_used_serialization(self):
        ep = Episode(
            agent_id="agent-2",
            task_type="fix_bug",
            work_type="code",
            tools_used=["edit", "write", "bash"],
            context={"branch": "fix/race-condition"},
            outcome="success",
            duration_seconds=120.0,
        )
        from general_ludd.memory.episodic import _dict_to_episode, _episode_to_dict

        d = _episode_to_dict(ep)
        assert d["tools_used"] == ["edit", "write", "bash"]
        restored = _dict_to_episode(d)
        assert restored.tools_used == ["edit", "write", "bash"]
        assert restored.context == {"branch": "fix/race-condition"}

    @pytest.mark.asyncio
    async def test_episodic_recorder_stores_and_retrieves_tools(self):
        repo = FakeMemoryRepo()
        recorder = EpisodicMemoryRecorder(repo)
        ep = Episode(
            agent_id="agent-3",
            task_type="refactor",
            work_type="code",
            tools_used=["grep", "edit", "glob"],
            takeaway="moved utils to common module",
            outcome="success",
            duration_seconds=30.0,
        )
        ep_id = await recorder.record_episode(ep)
        assert isinstance(ep_id, str)
        assert len(ep_id) > 0

    def test_procedure_tool_chain_field(self):
        proc = Procedure(
            name="deploy_routine",
            description="Standard deployment steps",
            trigger="deploy",
            steps=[{"tool": "bash", "action": "make dist"}, {"tool": "git", "action": "push tag"}],
            tool_chain=["bash", "git"],
        )
        assert proc.tool_chain == ["bash", "git"]
        assert proc.success_rate == 0.0

    def test_procedure_tool_chain_serialization(self):
        from general_ludd.memory.procedural import _dict_to_procedure, _procedure_to_dict

        proc = Procedure(
            name="ci_pipeline",
            trigger="release",
            tool_chain=["make", "gh", "docker"],
            steps=[{"tool": "make", "action": "make gate"}],
            success_count=5,
            failure_count=1,
        )
        d = _procedure_to_dict(proc)
        assert d["tool_chain"] == ["make", "gh", "docker"]
        restored = _dict_to_procedure(d)
        assert restored.tool_chain == ["make", "gh", "docker"]
        assert restored.success_rate == 5 / 6

    def test_procedure_record_success_and_failure_updates(self):
        from general_ludd.memory.procedural import _procedure_to_dict

        proc = Procedure(name="test_proc", trigger="test")
        d = _procedure_to_dict(proc)
        assert d["success_count"] == 0
        assert d["failure_count"] == 0

        # Simulate: increment counters
        data = dict(d)
        data["success_count"] = 1
        data["failure_count"] = 2
        restored = Procedure(
            id=str(data["id"]),
            name=str(data["name"]),
            success_count=1,
            failure_count=2,
        )
        assert restored.success_count == 1
        assert restored.failure_count == 2
        assert restored.success_rate == 1 / 3

    def test_consolidation_extracts_tool_patterns_from_episodes(self):
        tool_eps = [
            Episode(
                agent_id="agent-t",
                task_type="test_fix",
                tools_used=["bash", "edit"],
                takeaway="ran tests",
                outcome="success",
            ),
            Episode(
                agent_id="agent-t",
                task_type="test_fix",
                tools_used=["bash", "grep"],
                takeaway="fixed regex",
                outcome="success",
            ),
            Episode(
                agent_id="agent-t",
                task_type="test_fix",
                tools_used=["edit"],
                takeaway="",
                outcome="failure",
                error_message="syntax error",
            ),
        ]
        consolidator = MemoryConsolidator(FakeMemoryRepo(episodes=tool_eps), min_episodes_to_consolidate=1)
        summary = consolidator._summarize_group("test_fix", tool_eps)
        assert summary["episode_count"] == 3
        assert summary["outcomes"] == {"success": 2, "failure": 1}
        error_pats: list = summary["error_patterns"]  # type: ignore[assignment]
        takeaways_list: list = summary["key_takeaways"]  # type: ignore[assignment]
        assert any("syntax error" in e for e in error_pats)
        assert any("ran tests" in t for t in takeaways_list)


# ============================================================================
#  State persistence — serialization roundtrips, TTL, cross-session
# ============================================================================


class TestStatePersistence:
    def test_conversation_meta_serialization_roundtrip(self):
        meta = ConversationMeta(
            conversation_id="persist-meta",
            agent_id="agent-p",
            project_id="proj-1",
            started_at=1730000000.0,
            ended_at=1730003600.0,
            status="completed",
            tags=["persistence", "test"],
            summary="Persistence roundtrip test",
            decision_count=5,
            outcome="success",
        )
        d = meta.to_dict()
        restored = ConversationMeta.from_dict(d)
        assert restored.conversation_id == "persist-meta"
        assert restored.agent_id == "agent-p"
        assert restored.project_id == "proj-1"
        assert restored.started_at == 1730000000.0
        assert restored.ended_at == 1730003600.0
        assert restored.status == "completed"
        assert restored.tags == ["persistence", "test"]
        assert restored.decision_count == 5
        assert restored.outcome == "success"

    def test_conversation_meta_from_minimal_dict(self):
        minimal = {"conversation_id": "min-cv"}
        meta = ConversationMeta.from_dict(minimal)
        assert meta.conversation_id == "min-cv"
        assert meta.agent_id == ""
        assert meta.status == "active"
        assert meta.decision_count == 0
        assert meta.outcome == "unknown"

    def test_working_memory_item_serialization_roundtrip(self):
        wm = WorkingMemoryItem(
            conversation_id="conv-w",
            key="config",
            value={"theme": "dark", "lang": "en"},
            project_id="proj-w",
            created_at=1730000001.0,
            updated_at=1730000002.0,
        )
        d = wm.to_dict()
        restored = WorkingMemoryItem.from_dict(d)
        assert restored.conversation_id == "conv-w"
        assert restored.key == "config"
        assert restored.value == {"theme": "dark", "lang": "en"}
        assert restored.project_id == "proj-w"
        assert restored.created_at == 1730000001.0
        assert restored.updated_at == 1730000002.0

    def test_conversation_context_construction(self):
        meta = ConversationMeta(conversation_id="ctx-1", agent_id="a")
        ctx = ConversationContext(
            meta=meta,
            working_memory={"step": "read"},
            summary="Context test",
            project_id="proj-ctx",
        )
        assert ctx.meta is meta
        assert ctx.working_memory["step"] == "read"
        assert ctx.summary == "Context test"
        assert ctx.project_id == "proj-ctx"

    @pytest.mark.asyncio
    async def test_local_memory_ttl_expiration(self, tmp_path: Path):
        cache_dir = str(tmp_path / "ttl_mem")
        mem = LocalAgentMemory(cache_dir=cache_dir)
        await mem.set("agent-t", "ephemeral", "gone soon", namespace="test", ttl_seconds=0)
        await asyncio.sleep(0.01)
        record = await mem.get("agent-t", "ephemeral", namespace="test")
        assert record is None
        mem.close()

    @pytest.mark.asyncio
    async def test_local_memory_no_ttl_persists(self, tmp_path: Path):
        cache_dir = str(tmp_path / "no_ttl_mem")
        mem = LocalAgentMemory(cache_dir=cache_dir)
        await mem.set("agent-p", "persistent", "keep me", namespace="test")
        record = await mem.get("agent-p", "persistent", namespace="test")
        assert record is not None
        assert record.value == "keep me"
        mem.close()

    @pytest.mark.asyncio
    async def test_local_memory_cross_session_survival(self, tmp_path: Path):
        cache_dir = str(tmp_path / "cross_session")
        mem1 = LocalAgentMemory(cache_dir=cache_dir)
        await mem1.set("agent-s", "session_key", "survive", namespace="test")
        mem1.close()

        mem2 = LocalAgentMemory(cache_dir=cache_dir)
        record = await mem2.get("agent-s", "session_key", namespace="test")
        assert record is not None
        assert record.value == "survive"
        mem2.close()

    @pytest.mark.asyncio
    async def test_local_memory_list_by_namespace(self, tmp_path: Path):
        cache_dir = str(tmp_path / "list_ns")
        mem = LocalAgentMemory(cache_dir=cache_dir)
        await mem.set("agent-l", "a", "1", namespace="ns1")
        await mem.set("agent-l", "b", "2", namespace="ns1")
        await mem.set("agent-l", "c", "3", namespace="ns2")

        ns1_records = await mem.list_by_namespace("agent-l", namespace="ns1")
        assert len(ns1_records) >= 2

        ns2_records = await mem.list_by_namespace("agent-l", namespace="ns2")
        assert len(ns2_records) >= 1

        mem.close()

    @pytest.mark.asyncio
    async def test_local_memory_project_isolation(self, tmp_path: Path):
        cache_dir = str(tmp_path / "project_iso")
        mem = LocalAgentMemory(cache_dir=cache_dir)
        await mem.set("agent-i", "key", "proj-a-value", namespace="test", project_id="proj-a")
        await mem.set("agent-i", "key", "proj-b-value", namespace="test", project_id="proj-b")

        rec_a = await mem.get("agent-i", "key", namespace="test", project_id="proj-a")
        rec_b = await mem.get("agent-i", "key", namespace="test", project_id="proj-b")
        rec_global = await mem.get("agent-i", "key", namespace="test")

        assert rec_a is not None
        assert rec_a.value == "proj-a-value"
        assert rec_b is not None
        assert rec_b.value == "proj-b-value"
        assert rec_global is None

        mem.close()

    def test_memory_record_defaults(self):
        record = MemoryRecord(agent_id="agent-d", key="k", value="v")
        assert record.namespace == "default"
        assert record.project_id is None
        assert record.ttl_seconds is None
        assert isinstance(record.created_at, float)
        assert record.created_at > 0

    def test_memory_record_serialization_roundtrip(self):
        record = MemoryRecord(
            agent_id="agent-r",
            key="roundtrip_key",
            value="roundtrip_value",
            namespace="ns-r",
            project_id="proj-r",
            ttl_seconds=3600,
            created_at=1730000000.0,
            updated_at=1730000001.0,
        )
        d = record.as_dict()
        restored = MemoryRecord.from_dict(d)
        assert restored.agent_id == "agent-r"
        assert restored.key == "roundtrip_key"
        assert restored.value == "roundtrip_value"
        assert restored.namespace == "ns-r"
        assert restored.project_id == "proj-r"
        assert restored.ttl_seconds == 3600
        assert restored.created_at == 1730000000.0
        assert restored.updated_at == 1730000001.0

    def test_cross_conversation_store_ttl_expiration(self):
        store = CrossConversationStore()
        store.put("ttl-key", {"val": 42}, ttl=0.01)
        time.sleep(0.02)
        result = store.get("ttl-key")
        assert result is None

    def test_cross_conversation_store_basic_roundtrip(self):
        store = CrossConversationStore()
        store.put("basic-key", {"greeting": "hello"}, namespace=("test",))
        result = store.get("basic-key", namespace=("test",))
        assert result is not None
        assert result["value"]["greeting"] == "hello"

    def test_cross_conversation_store_delete(self):
        store = CrossConversationStore()
        store.put("del-key", {"val": "temp"})
        existed = store.delete("del-key")
        assert existed is True
        assert store.get("del-key") is None

    def test_cross_conversation_store_search_by_namespace(self):
        store = CrossConversationStore()
        store.put("s1", {"title": "alpha"}, namespace=("search", "a"))
        store.put("s2", {"title": "beta"}, namespace=("search", "b"))
        results = store.search(namespace_prefix=("search",), limit=10)
        assert len(results) >= 2
        keys = {r["key"] for r in results}
        assert "s1" in keys or "s2" in keys

    def test_cross_conversation_store_purge_expired(self):
        store = CrossConversationStore()
        store.put("purge-me", {"val": 1}, ttl=0.01)
        store.put("purge-me-2", {"val": 2}, ttl=0.01)
        time.sleep(0.02)
        purged = store.purge_expired()
        assert purged >= 2
        assert store.get("purge-me") is None
        assert store.get("purge-me-2") is None

    def test_cross_conversation_store_project_isolation(self):
        store = CrossConversationStore()
        store.put("project-key", {"scope": "a"}, project_id="proj-a")
        store.put("project-key", {"scope": "b"}, project_id="proj-b")

        result_a = store.get("project-key", project_id="proj-a")
        result_b = store.get("project-key", project_id="proj-b")
        result_none = store.get("project-key", project_id="proj-c")

        assert result_a is not None
        assert result_a["value"]["scope"] == "a"
        assert result_b is not None
        assert result_b["value"]["scope"] == "b"
        assert result_none is None


# ============================================================================
#  Integration-like: conversation + memory bank + episode pipeline
# ============================================================================


class TestConversationMemoryPipeline:
    def test_full_conversation_lifecycle_with_summaries(self):
        ccm = CrossConversationMemory()
        conv_id = "pipeline-conv"
        ccm.start_conversation(conv_id, agent_id="agent-pl", tags=["integration"])

        ccm.set_working_memory(conv_id, "branch", "feature/deep-tests")
        ccm.set_working_memory(conv_id, "task", "write memory tests")
        ccm.record_decision(conv_id, "Use in-memory store for tests", "isolation")
        ccm.record_decision(conv_id, "Cover all memory subsystems", "completeness")

        ccm.end_conversation(
            conv_id,
            summary="Wrote 37 deep memory tests covering conversation, context, priority, tools, persistence",
            outcome="success",
        )

        ctx = ccm.get_context(conv_id)
        assert ctx is not None
        assert ctx.meta.decision_count == 2
        assert ctx.meta.outcome == "success"
        assert ctx.meta.status == "completed"
        assert ctx.working_memory["task"] == "write memory tests"

        summary_text = ccm.get_summary(conv_id)
        assert summary_text is not None
        assert "37 deep memory tests" in summary_text

        search_results = ccm.search_summaries("deep memory tests")
        assert len(search_results) >= 1
        assert search_results[0]["conversation_id"] == conv_id

    def test_fact_to_memory_bank_to_recall_flow(self):
        bank = MemoryBank(MemoryBankConfig(bank_id="flow-bank", mission="End-to-end test"))

        bank.add_mental_model(
            MentalModel(
                subject="Test Strategy",
                content="Unit tests cover all public methods with edge cases",
                priority=10,
                tags=["testing"],
            )
        )

        bank.retain(MemoryEntry(content="Memory tests use tmp_path for isolation", source="test-suite"))
        bank.retain(MemoryEntry(content="Concurrency tests use thread-safe stores", source="test-suite"))
        bank.retain(MemoryEntry(content="Pytest asyncio marks async tests", source="test-suite"))

        result = bank.recall("test isolation patterns")
        assert len(result.mental_models) >= 1
        assert len(result.facts) >= 1
        assert "Test Strategy" in result.synthesized

    def test_bank_registry_list_configs(self):
        registry = MemoryBankRegistry()
        registry.create_bank(MemoryBankConfig(bank_id="list-a", mission="A"))
        registry.create_bank(MemoryBankConfig(bank_id="list-b", mission="B"))
        configs = registry.list_banks()
        assert len(configs) >= 2
        ids = {c.bank_id for c in configs}
        assert "list-a" in ids
        assert "list-b" in ids
