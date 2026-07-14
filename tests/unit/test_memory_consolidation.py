"""Structural tests for memory/consolidation.py — MemoryConsolidator."""

from __future__ import annotations

from general_ludd.memory.consolidation import (
    CONSOLIDATED_NAMESPACE,
    CONSOLIDATION_KEY_PREFIX,
    MemoryConsolidator,
    _safe_key,
)


class TestSafeKey:
    def test_alphanumeric_preserved(self):
        assert _safe_key("hello_world-123") == "hello_world-123"

    def test_spaces_replaced(self):
        assert _safe_key("hello world") == "hello_world"

    def test_special_chars_replaced(self):
        result = _safe_key("hello@world!")
        assert "@" not in result
        assert "!" not in result

    def test_lowercases(self):
        assert _safe_key("HelloWorld") == "helloworld"

    def test_truncates_to_64_chars(self):
        long_name = "a" * 100
        assert len(_safe_key(long_name)) == 64


class TestConstants:
    def test_namespace(self):
        assert CONSOLIDATED_NAMESPACE == "consolidated"

    def test_key_prefix(self):
        assert CONSOLIDATION_KEY_PREFIX == "summary_"


class TestMemoryConsolidatorInit:
    def test_default_construction(self):
        mc = MemoryConsolidator(memory_repo=object())
        assert mc._repo is not None
        assert mc._model_gateway is None
        assert mc._min_episodes == 10
        assert mc._max_age_hours == 24.0

    def test_custom_params(self):
        repo = object()
        mc = MemoryConsolidator(memory_repo=repo, min_episodes_to_consolidate=5, max_episode_age_hours=12.0)
        assert mc._min_episodes == 5
        assert mc._max_age_hours == 12.0

    def test_with_gateway(self):
        gw = object()
        mc = MemoryConsolidator(memory_repo=object(), model_gateway=gw)
        assert mc._model_gateway is gw


class TestConsolidateInsufficient:
    @staticmethod
    async def test_too_few_episodes():
        repo = _FakeRepo(episodes=[])
        mc = MemoryConsolidator(memory_repo=repo, min_episodes_to_consolidate=10)
        result = await mc.consolidate("agent-1")
        assert result["consolidated"] == 0
        assert "insufficient" in result["reason"]

    @staticmethod
    async def test_force_bypasses_minimum():
        repo = _FakeRepo(episodes=[])
        mc = MemoryConsolidator(memory_repo=repo, min_episodes_to_consolidate=10)
        result = await mc.consolidate("agent-1", force=True)
        assert result["consolidated"] == 0
        assert "consolidated" in result


class TestSummarizeGroup:
    def test_empty_episodes(self):
        mc = MemoryConsolidator(memory_repo=object())
        summary = mc._summarize_group("test_type", [])
        assert summary["task_type"] == "test_type"
        assert summary["episode_count"] == 0
        assert summary["avg_duration_seconds"] == 0

    def test_with_successes(self):
        ep = _FakeEpisode(outcome="success", takeaway="use smaller batches", error_message="")
        mc = MemoryConsolidator(memory_repo=object())
        summary = mc._summarize_group("code", [ep])
        assert "use smaller batches" in summary["key_takeaways"]
        assert summary["episode_count"] == 1

    def test_with_failures(self):
        ep = _FakeEpisode(outcome="failure", takeaway="", error_message="disk full")
        mc = MemoryConsolidator(memory_repo=object())
        summary = mc._summarize_group("code", [ep])
        assert "disk full" in summary["error_patterns"]


class TestGetConsolidated:
    @staticmethod
    async def test_empty():
        repo = _FakeRepo(episodes=[], stored={})
        mc = MemoryConsolidator(memory_repo=repo)
        result = await mc.get_consolidated("agent-1")
        assert result == []


class _FakeRepo:
    def __init__(self, episodes=None, stored=None):
        self._episodes = episodes or []
        self._stored = stored or {}

    async def list_episodes(self, agent_id, *, project_id=None, limit=1000):
        return self._episodes

    async def set(self, *, agent_id, key, value, namespace="", project_id=None):
        self._stored[(agent_id, namespace, key)] = value

    async def list_by_namespace(self, agent_id, *, namespace="", project_id=None, limit=100):
        results = []
        for (aid, ns, _key), val in self._stored.items():
            if aid == agent_id and ns == namespace:
                results.append(_FakeRow(value=val))
        return results


class _FakeEpisode:
    def __init__(self, *, outcome="success", error_message="", takeaway="", priority="medium",
                 duration_seconds=5.0, task_type="code", created_at=None):
        self.outcome = outcome
        self.error_message = error_message
        self.takeaway = takeaway
        self.priority = priority
        self.duration_seconds = duration_seconds
        self.task_type = task_type
        self.created_at = created_at or "2024-01-01T00:00:00+00:00"
        self.error_message = error_message  # needed for Counter


class _FakeRow:
    def __init__(self, *, value=""):
        self.value = value
