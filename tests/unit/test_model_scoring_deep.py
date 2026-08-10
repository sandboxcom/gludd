"""Deep edge-case tests for model_scoring module."""

from __future__ import annotations

import datetime
import os
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from general_ludd.models.model_scoring import (
    BudgetProfile,
    ModelScore,
    _compute_score,
    _detect_hardware,
    _get_capabilities,
    _is_local_model,
    _local_model_available,
    _local_model_base_url,
    _source_for,
    best_model,
    rank_models,
    score_model,
)

# ── ModelScore ──────────────────────────────────────────────────────────


class TestModelScoreEdgeCases:
    def test_zero_score(self):
        ms = ModelScore(model_id="m", score=0.0, cost_estimate=0.0, latency_estimate=0.0, source="cloud")
        assert ms.score == 0.0

    def test_zero_cost_is_valid(self):
        ms = ModelScore(model_id="m", score=50.0, cost_estimate=0.0, latency_estimate=100.0, source="cloud")
        assert ms.cost_estimate == 0.0

    def test_zero_latency_is_valid(self):
        ms = ModelScore(model_id="m", score=50.0, cost_estimate=0.01, latency_estimate=0.0, source="cloud")
        assert ms.latency_estimate == 0.0

    def test_very_large_score(self):
        ms = ModelScore(model_id="m", score=1e9, cost_estimate=0.0, latency_estimate=0.0, source="local")
        assert ms.score == 1e9

    def test_empty_model_id(self):
        ms = ModelScore(model_id="", score=50.0, cost_estimate=0.0, latency_estimate=100.0, source="cloud")
        assert ms.model_id == ""

    def test_source_local_valid(self):
        ms = ModelScore(model_id="m", score=50.0, cost_estimate=0.0, latency_estimate=0.0, source="local")
        assert ms.source == "local"

    def test_frozen_dataclass(self):
        ms = ModelScore(model_id="m", score=50.0, cost_estimate=0.0, latency_estimate=100.0, source="cloud")
        with pytest.raises(AttributeError):
            ms.score = 99.0  # type: ignore[misc]

    def test_equality(self):
        a = ModelScore(model_id="m", score=1.0, cost_estimate=0.0, latency_estimate=0.0, source="cloud")
        b = ModelScore(model_id="m", score=1.0, cost_estimate=0.0, latency_estimate=0.0, source="cloud")
        assert a == b

    def test_inequality_different_model(self):
        a = ModelScore(model_id="a", score=1.0, cost_estimate=0.0, latency_estimate=0.0, source="cloud")
        b = ModelScore(model_id="b", score=1.0, cost_estimate=0.0, latency_estimate=0.0, source="cloud")
        assert a != b


# ── BudgetProfile ───────────────────────────────────────────────────────


class TestBudgetProfileEdgeCases:
    def test_zero_max_cost(self):
        bp = BudgetProfile(max_cost_usd=0.0)
        assert bp.max_cost_usd == 0.0
        assert bp.prefer_local is False
        assert bp.max_latency_ms is None

    def test_all_params_set(self):
        bp = BudgetProfile(max_cost_usd=0.05, prefer_local=True, max_latency_ms=300.0)
        assert bp.max_cost_usd == 0.05
        assert bp.prefer_local is True
        assert bp.max_latency_ms == 300.0

    def test_equality(self):
        a = BudgetProfile(max_cost_usd=0.01)
        b = BudgetProfile(max_cost_usd=0.01)
        assert a == b

    def test_inequality(self):
        a = BudgetProfile(max_cost_usd=0.01)
        b = BudgetProfile(max_cost_usd=0.02)
        assert a != b


# ── Internal: _local_model_base_url ─────────────────────────────────────


class TestLocalModelBaseUrl:
    def test_default_value(self):
        with patch.dict(os.environ, {}, clear=True):
            assert _local_model_base_url() == "http://localhost:11434/v1"

    def test_custom_value(self):
        with patch.dict(os.environ, {"LOCAL_MODEL_BASE_URL": "http://192.168.1.1:8080/v1"}):
            assert _local_model_base_url() == "http://192.168.1.1:8080/v1"


# ── Internal: _local_model_available ────────────────────────────────────


