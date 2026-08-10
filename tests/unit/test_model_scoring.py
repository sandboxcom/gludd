"""Unit tests for model_scoring module."""

from __future__ import annotations

import datetime
import os
from unittest.mock import MagicMock, patch

import pytest

from general_ludd.models.model_scoring import (
    BudgetProfile,
    ModelScore,
    _source_for,
    best_model,
    rank_models,
    score_model,
)


class TestModelScore:
    def test_create(self):
        ms = ModelScore(
            model_id="openai/gpt-4o",
            score=85.5,
            cost_estimate=0.05,
            latency_estimate=1200.0,
            source="cloud",
        )
        assert ms.model_id == "openai/gpt-4o"
        assert ms.score == 85.5
        assert ms.cost_estimate == 0.05
        assert ms.latency_estimate == 1200.0
        assert ms.source == "cloud"

    def test_source_local(self):
        ms = ModelScore(
            model_id="local/llama",
            score=70.0,
            cost_estimate=0.0,
            latency_estimate=500.0,
            source="local",
        )
        assert ms.source == "local"

    def test_invalid_source_raises(self):
        with pytest.raises(ValueError, match="invalid"):
            ModelScore(
                model_id="test",
                score=50.0,
                cost_estimate=0.0,
                latency_estimate=100.0,
                source="invalid",
            )

    def test_invalid_source_message(self):
        with pytest.raises(ValueError, match="local"):
            ModelScore(
                model_id="test",
                score=50.0,
                cost_estimate=0.0,
                latency_estimate=100.0,
                source="invalid",
            )

    def test_negative_cost_raises(self):
        with pytest.raises(ValueError, match="cost_estimate"):
            ModelScore(
                model_id="test",
                score=50.0,
                cost_estimate=-1.0,
                latency_estimate=100.0,
                source="cloud",
            )

    def test_negative_latency_raises(self):
        with pytest.raises(ValueError, match="latency_estimate"):
            ModelScore(
                model_id="test",
                score=50.0,
                cost_estimate=0.0,
                latency_estimate=-100.0,
                source="cloud",
            )


class TestBudgetProfile:
    def test_defaults(self):
        bp = BudgetProfile(max_cost_usd=0.01)
        assert bp.max_cost_usd == 0.01
        assert bp.prefer_local is False
        assert bp.max_latency_ms is None

    def test_prefer_local(self):
        bp = BudgetProfile(max_cost_usd=0.01, prefer_local=True)
        assert bp.prefer_local is True

    def test_max_latency(self):
        bp = BudgetProfile(max_cost_usd=0.01, max_latency_ms=500.0)
        assert bp.max_latency_ms == 500.0


class TestScoreModel:
    def test_scores_known_model(self):
        budget = BudgetProfile(max_cost_usd=0.10)
        result = score_model("openai/gpt-4o-mini", "bug_fix", budget)
        assert isinstance(result, ModelScore)
        assert result.model_id == "openai/gpt-4o-mini"
        assert result.source == "cloud"
        assert result.score > 0
        assert result.cost_estimate > 0
        assert result.latency_estimate > 0

    def test_scores_unknown_model_uses_defaults(self):
        budget = BudgetProfile(max_cost_usd=0.10)
        result = score_model("unknown/model", "bug_fix", budget)
        assert isinstance(result, ModelScore)
        assert result.model_id == "unknown/model"
        assert result.source == "cloud"
        assert result.score > 0

    def test_cheaper_model_scores_higher(self):
        budget = BudgetProfile(max_cost_usd=0.10)
        cheap = score_model("deepseek/deepseek-chat", "bug_fix", budget)
        expensive = score_model("anthropic/claude-4", "bug_fix", budget)
        assert cheap.score > expensive.score

    def test_score_reflects_formula_components(self):
        budget = BudgetProfile(max_cost_usd=0.10)
        result = score_model("openai/gpt-4o-mini", "bug_fix", budget)
        assert result.score > 50
        assert result.score < 200
        assert 0 < result.cost_estimate < 1.0
        assert 100 < result.latency_estimate < 5000

    def test_budget_exceeded_penalizes_score(self):
        tight_budget = BudgetProfile(max_cost_usd=0.001)
        generous_budget = BudgetProfile(max_cost_usd=0.10)
        result_tight = score_model("openai/gpt-4o", "bug_fix", tight_budget)
        result_generous = score_model("openai/gpt-4o", "bug_fix", generous_budget)
        assert result_tight.score < result_generous.score

    def test_prefer_local_adds_bonus(self):
        budget_remote = BudgetProfile(max_cost_usd=0.10, prefer_local=False)
        budget_local = BudgetProfile(max_cost_usd=0.10, prefer_local=True)
        result_remote = score_model("openai/gpt-4o-mini", "bug_fix", budget_remote)
        result_local = score_model("openai/gpt-4o-mini", "bug_fix", budget_local)
        assert result_local.score > result_remote.score

    def test_unknown_task_type_uses_defaults(self):
        budget = BudgetProfile(max_cost_usd=0.10)
        result = score_model("openai/gpt-4o", "nonexistent_task", budget)
        assert isinstance(result, ModelScore)
        assert result.score > 0

    def test_with_cost_router_integration(self):
        from general_ludd.models.cost_router import CostAwareRouter, PeakPricingSchedule

        perf_router = MagicMock()
        peak = PeakPricingSchedule(peak_start_hour=0, peak_end_hour=0)
        cr = CostAwareRouter(performance_router=perf_router, peak_schedule=peak)

        budget = BudgetProfile(max_cost_usd=0.10)
        now = datetime.datetime(2026, 1, 1, 12, 0, 0, tzinfo=datetime.UTC)
        result = score_model(
            "openai/gpt-4o-mini",
            "bug_fix",
            budget,
            cost_router=cr,
            now=now,
        )
        assert isinstance(result, ModelScore)
        assert result.cost_estimate > 0

    def test_local_source_when_local_available(self):
        with patch.dict(
            os.environ,
            {
                "GLUDD_ALLOW_LOCAL_MODEL_BASE_URLS": "1",
                "LOCAL_MODEL_BASE_URL": "http://localhost:11434/v1",
            },
        ):
            result = score_model(
                "local/llama",
                "bug_fix",
                BudgetProfile(max_cost_usd=0.10),
            )
            assert result.source == "local"

    def test_cloud_source_by_default(self):
        with patch.dict(os.environ, {}, clear=True):
            result = score_model(
                "openai/gpt-4o",
                "bug_fix",
                BudgetProfile(max_cost_usd=0.10),
            )
            assert result.source == "cloud"


