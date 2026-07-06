"""Unit tests for AutoMemory subsystem: episodic memory, consolidation,
memory retrieval with relevance scoring, and cross-task learning."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest_asyncio
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from general_ludd.db.models import Base
from general_ludd.db.repository import MemoryRepository
from general_ludd.memory.consolidation import MemoryConsolidator
from general_ludd.memory.cross_task import CrossTaskLearner
from general_ludd.memory.episodic import (
    EPISODIC_NAMESPACE,
    Episode,
    EpisodicMemoryRecorder,
)
from general_ludd.memory.retrieval import MemoryRetriever


@pytest_asyncio.fixture
async def memory_repo():
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        echo=False,
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, _connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    repo = MemoryRepository(session_factory=session_factory)
    yield repo
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture
async def recorder(memory_repo):
    return EpisodicMemoryRecorder(memory_repo)


@pytest_asyncio.fixture
async def retriever(memory_repo):
    return MemoryRetriever(memory_repo)


@pytest_asyncio.fixture
async def consolidator(memory_repo):
    return MemoryConsolidator(memory_repo)


@pytest_asyncio.fixture
async def learner(memory_repo):
    return CrossTaskLearner(memory_repo)


class FakeGateway:
    """Fake model gateway for consolidation tests."""

    def __init__(self, response_text: str = '{"strengths":["good"],"weaknesses":["slow"],"recommendation":"improve"}'):
        self.response_text = response_text
        self.last_prompt = ""
        self.call_count = 0
        self.last_work_type = ""

    def call_model(self, profile_id, messages, work_type="unknown"):
        self.call_count += 1
        self.last_prompt = messages[0]["content"]
        self.last_work_type = work_type

        class FakeResponse:
            content = self.response_text
        return FakeResponse()

    def complete(self, prompt):
        self.call_count += 1
        self.last_prompt = prompt

        class FakeResponse:
            content = self.response_text
        return FakeResponse()


# ---------------------------------------------------------------------------
# Episodic memory tests
# ---------------------------------------------------------------------------

class TestEpisodicMemoryRecorder:

    async def test_record_and_get_episode(self, recorder):
        ep_id = await recorder.record_completion(
            agent_id="agent1",
            task_type="refactor",
            work_type="code",
            priority="high",
            outcome="success",
            takeaway="Use dataclasses for structured data",
            context={"files": ["a.py", "b.py"]},
        )
        assert ep_id

        retrieved = await recorder.get_episode("agent1", ep_id)
        assert retrieved is not None
        assert retrieved.task_type == "refactor"
        assert retrieved.outcome == "success"
        assert retrieved.takeaway == "Use dataclasses for structured data"
        assert retrieved.context == {"files": ["a.py", "b.py"]}
        assert retrieved.priority == "high"

    async def test_record_episode_object(self, recorder):
        ep = Episode(
            agent_id="agent2",
            task_type="debug",
            work_type="test",
            priority="low",
            outcome="failure",
            error_message="TypeError on line 42",
            tools_used=["pytest", "mypy"],
            duration_seconds=120.5,
        )
        ep_id = await recorder.record_episode(ep)
        assert ep_id == ep.id

        retrieved = await recorder.get_episode("agent2", ep_id)
        assert retrieved is not None
        assert retrieved.error_message == "TypeError on line 42"
        assert retrieved.tools_used == ["pytest", "mypy"]
        assert retrieved.duration_seconds == 120.5
        assert retrieved.outcome == "failure"

    async def test_get_nonexistent_returns_none(self, recorder):
        result = await recorder.get_episode("agent1", "nonexistent_id")
        assert result is None

    async def test_list_episodes(self, recorder):
        await recorder.record_completion(agent_id="a1", task_type="refactor", outcome="success", takeaway="t1")
        await recorder.record_completion(agent_id="a1", task_type="test", outcome="failure", error_message="e1")
        await recorder.record_completion(agent_id="a1", task_type="refactor", outcome="success", takeaway="t2")

        all_eps = await recorder.list_episodes("a1")
        assert len(all_eps) == 3

    async def test_list_filter_by_task_type(self, recorder):
        await recorder.record_completion(agent_id="a1", task_type="refactor", outcome="success")
        await recorder.record_completion(agent_id="a1", task_type="test", outcome="success")
        await recorder.record_completion(agent_id="a1", task_type="refactor", outcome="failure")

        refactors = await recorder.list_episodes("a1", task_type="refactor")
        assert len(refactors) == 2
        assert all(e.task_type == "refactor" for e in refactors)

    async def test_list_filter_by_outcome(self, recorder):
        await recorder.record_completion(agent_id="a1", task_type="t1", outcome="success")
        await recorder.record_completion(agent_id="a1", task_type="t2", outcome="failure")
        await recorder.record_completion(agent_id="a1", task_type="t3", outcome="success")

        failures = await recorder.list_by_outcome("a1", "failure")
        assert len(failures) == 1
        assert failures[0].outcome == "failure"

    async def test_list_empty_agent(self, recorder):
        result = await recorder.list_episodes("nonexistent")
        assert result == []

    async def test_episode_json_roundtrip(self, recorder, memory_repo):
        ep = Episode(
            agent_id="a1",
            task_type="implement",
            context={"nested": {"key": "value"}, "list": [1, 2, 3]},
            tools_used=["tool_a", "tool_b"],
        )
        ep_id = await recorder.record_episode(ep)

        stored_row = await memory_repo.get("a1", ep_id, namespace=EPISODIC_NAMESPACE)
        assert stored_row is not None
        parsed = json.loads(stored_row.value)
        assert parsed["context"] == {"nested": {"key": "value"}, "list": [1, 2, 3]}
        assert parsed["tools_used"] == ["tool_a", "tool_b"]


# ---------------------------------------------------------------------------
# Memory retrieval tests
# ---------------------------------------------------------------------------

class TestMemoryRetrieval:

    async def _seed_for_retrieval(self, recorder):
        await recorder.record_completion(
            agent_id="a1", task_type="refactor",
            outcome="success",
            takeaway="Using list comprehensions improved performance significantly",
            context={"files": ["src/main.py"]},
        )
        await recorder.record_completion(
            agent_id="a1", task_type="debug",
            outcome="failure",
            error_message="ImportError: cannot import name 'foo'",
            takeaway="Always check circular imports first",
        )
        await recorder.record_completion(
            agent_id="a1", task_type="test",
            outcome="success",
            takeaway="Parametrized tests caught edge cases missed by manual review",
        )
        await recorder.record_completion(
            agent_id="a1", task_type="refactor",
            outcome="failure",
            error_message="ModuleNotFoundError when importing utils",
            takeaway="Verify module paths after moving files",
        )

    async def test_query_returns_scored_results(self, recorder, retriever):
        await self._seed_for_retrieval(recorder)
        results = await retriever.query("a1", "import error module not found")
        assert len(results) > 0
        assert all(hasattr(r, "score") for r in results)
        assert all(hasattr(r, "episode") for r in results)
        assert results[0].score >= results[-1].score

    async def test_query_scores_relevant_higher(self, recorder, retriever):
        await self._seed_for_retrieval(recorder)
        results = await retriever.query("a1", "import error")
        assert len(results) > 0
        top = results[0]
        assert top.episode.error_message is not None
        assert "import" in top.episode.error_message.lower() or "import" in (top.episode.takeaway or "").lower()

    async def test_query_filter_by_task_type(self, recorder, retriever):
        await self._seed_for_retrieval(recorder)
        results = await retriever.query("a1", "performance", task_type="refactor")
        assert len(results) > 0
        for r in results:
            assert r.episode.task_type == "refactor"

    async def test_query_respects_top_k(self, recorder, retriever):
        await self._seed_for_retrieval(recorder)
        results = await retriever.query("a1", "test", top_k=1)
        assert len(results) <= 1

    async def test_query_respects_min_score(self, recorder, retriever):
        await self._seed_for_retrieval(recorder)
        results = await retriever.query("a1", "completely unrelated zxcvbnm phrase", min_score=0.1)
        assert len(results) == 0

    async def test_query_empty_results_for_no_episodes(self, retriever):
        results = await retriever.query("no_agent", "anything")
        assert results == []

    async def test_recency_boost(self, recorder, retriever):
        ep_recent = Episode(
            agent_id="a2", task_type="docs", outcome="success",
            takeaway="Recently written docs use markdown tables",
            created_at=datetime.now(UTC).isoformat(),
        )
        ep_old = Episode(
            agent_id="a2", task_type="docs", outcome="success",
            takeaway="Old docs use markdown tables for structured data",
            created_at="2020-01-01T00:00:00+00:00",
        )
        await recorder.record_episode(ep_recent)
        await recorder.record_episode(ep_old)

        results = await retriever.query("a2", "markdown tables docs")
        assert len(results) >= 2
        assert results[0].episode.id == ep_recent.id


# ---------------------------------------------------------------------------
# Memory consolidation tests
# ---------------------------------------------------------------------------

class TestMemoryConsolidation:

    async def _seed_old_episodes(self, recorder):
        old_created = "2020-01-01T00:00:00+00:00"
        for i in range(15):
            await recorder.record_episode(Episode(
                agent_id="a1", task_type="refactor",
                outcome="success" if i % 3 != 0 else "failure",
                takeaway=f"takeaway_{i}" if i % 3 != 0 else "",
                error_message=f"error_{i}" if i % 3 == 0 else "",
                created_at=old_created,
            ))

    async def test_consolidate_with_old_episodes(self, memory_repo, recorder, consolidator):
        await self._seed_old_episodes(recorder)
        result = await consolidator.consolidate("a1")
        assert result["consolidated"] >= 1
        assert "refactor" in result["task_types"]
        assert result["episodes_consolidated"] >= 10

        consolidated = await consolidator.get_consolidated("a1")
        assert len(consolidated) >= 1

    async def test_consolidate_skips_if_insufficient(self, memory_repo, recorder, consolidator):
        await recorder.record_completion(agent_id="a1", task_type="test", outcome="success")
        result = await consolidator.consolidate("a1")
        assert result["consolidated"] == 0
        assert "reason" in result

    async def test_consolidate_with_force(self, memory_repo, recorder):
        for _i in range(5):
            await recorder.record_completion(agent_id="a1", task_type="test", outcome="success")
        consolidator_f = MemoryConsolidator(
            memory_repo, min_episodes_to_consolidate=1, max_episode_age_hours=0.0
        )
        result = await consolidator_f.consolidate("a1", force=True)
        assert result["consolidated"] >= 1

    async def test_consolidate_summary_has_correct_structure(self, memory_repo, recorder, consolidator):
        await self._seed_old_episodes(recorder)
        await consolidator.consolidate("a1")
        consolidated = await consolidator.get_consolidated("a1")

        for summary in consolidated:
            if "outcomes" in summary:
                assert "success" in summary["outcomes"] or "failure" in summary["outcomes"]
            if "episode_count" in summary:
                assert summary["episode_count"] >= 1
            if "error_patterns" in summary:
                assert isinstance(summary["error_patterns"], list)

    async def test_consolidate_with_model_gateway(self, memory_repo, recorder):
        await self._seed_old_episodes(recorder)
        fake_gw = FakeGateway()
        consolidator_gw = MemoryConsolidator(memory_repo, model_gateway=fake_gw)
        result = await consolidator_gw.consolidate("a1")
        assert result["consolidated"] >= 1
        assert fake_gw.call_count >= 1

    async def test_consolidate_model_failure_graceful(self, memory_repo, recorder):
        await self._seed_old_episodes(recorder)

        class BrokenGateway:
            def call_model(self, *args, **kwargs):
                raise RuntimeError("simulated failure")

        consolidator_broken = MemoryConsolidator(memory_repo, model_gateway=BrokenGateway())
        result = await consolidator_broken.consolidate("a1")
        assert result["consolidated"] >= 1  # static consolidation still works


# ---------------------------------------------------------------------------
# Cross-task learning tests
# ---------------------------------------------------------------------------

class TestCrossTaskLearner:

    async def _seed_diverse_episodes(self, recorder):
        await recorder.record_completion(
            agent_id="a1", task_type="refactor", outcome="success",
            takeaway="Extract small functions first",
        )
        await recorder.record_completion(
            agent_id="a1", task_type="refactor", outcome="success",
            takeaway="Use dataclasses for structured data",
        )
        await recorder.record_completion(
            agent_id="a1", task_type="refactor", outcome="failure",
            error_message="ImportError: cannot import name 'Config'",
        )
        await recorder.record_completion(
            agent_id="a1", task_type="test", outcome="success",
            takeaway="Parametrized tests catch more edge cases",
        )
        await recorder.record_completion(
            agent_id="a1", task_type="test", outcome="failure",
            error_message="AssertionError: expected 5, got 6",
        )
        await recorder.record_completion(
            agent_id="a1", task_type="debug", outcome="failure",
            error_message="ImportError: cannot import name 'Config'",
        )
        await recorder.record_completion(
            agent_id="a1", task_type="debug", outcome="failure",
            error_message="ImportError: cannot import name 'Config'",
        )

    async def test_learn_patterns(self, recorder, learner):
        await self._seed_diverse_episodes(recorder)
        patterns = await learner.learn_patterns("a1")

        assert patterns["total_episodes"] == 7
        assert patterns["overall_success_rate_pct"] < 100
        assert patterns["overall_failure_rate_pct"] > 0
        assert "per_type_analysis" in patterns
        assert "refactor" in patterns["per_type_analysis"]
        assert "test" in patterns["per_type_analysis"]
        assert "debug" in patterns["per_type_analysis"]

    async def test_learn_patterns_empty(self, learner):
        patterns = await learner.learn_patterns("nonexistent")
        assert patterns["total_episodes"] == 0
        assert patterns["patterns_found"] == 0

    async def test_learn_patterns_recurring_errors(self, recorder, learner):
        await self._seed_diverse_episodes(recorder)
        patterns = await learner.learn_patterns("a1")
        recurring = patterns.get("recurring_errors", [])
        import_error = next(
            (e for e in recurring if "ImportError" in e.get("error", "")), None
        )
        assert import_error is not None
        assert import_error["count"] >= 2

    async def test_learn_patterns_effective_strategies(self, recorder, learner):
        await self._seed_diverse_episodes(recorder)
        patterns = await learner.learn_patterns("a1")
        strategies = patterns.get("effective_strategies", [])
        assert len(strategies) >= 1

    async def test_recommend_for_task(self, recorder, learner):
        await self._seed_diverse_episodes(recorder)
        rec = await learner.recommend_for_task("a1", "refactor", "restructuring code")
        assert rec["task_type"] == "refactor"
        assert rec["similar_successes"] >= 1
        assert rec["similar_failures"] >= 1
        assert "recommendations" in rec
        assert "warnings" in rec

    async def test_recommend_for_unknown_task(self, recorder, learner):
        rec = await learner.recommend_for_task("a1", "nonexistent_task")
        assert rec["relevant_episodes"] == 0
        assert rec["recommendations"] == []

    async def test_generate_improvement_report(self, recorder, learner):
        await self._seed_diverse_episodes(recorder)
        await recorder.record_completion(
            agent_id="a1", task_type="deploy", outcome="failure",
            error_message="Permission denied",
        )
        await recorder.record_completion(
            agent_id="a1", task_type="deploy", outcome="failure",
            error_message="Connection refused",
        )
        await recorder.record_completion(
            agent_id="a1", task_type="deploy", outcome="failure",
            error_message="Timeout",
        )
        report = await learner.generate_improvement_report("a1")
        assert "improvements_needed" in report
        assert report["patterns_found"] > 0

    async def test_per_type_analysis_correct(self, recorder, learner):
        await recorder.record_completion(agent_id="a1", task_type="docs", outcome="success")
        await recorder.record_completion(agent_id="a1", task_type="docs", outcome="success")
        await recorder.record_completion(agent_id="a1", task_type="docs", outcome="success")
        patterns = await learner.learn_patterns("a1")
        per_type = patterns["per_type_analysis"].get("docs", {})
        assert per_type["total"] == 3
        assert per_type["success_rate_pct"] == 100.0


# ---------------------------------------------------------------------------
# Integration test — full pipeline
# ---------------------------------------------------------------------------

class TestAutoMemoryPipeline:

    async def test_full_pipeline_record_retrieve_consolidate_learn(
        self, memory_repo, recorder, retriever, consolidator, learner
    ):
        # 1. Record episodes
        for i in range(20):
            await recorder.record_completion(
                agent_id="agent_pipe",
                task_type="refactor" if i % 2 == 0 else "test",
                outcome="success" if i % 4 != 0 else "failure",
                takeaway=f"lesson_{i}" if i % 4 != 0 else "",
                error_message=f"error_{i}" if i % 4 == 0 else "",
            )

        # 2. Verify all recorded
        all_eps = await recorder.list_episodes("agent_pipe")
        assert len(all_eps) == 20

        # 3. Retrieve with relevance
        results = await retriever.query("agent_pipe", "error failure test")
        assert len(results) > 0

        # 4. Consolidate old episodes (use force for test — all are recent)
        consolidator_min = MemoryConsolidator(
            memory_repo, min_episodes_to_consolidate=1, max_episode_age_hours=0.0
        )
        result = await consolidator_min.consolidate("agent_pipe", force=True)
        assert result["consolidated"] >= 1

        # 5. Cross-task learning
        patterns = await learner.learn_patterns("agent_pipe")
        assert patterns["total_episodes"] == 20
        assert patterns["patterns_found"] > 0

        # 6. Get recommendations
        rec = await learner.recommend_for_task("agent_pipe", "refactor", "code improvement")
        assert isinstance(rec, dict)
        assert "warnings" in rec

        # 7. Improvement report
        report = await learner.generate_improvement_report("agent_pipe")
        assert report["total_episodes"] == 20
        assert "improvements_needed" in report