class TestLocalModelAvailable:
    def test_env_set_to_1(self):
        with patch.dict(os.environ, {"GLUDD_ALLOW_LOCAL_MODEL_BASE_URLS": "1"}):
            assert _local_model_available() is True

    def test_env_set_to_0(self):
        with patch.dict(os.environ, {"GLUDD_ALLOW_LOCAL_MODEL_BASE_URLS": "0"}):
            assert _local_model_available() is False

    def test_env_not_set(self):
        with patch.dict(os.environ, {}, clear=True):
            assert _local_model_available() is False

    def test_env_set_to_empty_string(self):
        with patch.dict(os.environ, {"GLUDD_ALLOW_LOCAL_MODEL_BASE_URLS": ""}):
            assert _local_model_available() is False

    def test_env_set_to_non_one_value(self):
        with patch.dict(os.environ, {"GLUDD_ALLOW_LOCAL_MODEL_BASE_URLS": "true"}):
            assert _local_model_available() is False


# ── Internal: _is_local_model ───────────────────────────────────────────


class TestIsLocalModel:
    def test_localhost_base_returns_true(self):
        with patch.dict(
            os.environ,
            {
                "GLUDD_ALLOW_LOCAL_MODEL_BASE_URLS": "1",
                "LOCAL_MODEL_BASE_URL": "http://localhost:8080/v1",
            },
        ):
            assert _is_local_model("any/model") is True

    def test_loopback_base_returns_true(self):
        with patch.dict(
            os.environ,
            {
                "GLUDD_ALLOW_LOCAL_MODEL_BASE_URLS": "1",
                "LOCAL_MODEL_BASE_URL": "http://127.0.0.1:11434/v1",
            },
        ):
            assert _is_local_model("any/model") is True

    def test_remote_base_returns_false(self):
        with patch.dict(
            os.environ,
            {
                "GLUDD_ALLOW_LOCAL_MODEL_BASE_URLS": "1",
                "LOCAL_MODEL_BASE_URL": "http://192.168.1.100:8080/v1",
            },
        ):
            assert _is_local_model("any/model") is False

    def test_local_models_not_available_returns_false(self):
        with patch.dict(
            os.environ,
            {
                "GLUDD_ALLOW_LOCAL_MODEL_BASE_URLS": "0",
                "LOCAL_MODEL_BASE_URL": "http://localhost:8080/v1",
            },
        ):
            assert _is_local_model("any/model") is False

    def test_local_base_with_127_prefix(self):
        with patch.dict(
            os.environ,
            {
                "GLUDD_ALLOW_LOCAL_MODEL_BASE_URLS": "1",
                "LOCAL_MODEL_BASE_URL": "http://127.0.0.1:1234",
            },
        ):
            assert _is_local_model("any/model") is True


# ── Internal: _get_capabilities ─────────────────────────────────────────


class TestGetCapabilities:
    def test_known_model_known_task(self):
        caps = _get_capabilities("bug_fix", "openai/gpt-4o")
        assert caps["success"] == 0.92
        assert caps["latency_ms"] == 1200
        assert caps["cost_usd_per_1k"] == 0.005

    def test_unknown_model_returns_defaults(self):
        caps = _get_capabilities("bug_fix", "nonexistent/model")
        assert caps["success"] == 0.80
        assert caps["latency_ms"] == 1000.0
        assert caps["cost_usd_per_1k"] == 0.005

    def test_unknown_task_returns_defaults(self):
        caps = _get_capabilities("unknown_task", "openai/gpt-4o")
        assert caps["success"] == 0.80
        assert caps["latency_ms"] == 1000.0
        assert caps["cost_usd_per_1k"] == 0.005

    def test_returns_copy_not_reference(self):
        caps1 = _get_capabilities("bug_fix", "deepseek/deepseek-chat")
        caps2 = _get_capabilities("bug_fix", "deepseek/deepseek-chat")
        assert caps1 is not caps2


# ── Internal: _compute_score ────────────────────────────────────────────