class TestRankModels:
    def test_ranks_all_models_for_task(self):
        budget = BudgetProfile(max_cost_usd=0.10)
        ranked = rank_models("bug_fix", budget)
        assert len(ranked) > 0
        for i in range(len(ranked) - 1):
            assert ranked[i].score >= ranked[i + 1].score

    def test_empty_for_unknown_task(self):
        budget = BudgetProfile(max_cost_usd=0.10)
        ranked = rank_models("nonexistent_task", budget)
        assert ranked == []

    def test_highest_scoring_model_is_best(self):
        budget = BudgetProfile(max_cost_usd=0.10)
        ranked = rank_models("bug_fix", budget)
        best = best_model("bug_fix", budget)
        assert best is not None
        assert best.model_id == ranked[0].model_id
        assert best.score == ranked[0].score

    def test_best_returns_none_for_unknown_task(self):
        result = best_model("nonexistent_task", BudgetProfile(max_cost_usd=0.10))
        assert result is None


class TestSourceFor:
    def test_cloud_by_default(self):
        with patch.dict(os.environ, {}, clear=True):
            assert _source_for("any/model") == "cloud"

    def test_local_when_enabled(self):
        with patch.dict(
            os.environ,
            {
                "GLUDD_ALLOW_LOCAL_MODEL_BASE_URLS": "1",
                "LOCAL_MODEL_BASE_URL": "http://localhost:11434/v1",
            },
        ):
            assert _source_for("any/model") == "local"

    def test_cloud_when_disabled(self):
        with patch.dict(os.environ, {"GLUDD_ALLOW_LOCAL_MODEL_BASE_URLS": "0"}):
            assert _source_for("any/model") == "cloud"


class TestCostRouterIntegration:
    def test_peak_multiplier_affects_cost_estimate(self):
        from general_ludd.models.cost_router import CostAwareRouter, PeakPricingSchedule

        perf_router = MagicMock()
        peak_schedule = PeakPricingSchedule(
            peak_start_hour=0,
            peak_end_hour=23,
            peak_multiplier=2.0,
            off_peak_multiplier=0.5,
        )
        cr = CostAwareRouter(
            performance_router=perf_router,
            peak_schedule=peak_schedule,
        )

        budget = BudgetProfile(max_cost_usd=0.10)
        peak_now = datetime.datetime(2026, 1, 1, 12, 0, 0, tzinfo=datetime.UTC)
        off_now = datetime.datetime(2026, 1, 4, 3, 0, 0, tzinfo=datetime.UTC)

        peak_result = score_model(
            "openai/gpt-4o-mini",
            "bug_fix",
            budget,
            cost_router=cr,
            now=peak_now,
        )
        off_result = score_model(
            "openai/gpt-4o-mini",
            "bug_fix",
            budget,
            cost_router=cr,
            now=off_now,
        )

        assert peak_result.cost_estimate > off_result.cost_estimate


class TestHardwareIntegration:
    def test_gpu_hardware_reduces_latency(self):
        class _MockGPU:
            def gpu_count(self) -> int:
                return 1

            def vram_gb_per_gpu(self) -> float:
                return 8.0

            def system_ram_gb(self) -> float:
                return 32.0

        budget = BudgetProfile(max_cost_usd=0.10)
        with patch.dict(
            os.environ,
            {
                "GLUDD_ALLOW_LOCAL_MODEL_BASE_URLS": "1",
                "LOCAL_MODEL_BASE_URL": "http://localhost:11434/v1",
            },
        ):
            no_hw = score_model("deepseek/deepseek-chat", "bug_fix", budget)
            hw_result = score_model(
                "deepseek/deepseek-chat",
                "bug_fix",
                budget,
                hardware=_MockGPU(),
            )
            assert hw_result.latency_estimate < no_hw.latency_estimate

    def test_hardware_no_effect_without_local_models(self):
        class _MockGPU:
            def gpu_count(self) -> int:
                return 1

            def vram_gb_per_gpu(self) -> float:
                return 8.0

            def system_ram_gb(self) -> float:
                return 32.0

        budget = BudgetProfile(max_cost_usd=0.10)
        with patch.dict(os.environ, {}, clear=True):
            no_hw = score_model("deepseek/deepseek-chat", "bug_fix", budget)
            hw_result = score_model(
                "deepseek/deepseek-chat",
                "bug_fix",
                budget,
                hardware=_MockGPU(),
            )
            assert hw_result.latency_estimate == no_hw.latency_estimate
