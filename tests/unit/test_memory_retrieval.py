"""Behavioral tests for memory/retrieval.py — hybrid_search, score_memory, MemoryRetriever."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from general_ludd.memory.retrieval import (
    MemoryRetriever,
    ScoredMemory,
    _any_overlap,
    _tokenize,
    hybrid_search,
    score_memory,
)


class TestTokenize:
    def test_removes_stop_words(self):
        result = _tokenize("the quick brown fox is jumping")
        assert "the" not in result
        assert "is" not in result

    def test_lowercases(self):
        result = _tokenize("Hello World")
        assert "hello" in result
        assert "world" in result

    def test_strips_short_words(self):
        result = _tokenize("a b c d hi")
        assert all(len(w) > 1 for w in result)

    def test_removes_punctuation(self):
        result = _tokenize("hello, world! how's it?")
        for w in result:
            assert w.isalnum() or "_" in w

    def test_empty_input(self):
        assert _tokenize("") == []

    def test_only_stop_words(self):
        assert _tokenize("the is a an and but") == []


class TestAnyOverlap:
    def test_overlap_exists(self):
        assert _any_overlap(["foo", "bar"], ["bar", "baz"]) is True

    def test_no_overlap(self):
        assert _any_overlap(["foo", "bar"], ["baz", "qux"]) is False

    def test_empty_lists(self):
        assert _any_overlap([], ["foo"]) is False
        assert _any_overlap(["foo"], []) is False
        assert _any_overlap([], []) is False

    def test_identical_lists(self):
        assert _any_overlap(["a", "b"], ["a", "b"]) is True


class TestScoredMemory:
    def test_construction(self):
        sm = ScoredMemory(episode="ep1", score=0.85, match_reasons=["term_overlap"])
        assert sm.episode == "ep1"
        assert sm.score == 0.85
        assert sm.match_reasons == ["term_overlap"]

    def test_default_match_reasons(self):
        sm = ScoredMemory(episode=None, score=0.0)
        assert sm.match_reasons == []


class TestScoreMemory:
    def test_fresh_memory_scores_high(self):
        mem = {
            "created_at": datetime.now(UTC).isoformat(),
            "importance_weight": 1.0,
            "access_count": 0,
        }
        result = score_memory(mem)
        assert result > 0.5

    def test_old_memory_scores_low(self):
        old = (datetime.now(UTC) - timedelta(days=365)).isoformat()
        mem = {
            "created_at": old,
            "importance_weight": 1.0,
            "access_count": 0,
        }
        result = score_memory(mem)
        assert result < 0.01

    def test_importance_weight_affects_score(self):
        high_imp = {
            "created_at": datetime.now(UTC).isoformat(),
            "importance_weight": 2.0,
            "access_count": 0,
        }
        low_imp = {
            "created_at": datetime.now(UTC).isoformat(),
            "importance_weight": 0.5,
            "access_count": 0,
        }
        assert score_memory(high_imp) > score_memory(low_imp)

    def test_access_count_boosts_score(self):
        base = {
            "created_at": datetime.now(UTC).isoformat(),
            "importance_weight": 1.0,
            "access_count": 0,
        }
        accessed = {
            "created_at": datetime.now(UTC).isoformat(),
            "importance_weight": 1.0,
            "access_count": 10,
        }
        assert score_memory(accessed) > score_memory(base)

    def test_access_bonus_capped_at_2x(self):
        mem = {
            "created_at": datetime.now(UTC).isoformat(),
            "importance_weight": 1.0,
            "access_count": 100,
        }
        result = score_memory(mem)
        assert result <= 2.0

    def test_no_created_at_uses_0_age(self):
        mem = {
            "importance_weight": 1.0,
            "access_count": 0,
        }
        result = score_memory(mem)
        assert result == 1.0

    def test_unparseable_created_at_handled(self):
        mem = {
            "created_at": "not-a-date",
            "importance_weight": 1.0,
            "access_count": 0,
        }
        result = score_memory(mem)
        assert result == 1.0

    def test_default_importance_weight_is_1(self):
        mem = {
            "created_at": datetime.now(UTC).isoformat(),
            "access_count": 0,
        }
        result = score_memory(mem)
        assert 0.5 < result <= 1.0

    def test_explicit_current_time(self):
        past = "2024-01-01T00:00:00+00:00"
        now = datetime(2024, 1, 2, tzinfo=UTC)
        mem = {
            "created_at": past,
            "importance_weight": 1.0,
            "access_count": 0,
        }
        result = score_memory(mem, current_time=now)
        expected = 1.0 / (1.0 + 1.0)
        assert abs(result - expected) < 0.01

    def test_weight_0_always_0(self):
        mem = {
            "created_at": datetime.now(UTC).isoformat(),
            "importance_weight": 0.0,
            "access_count": 0,
        }
        assert score_memory(mem) == 0.0


class TestHybridSearch:
    def test_empty_memories(self):
        result = hybrid_search("query", [])
        assert result == []

    def test_empty_query(self):
        mems = [{"task": "write code"}, {"task": "fix bug"}]
        result = hybrid_search("", mems, top_k=2)
        assert len(result) == 2

    def test_returns_top_k(self):
        mems = [{"task": f"task_{i}"} for i in range(20)]
        result = hybrid_search("task", mems, top_k=5)
        assert len(result) == 5

    def test_scores_are_floats(self):
        mems = [
            {"task": "write python code", "outcome": "success"},
            {"task": "fix deployment bug", "outcome": "failure"},
        ]
        result = hybrid_search("python code", mems)
        for _, score in result:
            assert isinstance(score, float)
            assert 0.0 <= score <= 1.0

    def test_exact_match_scores_higher(self):
        mems = [
            {"task": "write python code", "title": "python"},
            {"task": "deploy infrastructure", "title": "deploy"},
        ]
        result = hybrid_search("python", mems)
        assert result[0][0]["title"] == "python"

    def test_sorted_by_score_descending(self):
        mems = [
            {"task": "python coding"},
            {"task": "deploy server"},
            {"task": "python unit tests"},
            {"task": "fix config"},
        ]
        result = hybrid_search("python", mems)
        scores = [s for _, s in result]
        assert scores == sorted(scores, reverse=True)

    def test_bm25_only_weight(self):
        mems = [
            {"task": "python coding"},
            {"task": "deploy server"},
        ]
        result_bm25 = hybrid_search("python", mems, bm25_weight=1.0, semantic_weight=0.0)
        result_semantic = hybrid_search("python", mems, bm25_weight=0.0, semantic_weight=1.0)
        assert len(result_bm25) == 2
        assert len(result_semantic) == 2

    def test_semantic_only_weight(self):
        mems = [
            {"task": "python coding"},
            {"task": "deploy server"},
        ]
        result = hybrid_search("python", mems, bm25_weight=0.0, semantic_weight=1.0)
        assert result[0][0]["task"] == "python coding"

    def test_top_k_larger_than_memories(self):
        mems = [{"task": "a"}, {"task": "b"}, {"task": "c"}]
        result = hybrid_search("a", mems, top_k=10)
        assert len(result) == 3

    def test_handles_non_string_values(self):
        mems = [
            {"count": 42, "active": True, "name": "alpha"},
            {"count": 7, "active": False, "name": "beta"},
        ]
        result = hybrid_search("alpha", mems)
        assert len(result) == 2

    def test_bm25_uses_term_frequency(self):
        mems = [
            {"id": "repeated", "text": "python python python"},
            {"id": "single", "text": "python"},
        ]
        result = hybrid_search("python", mems, bm25_weight=1.0, semantic_weight=0.0)
        assert [row[0]["id"] for row in result] == ["repeated", "single"]

    def test_rejects_negative_weights(self):
        with pytest.raises(ValueError, match="weights"):
            hybrid_search("python", [{"text": "python"}], bm25_weight=-0.1)


class TestMemoryRetrieverQuery:
    def make_repo(self, episodes):
        return _FakeRetrievalRepo(episodes)

    @pytest.mark.asyncio
    async def test_query_returns_scored_results(self):
        eps = [_make_retrieval_ep_dict(task_type="code", takeaway="use caching")]
        repo = self.make_repo(eps)
        retriever = MemoryRetriever(repo)
        results = await retriever.query("agent-1", "caching")
        assert len(results) >= 1
        assert isinstance(results[0], ScoredMemory)

    @pytest.mark.asyncio
    async def test_query_empty_returns_empty(self):
        repo = self.make_repo([])
        retriever = MemoryRetriever(repo)
        results = await retriever.query("agent-1", "nothing")
        assert results == []

    @pytest.mark.asyncio
    async def test_query_filters_by_task_type(self):
        eps = [
            _make_retrieval_ep_dict(task_type="code", takeaway="good"),
            _make_retrieval_ep_dict(task_type="deploy", takeaway="good"),
        ]
        repo = self.make_repo(eps)
        retriever = MemoryRetriever(repo)
        results = await retriever.query("agent-1", "good", task_type="code")
        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_min_score_filters_results(self):
        eps = [_make_retrieval_ep_dict(task_type="code", takeaway="alpha")]
        repo = self.make_repo(eps)
        retriever = MemoryRetriever(repo)
        results = await retriever.query("agent-1", "alpha", min_score=0.99)
        assert len(results) <= 1

    @pytest.mark.asyncio
    async def test_top_k_limits_results(self):
        eps = [_make_retrieval_ep_dict(task_type="code") for _ in range(5)]
        repo = self.make_repo(eps)
        retriever = MemoryRetriever(repo)
        results = await retriever.query("agent-1", "code", top_k=2)
        assert len(results) == 2

    @pytest.mark.asyncio
    async def test_takeaway_match_boosts_score(self):
        eps = [
            _make_retrieval_ep_dict(task_type="code", takeaway="use pytest fixtures"),
            _make_retrieval_ep_dict(task_type="code", takeaway="something unrelated"),
        ]
        repo = self.make_repo(eps)
        retriever = MemoryRetriever(repo)
        results = await retriever.query("agent-1", "pytest fixtures")
        assert results[0].episode.takeaway == "use pytest fixtures"

    @pytest.mark.asyncio
    async def test_outcome_match_failure(self):
        eps = [
            _make_retrieval_ep_dict(task_type="code", outcome="failure", error_message="disk full"),
            _make_retrieval_ep_dict(task_type="code", outcome="success", error_message=""),
        ]
        repo = self.make_repo(eps)
        retriever = MemoryRetriever(repo)
        results = await retriever.query("agent-1", "fail")
        assert "failure_query_match" in results[0].match_reasons

    @pytest.mark.asyncio
    async def test_outcome_match_success(self):
        eps = [
            _make_retrieval_ep_dict(task_type="code", outcome="success"),
            _make_retrieval_ep_dict(task_type="code", outcome="failure"),
        ]
        repo = self.make_repo(eps)
        retriever = MemoryRetriever(repo)
        results = await retriever.query("agent-1", "success")
        assert "success_query_match" in results[0].match_reasons

    @pytest.mark.asyncio
    async def test_error_pattern_match(self):
        eps = [
            _make_retrieval_ep_dict(task_type="code", outcome="failure", error_message="disk full error on write"),
            _make_retrieval_ep_dict(task_type="code", outcome="success"),
        ]
        repo = self.make_repo(eps)
        retriever = MemoryRetriever(repo)
        results = await retriever.query("agent-1", "disk full")
        assert "error_pattern_match" in results[0].match_reasons

    @pytest.mark.asyncio
    async def test_lesson_match(self):
        eps = [
            _make_retrieval_ep_dict(task_type="code", takeaway="always check disk space first"),
            _make_retrieval_ep_dict(task_type="code", takeaway="something else"),
        ]
        repo = self.make_repo(eps)
        retriever = MemoryRetriever(repo)
        results = await retriever.query("agent-1", "disk space")
        assert "lesson_match" in results[0].match_reasons

    @pytest.mark.asyncio
    async def test_recency_boost(self):
        eps = [
            _make_retrieval_ep_dict(task_type="code", takeaway="same takeaway", created_at=datetime.now(UTC).isoformat()),
            _make_retrieval_ep_dict(
                task_type="code",
                takeaway="same takeaway",
                created_at=(datetime.now(UTC) - timedelta(days=30)).isoformat(),
            ),
        ]
        repo = self.make_repo(eps)
        retriever = MemoryRetriever(repo)
        results = await retriever.query("agent-1", "takeaway")
        assert results[0].score > results[1].score

    @pytest.mark.asyncio
    async def test_handles_missing_created_at(self):
        eps = [
            _make_retrieval_ep_dict(task_type="code", created_at=""),
            _make_retrieval_ep_dict(task_type="code", created_at=datetime.now(UTC).isoformat()),
        ]
        repo = self.make_repo(eps)
        retriever = MemoryRetriever(repo)
        results = await retriever.query("agent-1", "code", top_k=2)
        assert len(results) == 2


# --- Helpers ---

class _FakeRetrievalRow:
    def __init__(self, *, value: str = ""):
        self.value = value


class _FakeRetrievalEpisode:
    def __init__(
        self,
        *,
        task_type: str = "code",
        work_type: str = "code",
        priority: str = "medium",
        outcome: str = "success",
        context: dict | None = None,
        takeaway: str = "",
        error_message: str = "",
        duration_seconds: float = 5.0,
        created_at: str = "",
    ):
        self.task_type = task_type
        self.work_type = work_type
        self.priority = priority
        self.outcome = outcome
        self.context = context or {}
        self.takeaway = takeaway
        self.error_message = error_message
        self.duration_seconds = duration_seconds
        self.created_at = created_at or datetime.now(UTC).isoformat()


class _FakeRetrievalRepo:
    def __init__(self, episodes: list[dict[str, Any]] | None = None):
        self._stored: dict[tuple[str, str, str], str] = {}
        for ep_dict in (episodes or []):
            ep_key = ep_dict.get("id", f"ep-{hash(json.dumps(ep_dict, sort_keys=True, default=str))}")
            self._stored[("agent-1", "episodic", ep_key)] = json.dumps(ep_dict, default=str)

    async def list_by_namespace(self, agent_id, *, namespace="", project_id=None, limit=100):
        results = []
        for (aid, ns, _key), val in self._stored.items():
            if aid == agent_id and ns == namespace:
                results.append(_FakeRetrievalRow(value=val))
        return results[:limit]


def _make_retrieval_ep_dict(
    *,
    task_type: str = "code",
    work_type: str = "code",
    priority: str = "medium",
    outcome: str = "success",
    takeaway: str = "",
    error_message: str = "",
    duration_seconds: float = 5.0,
    created_at: str = "",
    context: dict | None = None,
) -> dict[str, Any]:
    _make_retrieval_ep_dict._counter += 1
    return {
        "id": f"ep-{_make_retrieval_ep_dict._counter:08d}",
        "agent_id": "agent-1",
        "task_type": task_type,
        "work_type": work_type,
        "priority": priority,
        "outcome": outcome,
        "context": context or {},
        "tools_used": [],
        "takeaway": takeaway,
        "error_message": error_message,
        "duration_seconds": duration_seconds,
        "session_id": "",
        "created_at": created_at or datetime.now(UTC).isoformat(),
    }


_make_retrieval_ep_dict._counter = 0