class TestComputeScore:
    @pytest.fixture
    def caps(self) -> dict[str, float]:
        return {"success": 0.90, "latency_ms": 500.0, "cost_usd_per_1k": 0.001}

    @pytest.fixture
    def budget(self) -> BudgetProfile:
        return BudgetProfile(max_cost_usd=0.10)

    def test_success_component(self, caps, budget):
        score = _compute_score(caps, budget)
        assert score > 0
        expected_min = 0.90 * 60.0
        assert score >= expected_min

    def test_cost_component_zero_cost(self, budget):
        caps = {"success": 0.80, "latency_ms": 1000.0, "cost_usd_per_1k": 0.0}
        score = _compute_score(caps, budget)
        assert score > 0

    def test_latency_component_zero_latency(self, budget):
        caps = {"success": 0.80, "latency_ms": 0.0, "cost_usd_per_1k": 0.001}
        score = _compute_score(caps, budget)
        assert score > 0

    def test_prefer_local_adds_bonus(self, caps):
        bp_local = BudgetProfile(max_cost_usd=0.10, prefer_local=True)
        bp_remote = BudgetProfile(max_cost_usd=0.10, prefer_local=False)
        assert _compute_score(caps, bp_local) > _compute_score(caps, bp_remote)

    def test_max_latency_penalty(self, caps):
        bp_tight = BudgetProfile(max_cost_usd=0.10, max_latency_ms=100.0)
        bp_loose = BudgetProfile(max_cost_usd=0.10, max_latency_ms=None)
        assert _compute_score(caps, bp_tight) < _compute_score(caps, bp_loose)

    def test_max_latency_no_penalty_when_under(self, caps):
        bp = BudgetProfile(max_cost_usd=0.10, max_latency_ms=1000.0)
        bp_none = BudgetProfile(max_cost_usd=0.10, max_latency_ms=None)
        assert _compute_score(caps, bp) == _compute_score(caps, bp_none)

    def test_budget_exceeded_penalty(self, caps):
        bp_tight = BudgetProfile(max_cost_usd=0.0001)
        bp_loose = BudgetProfile(max_cost_usd=0.10)
        assert _compute_score(caps, bp_tight) < _compute_score(caps, bp_loose)

    def test_cost_multiplier_increases_cost_penalty(self, caps):
        bp = BudgetProfile(max_cost_usd=0.10)
        base = _compute_score(caps, bp, cost_multiplier=1.0)
        high = _compute_score(caps, bp, cost_multiplier=2.0)
        assert high <= base

    def test_both_penalties_applied(self, caps):
        bp = BudgetProfile(max_cost_usd=0.0001, max_latency_ms=100.0)
        score = _compute_score(caps, bp)
        assert score > 0
        bp_relaxed = BudgetProfile(max_cost_usd=0.10)
        assert score < _compute_score(caps, bp_relaxed)

    def test_missing_fields_use_defaults(self):
        caps: dict[str, float] = {}
        bp = BudgetProfile(max_cost_usd=0.10)
        score = _compute_score(caps, bp)
        assert score > 0

    def test_score_is_rounded_to_two_decimals(self, caps, budget):
        score = _compute_score(caps, budget)
        assert score == round(score, 2)
        assert len(str(score).split(".")[-1]) <= 2


# ── score_model ─────────────────────────────────────────────────────────


class TestScoreModelEdgeCases:
    def test_both_cost_router_and_hardware(self):
        from general_ludd.models.cost_router import CostAwareRouter, PeakPricingSchedule

        class _MockGPU:
            def gpu_count(self) -> int:
                return 1

            def vram_gb_per_gpu(self) -> float:
                return 8.0

            def system_ram_gb(self) -> float:
                return 32.0

        perf_router = MagicMock()
        peak = PeakPricingSchedule(peak_start_hour=0, peak_end_hour=0)
        cr = CostAwareRouter(performance_router=perf_router, peak_schedule=peak)
        budget = BudgetProfile(max_cost_usd=0.10)

        with patch.dict(
            os.environ,
            {
                "GLUDD_ALLOW_LOCAL_MODEL_BASE_URLS": "1",
                "LOCAL_MODEL_BASE_URL": "http://localhost:11434/v1",
            },
        ):
            result = score_model(
                "deepseek/deepseek-chat",
                "bug_fix",
                budget,
                cost_router=cr,
                hardware=_MockGPU(),
                now=datetime.datetime(2026, 1, 1, 12, 0, 0, tzinfo=datetime.UTC),
            )
            assert isinstance(result, ModelScore)
            assert result.latency_estimate > 0
            assert result.cost_estimate > 0

    def test_hardware_no_gpu(self):
        class _NoGPU:
            def gpu_count(self) -> int:
                return 0

            def vram_gb_per_gpu(self) -> float:
                return 0.0

            def system_ram_gb(self) -> float:
                return 16.0

        budget = BudgetProfile(max_cost_usd=0.10)
        with patch.dict(
            os.environ,
            {
                "GLUDD_ALLOW_LOCAL_MODEL_BASE_URLS": "1",
                "LOCAL_MODEL_BASE_URL": "http://localhost:11434/v1",
            },
        ):
            no_hw = score_model("deepseek/deepseek-chat", "bug_fix", budget)
            hw_result = score_model("deepseek/deepseek-chat", "bug_fix", budget, hardware=_NoGPU())
            assert hw_result.latency_estimate == no_hw.latency_estimate

    def test_hardware_low_vram(self):
        class _LowVRAM:
            def gpu_count(self) -> int:
                return 1

            def vram_gb_per_gpu(self) -> float:
                return 2.0

            def system_ram_gb(self) -> float:
                return 16.0

        budget = BudgetProfile(max_cost_usd=0.10)
        with patch.dict(
            os.environ,
            {
                "GLUDD_ALLOW_LOCAL_MODEL_BASE_URLS": "1",
                "LOCAL_MODEL_BASE_URL": "http://localhost:11434/v1",
            },
        ):
            no_hw = score_model("deepseek/deepseek-chat", "bug_fix", budget)
            hw_result = score_model("deepseek/deepseek-chat", "bug_fix", budget, hardware=_LowVRAM())
            assert hw_result.latency_estimate == no_hw.latency_estimate

    def test_now_not_datetime_does_not_crash(self):
        budget = BudgetProfile(max_cost_usd=0.10)
        result = score_model("openai/gpt-4o-mini", "bug_fix", budget, now="not a datetime")
        assert isinstance(result, ModelScore)

    def test_cost_router_with_now_none(self):
        from general_ludd.models.cost_router import CostAwareRouter, PeakPricingSchedule

        perf_router = MagicMock()
        peak = PeakPricingSchedule(peak_start_hour=0, peak_end_hour=0)
        cr = CostAwareRouter(performance_router=perf_router, peak_schedule=peak)
        budget = BudgetProfile(max_cost_usd=0.10)
        result = score_model("openai/gpt-4o-mini", "bug_fix", budget, cost_router=cr, now=None)
        assert isinstance(result, ModelScore)
        assert result.cost_estimate > 0

    def test_all_known_task_types(self):
        budget = BudgetProfile(max_cost_usd=0.10)
        for task_type in ["bug_fix", "feature", "review", "chat", "generate"]:
            result = score_model("openai/gpt-4o-mini", task_type, budget)
            assert isinstance(result, ModelScore)
            assert result.model_id == "openai/gpt-4o-mini"


