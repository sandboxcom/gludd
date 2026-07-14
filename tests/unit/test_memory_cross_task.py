"""Structural tests for memory/cross_task.py — CrossTaskLearner."""

from __future__ import annotations

from general_ludd.memory.cross_task import CrossTaskLearner


class TestCrossTaskLearnerInit:
    def test_construction_with_repo(self):
        learner = CrossTaskLearner(memory_repo=object())
        assert learner._repo is not None
        assert learner._model_gateway is None

    def test_construction_with_gateway(self):
        gw = object()
        learner = CrossTaskLearner(memory_repo=object(), model_gateway=gw)
        assert learner._model_gateway is gw


class TestLearnPatternsEmpty:
    @staticmethod
    async def test_no_episodes_returns_empty():
        repo = _FakeMemoryRepo(episodes=[])
        learner = CrossTaskLearner(memory_repo=repo)
        result = await learner.learn_patterns("agent-1")
        assert result["patterns_found"] == 0
        assert result["total_episodes"] == 0
        assert "no episodes" in result["message"]


class TestRecommendForTask:
    @staticmethod
    async def test_returns_structure():
        repo = _FakeMemoryRepo(episodes=[], query_results=[])
        learner = CrossTaskLearner(memory_repo=repo)
        result = await learner.recommend_for_task("agent-1", "code_review")
        assert result["task_type"] == "code_review"
        assert "recommendations" in result
        assert "warnings" in result


class TestGenerateImprovementReport:
    @staticmethod
    async def test_no_episodes_shortcircuit():
        repo = _FakeMemoryRepo(episodes=[])
        learner = CrossTaskLearner(memory_repo=repo)
        result = await learner.generate_improvement_report("agent-1")
        assert result["total_episodes"] == 0


class _FakeMemoryRepo:
    def __init__(self, episodes=None, query_results=None, consolidated=None):
        self._episodes = episodes or []
        self._query_results = query_results or []
        self._consolidated = consolidated or []

    async def list_episodes(self, agent_id, *, project_id=None, limit=1000):
        return self._episodes

    async def query(self, agent_id, *, query_text="", task_type="", project_id=None, top_k=5):
        return self._query_results

    async def get_consolidated(self, agent_id, *, task_type=None, project_id=None):
        return self._consolidated

    async def list_by_namespace(self, agent_id, *, namespace="", project_id=None, limit=100):
        return []

    async def set(self, *, agent_id, key, value, namespace="", project_id=None):
        pass
