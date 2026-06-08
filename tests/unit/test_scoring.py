"""Tests for benchmark schemas, scoring engine, and adaptive router."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from general_ludd.schemas.benchmark import (
    BenchmarkResult,
    BenchmarkScores,
    PromptProfile,
    RoutingCandidate,
    RoutingDecision,
    TaskType,
)
from general_ludd.scoring.engine import (
    DEFAULT_BENCHMARK_TASKS,
    BenchmarkTask,
    PromptScoringEngine,
)
from general_ludd.scoring.router import AdaptiveRouter


class TestTaskType:
    def test_all_task_types(self):
        expected = {
            "bug_fix", "feature", "refactor", "test_write",
            "code_review", "documentation", "debugging",
            "optimization", "security_fix", "integration",
        }
        assert {t.value for t in TaskType} == expected


class TestBenchmarkScores:
    def test_composite_score_weights(self):
        scores = BenchmarkScores(
            completion_score=1.0,
            code_quality_score=1.0,
            instruction_adherence_score=1.0,
            token_efficiency_score=1.0,
        )
        assert scores.composite_score == 1.0

    def test_composite_score_partial(self):
        scores = BenchmarkScores(
            completion_score=0.0,
            code_quality_score=0.0,
            instruction_adherence_score=0.0,
            token_efficiency_score=0.0,
        )
        assert scores.composite_score == 0.0

    def test_composite_score_mixed(self):
        scores = BenchmarkScores(
            completion_score=1.0,
            code_quality_score=0.0,
            instruction_adherence_score=0.0,
            token_efficiency_score=0.0,
        )
        assert abs(scores.composite_score - 0.35) < 0.001

    def test_composite_score_mixed_2(self):
        scores = BenchmarkScores(
            completion_score=0.8,
            code_quality_score=0.9,
            instruction_adherence_score=0.7,
            token_efficiency_score=0.6,
        )
        expected = 0.8 * 0.35 + 0.9 * 0.25 + 0.7 * 0.25 + 0.6 * 0.15
        assert abs(scores.composite_score - expected) < 0.001


class TestPromptProfile:
    def test_defaults(self):
        p = PromptProfile(
            id="pp-1",
            name="test",
            source="aider",
            prompt_text="Hello",
        )
        assert p.source_url == ""
        assert p.task_types == []
        assert p.tags == []
        assert p.version == "latest"


class TestBenchmarkResult:
    def test_defaults(self):
        r = BenchmarkResult(
            model_profile_id="openai",
            task_type=TaskType.BUG_FIX,
            scores=BenchmarkScores(
                completion_score=0.8,
                code_quality_score=0.7,
                instruction_adherence_score=0.9,
                token_efficiency_score=0.6,
            ),
        )
        assert r.success is False
        assert r.input_tokens == 0
        assert r.id is None


class TestRoutingDecision:
    def test_defaults(self):
        d = RoutingDecision(
            selected_prompt_profile_id=None,
            selected_model_profile_id="openai",
            composite_score=0.8,
            estimated_cost_usd=0.01,
            sample_count=10,
        )
        assert d.fallback is False
        assert d.selected_prompt_profile_id is None


class TestRoutingCandidate:
    def test_fields(self):
        c = RoutingCandidate(
            prompt_profile_id="pp-1",
            model_profile_id="openai",
            composite_score=0.9,
            avg_cost_usd=0.02,
            sample_count=5,
            task_type=TaskType.FEATURE,
        )
        assert c.composite_score == 0.9
        assert c.task_type == TaskType.FEATURE


class TestBenchmarkTask:
    def test_default_task_list(self):
        assert len(DEFAULT_BENCHMARK_TASKS) == 10

    def test_each_task_type_covered(self):
        types = {t.task_type for t in DEFAULT_BENCHMARK_TASKS}
        assert types == set(TaskType)

    def test_task_fields(self):
        t = DEFAULT_BENCHMARK_TASKS[0]
        assert t.task_type == TaskType.BUG_FIX
        assert t.description
        assert t.prompt_instruction
        assert t.expected_patterns
        assert t.max_tokens > 0


class TestPromptScoringEngine:
    def setup_method(self):
        self.engine = PromptScoringEngine()

    def test_score_empty_output(self):
        task = BenchmarkTask(
            task_type=TaskType.BUG_FIX,
            description="test",
            prompt_instruction="Fix the bug. Return only the corrected function.",
            expected_patterns=[r"def \w+", r"return"],
        )
        scores = self.engine.score_output("", task)
        assert scores.completion_score == 0.0

    def test_score_perfect_output(self):
        task = BenchmarkTask(
            task_type=TaskType.BUG_FIX,
            description="fix off-by-one",
            prompt_instruction="Fix the bug. Return only the corrected function.",
            expected_patterns=[r"def get_last_n", r"items\[-n:\]"],
            forbidden_patterns=[r"items\[:-n\]"],
        )
        output = 'def get_last_n(items, n):\n    return items[-n:]'
        scores = self.engine.score_output(output, task)
        assert scores.completion_score == 1.0
        assert scores.code_quality_score > 0.3

    def test_score_forbidden_pattern_penalizes(self):
        task = BenchmarkTask(
            task_type=TaskType.BUG_FIX,
            description="test",
            prompt_instruction="Fix the bug. Return only the corrected function.",
            expected_patterns=[r"def \w+"],
            forbidden_patterns=[r"items\[:-n\]"],
        )
        good = 'def get_last_n(items, n):\n    return items[-n:]'
        bad = 'def get_last_n(items, n):\n    return items[:-n]'
        good_scores = self.engine.score_output(good, task)
        bad_scores = self.engine.score_output(bad, task)
        assert good_scores.instruction_adherence_score > bad_scores.instruction_adherence_score

    def test_score_code_quality_with_docstring(self):
        output = 'def foo():\n    """Docstring."""\n    pass'
        task = BenchmarkTask(
            task_type=TaskType.FEATURE,
            description="test",
            prompt_instruction="Write a function",
        )
        scores = self.engine.score_output(output, task)
        assert scores.code_quality_score > 0.5

    def test_score_code_quality_with_try_except(self):
        output = 'def foo():\n    try:\n        pass\n    except Exception:\n        pass'
        task = BenchmarkTask(
            task_type=TaskType.FEATURE,
            description="test",
            prompt_instruction="Write a function",
        )
        scores = self.engine.score_output(output, task)
        assert scores.code_quality_score > 0.5

    def test_token_efficiency_short_lines(self):
        output = "\n".join(["x = 1"] * 10)
        task = BenchmarkTask(
            task_type=TaskType.FEATURE,
            description="test",
            prompt_instruction="Write code",
        )
        scores = self.engine.score_output(output, task)
        assert scores.token_efficiency_score >= 0.8

    def test_token_efficiency_long_lines(self):
        output = "x = " + "a" * 200
        task = BenchmarkTask(
            task_type=TaskType.FEATURE,
            description="test",
            prompt_instruction="Write code",
        )
        scores = self.engine.score_output(output, task)
        assert scores.token_efficiency_score < 0.7

    def test_get_tasks(self):
        tasks = self.engine.get_tasks()
        assert len(tasks) == 10

    def test_get_tasks_for_type(self):
        tasks = self.engine.get_tasks_for_type(TaskType.BUG_FIX)
        assert len(tasks) >= 1
        assert all(t.task_type == TaskType.BUG_FIX for t in tasks)

    def test_all_default_tasks_have_patterns(self):
        for task in DEFAULT_BENCHMARK_TASKS:
            assert task.expected_patterns, f"{task.task_type} has no expected_patterns"


class TestAdaptiveRouter:
    @pytest.mark.asyncio
    async def test_route_no_repo_falls_back(self):
        router = AdaptiveRouter(benchmark_repo=None)
        decision = await router.route(
            task_type=TaskType.BUG_FIX,
            default_prompt_profile="default",
            default_model_profile="openai",
        )
        assert decision.fallback is True
        assert decision.selected_model_profile_id == "openai"
        assert decision.selected_prompt_profile_id == "default"
        assert "insufficient" in decision.reason

    @pytest.mark.asyncio
    async def test_route_insufficient_data_falls_back(self):
        repo = AsyncMock()
        repo.get_aggregate_scores = AsyncMock(return_value=[])
        router = AdaptiveRouter(benchmark_repo=repo)
        decision = await router.route(
            task_type=TaskType.BUG_FIX,
            default_model_profile="openai",
        )
        assert decision.fallback is True

    @pytest.mark.asyncio
    async def test_route_selects_best(self):
        repo = AsyncMock()
        repo.get_aggregate_scores = AsyncMock(
            return_value=[
                {
                    "prompt_profile_id": "pp-aider",
                    "model_profile_id": "openai",
                    "task_type": "bug_fix",
                    "sample_count": 10,
                    "avg_completion": 0.9,
                    "avg_code_quality": 0.8,
                    "avg_instruction": 0.85,
                    "avg_token_efficiency": 0.7,
                    "avg_cost": 0.01,
                    "composite_score": 0.83,
                },
                {
                    "prompt_profile_id": "pp-swe",
                    "model_profile_id": "anthropic",
                    "task_type": "bug_fix",
                    "sample_count": 8,
                    "avg_completion": 0.7,
                    "avg_code_quality": 0.6,
                    "avg_instruction": 0.65,
                    "avg_token_efficiency": 0.5,
                    "avg_cost": 0.005,
                    "composite_score": 0.63,
                },
            ]
        )
        router = AdaptiveRouter(benchmark_repo=repo, min_samples=3)
        decision = await router.route(task_type=TaskType.BUG_FIX)
        assert decision.fallback is False
        assert decision.selected_prompt_profile_id == "pp-aider"
        assert decision.composite_score == 0.83

    @pytest.mark.asyncio
    async def test_route_min_samples_filters(self):
        repo = AsyncMock()
        repo.get_aggregate_scores = AsyncMock(
            return_value=[
                {
                    "prompt_profile_id": "pp-1",
                    "model_profile_id": "openai",
                    "task_type": "bug_fix",
                    "sample_count": 2,
                    "avg_completion": 0.9,
                    "avg_code_quality": 0.9,
                    "avg_instruction": 0.9,
                    "avg_token_efficiency": 0.9,
                    "avg_cost": 0.01,
                    "composite_score": 0.9,
                },
            ]
        )
        router = AdaptiveRouter(benchmark_repo=repo, min_samples=3)
        decision = await router.route(
            task_type=TaskType.BUG_FIX, default_model_profile="default"
        )
        assert decision.fallback is True

    @pytest.mark.asyncio
    async def test_route_cost_constraint(self):
        repo = AsyncMock()
        repo.get_aggregate_scores = AsyncMock(
            side_effect=lambda task_type=None: [
                {
                    "prompt_profile_id": "pp-expensive",
                    "model_profile_id": "gpt4",
                    "task_type": "bug_fix",
                    "sample_count": 10,
                    "avg_completion": 0.9,
                    "avg_code_quality": 0.9,
                    "avg_instruction": 0.9,
                    "avg_token_efficiency": 0.9,
                    "avg_cost": 0.10,
                    "composite_score": 0.9,
                },
                {
                    "prompt_profile_id": "pp-cheap",
                    "model_profile_id": "local",
                    "task_type": "bug_fix",
                    "sample_count": 5,
                    "avg_completion": 0.7,
                    "avg_code_quality": 0.7,
                    "avg_instruction": 0.7,
                    "avg_token_efficiency": 0.7,
                    "avg_cost": 0.001,
                    "composite_score": 0.7,
                },
            ]
        )
        router = AdaptiveRouter(benchmark_repo=repo, min_samples=3)
        decision = await router.route(
            task_type=TaskType.BUG_FIX, max_cost_usd=0.01
        )
        assert decision.selected_prompt_profile_id == "pp-cheap"
        assert decision.reason == "cost_constrained"

    @pytest.mark.asyncio
    async def test_leaderboard(self):
        repo = AsyncMock()
        repo.get_aggregate_scores = AsyncMock(
            return_value=[
                {
                    "prompt_profile_id": "pp-b",
                    "model_profile_id": "model-b",
                    "task_type": "feature",
                    "sample_count": 5,
                    "avg_completion": 0.8,
                    "avg_code_quality": 0.8,
                    "avg_instruction": 0.8,
                    "avg_token_efficiency": 0.8,
                    "avg_cost": 0.02,
                    "composite_score": 0.8,
                },
                {
                    "prompt_profile_id": "pp-a",
                    "model_profile_id": "model-a",
                    "task_type": "feature",
                    "sample_count": 5,
                    "avg_completion": 0.9,
                    "avg_code_quality": 0.9,
                    "avg_instruction": 0.9,
                    "avg_token_efficiency": 0.9,
                    "avg_cost": 0.03,
                    "composite_score": 0.9,
                },
            ]
        )
        router = AdaptiveRouter(benchmark_repo=repo)
        lb = await router.get_leaderboard(task_type=TaskType.FEATURE)
        assert len(lb) == 2
        assert lb[0].composite_score >= lb[1].composite_score

    @pytest.mark.asyncio
    async def test_leaderboard_empty(self):
        repo = AsyncMock()
        repo.get_aggregate_scores = AsyncMock(return_value=[])
        router = AdaptiveRouter(benchmark_repo=repo)
        lb = await router.get_leaderboard()
        assert lb == []

    def test_invalidate_cache(self):
        router = AdaptiveRouter()
        router._cache["test"] = RoutingDecision(
            selected_prompt_profile_id=None,
            selected_model_profile_id="test",
            composite_score=0.5,
            estimated_cost_usd=0.0,
            sample_count=1,
        )
        router._cache_time = None
        router.invalidate_cache()

    @pytest.mark.asyncio
    async def test_route_with_quantization_penalty(self):
        repo = AsyncMock()
        repo.get_aggregate_scores = AsyncMock(
            return_value=[
                {
                    "prompt_profile_id": "pp-a",
                    "model_profile_id": "quantized-model",
                    "task_type": "bug_fix",
                    "sample_count": 10,
                    "avg_completion": 0.9,
                    "avg_code_quality": 0.9,
                    "avg_instruction": 0.9,
                    "avg_token_efficiency": 0.9,
                    "avg_cost": 0.01,
                    "composite_score": 0.9,
                },
                {
                    "prompt_profile_id": "pp-b",
                    "model_profile_id": "full-precision-model",
                    "task_type": "bug_fix",
                    "sample_count": 10,
                    "avg_completion": 0.85,
                    "avg_code_quality": 0.85,
                    "avg_instruction": 0.85,
                    "avg_token_efficiency": 0.85,
                    "avg_cost": 0.02,
                    "composite_score": 0.85,
                },
            ]
        )
        quant_map = {
            "quantized-model": ("int4", 0.3),
            "full-precision-model": ("bf16", 0.95),
        }
        router = AdaptiveRouter(
            benchmark_repo=repo,
            min_samples=3,
            quantization_map=quant_map,
        )
        decision = await router.route(task_type=TaskType.BUG_FIX)
        assert decision.selected_model_profile_id == "full-precision-model"

    @pytest.mark.asyncio
    async def test_route_quantization_no_penalty_for_high_precision(self):
        repo = AsyncMock()
        repo.get_aggregate_scores = AsyncMock(
            return_value=[
                {
                    "prompt_profile_id": "pp-a",
                    "model_profile_id": "bf16-model",
                    "task_type": "bug_fix",
                    "sample_count": 10,
                    "avg_cost": 0.01,
                    "composite_score": 0.9,
                },
            ]
        )
        quant_map = {"bf16-model": ("bf16", 0.95)}
        router = AdaptiveRouter(
            benchmark_repo=repo,
            min_samples=3,
            quantization_map=quant_map,
        )
        decision = await router.route(task_type=TaskType.BUG_FIX)
        assert decision.selected_model_profile_id == "bf16-model"
        assert decision.composite_score == 0.9
        assert len(router._cache) == 0