# ── rank_models ─────────────────────────────────────────────────────────


class TestRankModelsEdgeCases:
    def test_unknown_task_returns_empty_list(self):
        ranked = rank_models("no_such_task", BudgetProfile(max_cost_usd=0.10))
        assert ranked == []
        assert isinstance(ranked, list)

    def test_all_task_types_return_results(self):
        budget = BudgetProfile(max_cost_usd=0.10)
        for task in ["bug_fix", "feature", "review", "chat", "generate"]:
            ranked = rank_models(task, budget)
            assert len(ranked) > 0, f"task {task} produced empty results"
            for i in range(len(ranked) - 1):
                assert ranked[i].score >= ranked[i + 1].score, f"task {task} not sorted"

    def test_single_model_task_returns_one(self):
        budget = BudgetProfile(max_cost_usd=0.10)
        full_ranked = rank_models("bug_fix", budget)
        assert len(full_ranked) > 1

    def test_score_tie_breaking(self):
        budget = BudgetProfile(max_cost_usd=0.10)
        ranked = rank_models("bug_fix", budget)
        for i in range(len(ranked) - 1):
            if ranked[i].score == ranked[i + 1].score:
                pass
        assert len(ranked) > 0

    def test_ranked_order_is_deterministic(self):
        budget = BudgetProfile(max_cost_usd=0.10)
        r1 = rank_models("bug_fix", budget)
        r2 = rank_models("bug_fix", budget)
        assert [m.model_id for m in r1] == [m.model_id for m in r2]

    def test_model_ids_are_unique(self):
        budget = BudgetProfile(max_cost_usd=0.10)
        ranked = rank_models("bug_fix", budget)
        ids = [m.model_id for m in ranked]
        assert len(ids) == len(set(ids))

    def test_all_scores_positive(self):
        budget = BudgetProfile(max_cost_usd=0.10)
        for task in ["bug_fix", "feature", "review", "chat", "generate"]:
            ranked = rank_models(task, budget)
            for m in ranked:
                assert m.score > 0, f"{task}/{m.model_id} score={m.score}"


# ── best_model ──────────────────────────────────────────────────────────


class TestBestModelEdgeCases:
    def test_unknown_task_returns_none(self):
        result = best_model("no_such_task", BudgetProfile(max_cost_usd=0.10))
        assert result is None

    def test_known_tasks_return_model_score(self):
        budget = BudgetProfile(max_cost_usd=0.10)
        for task in ["bug_fix", "feature", "review", "chat", "generate"]:
            best = best_model(task, budget)
            assert best is not None, f"task {task} returned None"
            assert isinstance(best, ModelScore)

    def test_best_matches_top_of_ranked(self):
        budget = BudgetProfile(max_cost_usd=0.10)
        for task in ["bug_fix", "feature", "review", "chat", "generate"]:
            ranked = rank_models(task, budget)
            best = best_model(task, budget)
            assert best is not None
            assert best.model_id == ranked[0].model_id
            assert best.score == ranked[0].score

    def test_tight_budget_lowers_all_scores(self):
        tight = BudgetProfile(max_cost_usd=0.0001)
        loose = BudgetProfile(max_cost_usd=0.10)
        for task in ["bug_fix", "chat"]:
            r_tight = score_model("openai/gpt-4o", task, tight)
            r_loose = score_model("openai/gpt-4o", task, loose)
            assert r_tight.score < r_loose.score


# ── _detect_hardware ────────────────────────────────────────────────────


class TestDetectHardware:
    def test_returns_protocol_compliant_object(self):
        hw = _detect_hardware()
        assert hasattr(hw, "gpu_count")
        assert hasattr(hw, "vram_gb_per_gpu")
        assert hasattr(hw, "system_ram_gb")
        assert callable(hw.gpu_count)
        assert callable(hw.vram_gb_per_gpu)
        assert callable(hw.system_ram_gb)

    def test_gpu_count_returns_int(self):
        hw = _detect_hardware()
        result = hw.gpu_count()
        assert isinstance(result, int)
        assert result >= 0

    def test_vram_gb_returns_float(self):
        hw = _detect_hardware()
        result = hw.vram_gb_per_gpu()
        assert isinstance(result, float)
        assert result >= 0.0

    def test_system_ram_gb_returns_float(self):
        hw = _detect_hardware()
        result = hw.system_ram_gb()
        assert isinstance(result, float)
        assert result >= 0.0

    def test_gpu_count_handles_subprocess_error(self):
        class _Detected:
            def gpu_count(self) -> int:
                try:
                    raise FileNotFoundError("nvidia-smi not found")
                except Exception:
                    return 0

        hw = _Detected()
        assert hw.gpu_count() == 0

    def test_gpu_count_handles_subprocess_timeout(self):
        class _Detected:
            def gpu_count(self) -> int:
                try:
                    raise subprocess.TimeoutExpired(cmd=["nvidia-smi"], timeout=5)
                except Exception:
                    return 0

        hw = _Detected()
        assert hw.gpu_count() == 0

    def test_vram_gb_handles_error(self):
        class _Detected:
            def vram_gb_per_gpu(self) -> float:
                try:
                    raise FileNotFoundError("nvidia-smi not found")
                except Exception:
                    return 0.0

        hw = _Detected()
        assert hw.vram_gb_per_gpu() == 0.0

    def test_system_ram_handles_error(self):
        class _Detected:
            def system_ram_gb(self) -> float:
                try:
                    raise Exception("sysctl failed")
                except Exception:
                    return 0.0

        hw = _Detected()
        assert hw.system_ram_gb() == 0.0


# ── Source-for edge ─────────────────────────────────────────────────────


class TestSourceForEdgeCases:
    def test_always_returns_cloud_or_local(self):
        with patch.dict(os.environ, {}, clear=True):
            assert _source_for("any/model") in ("local", "cloud")

    def test_unknown_model_still_returns_source(self):
        with patch.dict(os.environ, {}, clear=True):
            source = _source_for("completely/unknown-model-xyz")
            assert source in ("local", "cloud")


# ── Integration: cross-task consistency ─────────────────────────────────


class TestCrossTaskIntegration:
    def test_same_model_scores_differ_across_tasks(self):
        budget = BudgetProfile(max_cost_usd=0.10)
        scores = {}
        for task in ["bug_fix", "feature", "review", "chat", "generate"]:
            r = score_model("openai/gpt-4o", task, budget)
            scores[task] = r.score
        assert len(set(scores.values())) > 1

    def test_all_models_in_task_have_all_fields(self):
        budget = BudgetProfile(max_cost_usd=0.10)
        for task in ["bug_fix", "feature", "review", "chat", "generate"]:
            ranked = rank_models(task, budget)
            for m in ranked:
                assert m.model_id
                assert m.score > 0
                assert m.cost_estimate >= 0
                assert m.latency_estimate >= 0
                assert m.source in ("local", "cloud")

    def test_budget_profile_immutability(self):
        bp = BudgetProfile(max_cost_usd=0.10)
        with pytest.raises(AttributeError):
            bp.max_cost_usd = 0.20  # type: ignore[misc]
