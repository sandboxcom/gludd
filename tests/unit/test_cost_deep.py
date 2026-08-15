"""Deep edge-case tests for small_models/cost.py.

Covers: zero cost, negative cost, extreme budgets, concurrent tracking,
cost overflow, rounding precision, multi-source cost comparison, boundary
conditions on off-peak windows, and internal resolver edge cases.
"""

from __future__ import annotations

import math
import threading
from datetime import UTC, datetime
from typing import cast
from unittest.mock import patch

from general_ludd.small_models.cost import (
    _MODEL_SIZE_GB,
    _infer_tier,
    _resolve_model_size,
    compute_cost_score,
    estimate_download_cost,
    estimate_inference_cost,
    estimate_quantize_cost,
    is_off_peak,
    next_off_peak_window,
    should_defer_download,
)

_F = cast  # short alias for float extraction from dict[str, object]


# ── zero / negative cost edge cases ──────────────────────────────────────


class TestZeroNegativeCost:
    """Edge cases where costs approach zero or go negative."""

    def test_download_cost_zero_size_gb(self) -> None:
        result = estimate_download_cost("tiny", size_gb=0.0)
        assert cast(float, result["data_transfer_usd"]) == 0.0
        assert cast(float, result["estimated_storage_usd_per_month"]) == 0.0
        assert result["prefer_off_peak"] is False
        assert cast(float, result["size_gb"]) == 0.0

    def test_download_cost_zero_size_rounded(self) -> None:
        result = estimate_download_cost("tiny", size_gb=0.0)
        assert isinstance(result["data_transfer_usd"], float)
        assert isinstance(result["estimated_storage_usd_per_month"], float)

    def test_quantize_cost_zero_size_gb(self) -> None:
        result = estimate_quantize_cost("tiny", size_gb=0.0, method="q4_k_m")
        assert cast(float, result["estimated_gpu_hours"]) == 0.0
        assert cast(float, result["estimated_cost_usd"]) == 0.0

    def test_quantize_cost_zero_size_all_methods(self) -> None:
        for method in ("q4_0", "q4_k_m", "q5_k_m", "q8_0", "f16"):
            result = estimate_quantize_cost("m", size_gb=0.0, method=method)
            assert cast(float, result["estimated_cost_usd"]) == 0.0, f"non-zero for {method}"

    def test_quantize_cost_negative_size_gb(self) -> None:
        result = estimate_quantize_cost("bad", size_gb=-1.0, method="q4_k_m")
        assert cast(float, result["estimated_cost_usd"]) < 0.0

    def test_download_cost_negative_size_gb(self) -> None:
        result = estimate_download_cost("bad", size_gb=-5.0)
        assert cast(float, result["data_transfer_usd"]) < 0.0
        assert cast(float, result["estimated_storage_usd_per_month"]) < 0.0

    def test_compute_cost_score_clamps_zero_estimated_cost(self) -> None:
        with patch("general_ludd.small_models.cost.estimate_inference_cost") as mock_est:
            mock_est.return_value = {
                "model_id": "zero-cost",
                "tier": "small_local",
                "estimated_usd_per_hour": 0.0,
                "input_usd_per_1m_tokens": 0.0,
                "output_usd_per_1m_tokens": 0.0,
                "estimated_tokens_per_hour": 2000000,
                "estimated_gpu_usd_per_hour": 3.0,
            }
            score = compute_cost_score("zero-cost")
            assert score > 0.0
            assert score <= 1.0

    def test_compute_cost_score_clamps_negative_estimated_cost(self) -> None:
        with patch("general_ludd.small_models.cost.estimate_inference_cost") as mock_est:
            mock_est.return_value = {
                "model_id": "neg-cost",
                "tier": "small_local",
                "estimated_usd_per_hour": -5.0,
                "input_usd_per_1m_tokens": 0.0,
                "output_usd_per_1m_tokens": 0.0,
                "estimated_tokens_per_hour": 2000000,
                "estimated_gpu_usd_per_hour": 3.0,
            }
            score = compute_cost_score("neg-cost")
            assert score > 0.0
            assert score <= 1.0

    def test_compute_cost_score_very_expensive_model_approaches_zero(self) -> None:
        with patch("general_ludd.small_models.cost.estimate_inference_cost") as mock_est:
            mock_est.return_value = {
                "model_id": "expensive",
                "tier": "large_api",
                "estimated_usd_per_hour": 100.0,
                "input_usd_per_1m_tokens": 1.0,
                "output_usd_per_1m_tokens": 2.0,
                "estimated_tokens_per_hour": 200000,
                "estimated_gpu_usd_per_hour": 3.0,
            }
            score = compute_cost_score("expensive")
            assert 0.0 <= score < 0.01

    def test_compute_cost_score_very_cheap_model_approaches_one(self) -> None:
        with patch("general_ludd.small_models.cost.estimate_inference_cost") as mock_est:
            mock_est.return_value = {
                "model_id": "cheap",
                "tier": "small_local",
                "estimated_usd_per_hour": 0.000001,
                "input_usd_per_1m_tokens": 0.0,
                "output_usd_per_1m_tokens": 0.0,
                "estimated_tokens_per_hour": 2000000,
                "estimated_gpu_usd_per_hour": 3.0,
            }
            score = compute_cost_score("cheap")
            assert 0.99 <= score <= 1.0

    def test_compute_cost_score_unknown_tier_defaults_multiplier_one(self) -> None:
        with patch("general_ludd.small_models.cost.estimate_inference_cost") as mock_est:
            mock_est.return_value = {
                "model_id": "weird",
                "tier": "nonexistent",
                "estimated_usd_per_hour": 0.01,
                "input_usd_per_1m_tokens": 0.0001,
                "output_usd_per_1m_tokens": 0.0002,
                "estimated_tokens_per_hour": 2000000,
                "estimated_gpu_usd_per_hour": 3.0,
            }
            score = compute_cost_score("weird")
            assert score == 1.0


# ── extreme budgets / overflow ───────────────────────────────────────────


class TestExtremeValues:
    """Very large and very small inputs — verify no overflow or NaN."""

    def test_download_cost_very_large_size_gb(self) -> None:
        result = estimate_download_cost("huge", size_gb=1e12)
        transfer = cast(float, result["data_transfer_usd"])
        storage = cast(float, result["estimated_storage_usd_per_month"])
        assert math.isfinite(transfer)
        assert math.isfinite(storage)
        assert transfer > 0.0

    def test_quantize_cost_very_large_size_gb(self) -> None:
        result = estimate_quantize_cost("huge", size_gb=1e9, method="q4_k_m")
        cost = cast(float, result["estimated_cost_usd"])
        hours = cast(float, result["estimated_gpu_hours"])
        assert math.isfinite(cost)
        assert math.isfinite(hours)

    def test_quantize_cost_very_small_size_gb(self) -> None:
        result = estimate_quantize_cost("tiny", size_gb=1e-10, method="q4_k_m")
        cost = cast(float, result["estimated_cost_usd"])
        assert cost >= 0.0
        assert math.isfinite(cost)

    def test_quantize_cost_unknown_method_falls_back(self) -> None:
        result = estimate_quantize_cost("test", size_gb=5.0, method="nonexistent")
        cost = cast(float, result["estimated_cost_usd"])
        assert result["method"] == "nonexistent"
        assert math.isfinite(cost)

    def test_quantize_cost_empty_method(self) -> None:
        result = estimate_quantize_cost("test", size_gb=5.0, method="")
        cost = cast(float, result["estimated_cost_usd"])
        assert math.isfinite(cost)

    def test_inference_cost_unknown_model_no_metadata(self) -> None:
        result = estimate_inference_cost("totally-unknown-model-2026")
        est = cast(float, result["estimated_usd_per_hour"])
        assert result["model_id"] == "totally-unknown-model-2026"
        assert math.isfinite(est)
        assert est >= 0.0

    def test_download_cost_no_size_inference(self) -> None:
        result = estimate_download_cost("no-size-hint")
        assert cast(float, result["size_gb"]) == 4.0
        assert math.isfinite(cast(float, result["data_transfer_usd"]))

    def test_compute_cost_score_bounds_all_known_models(self) -> None:
        for model_id in _MODEL_SIZE_GB:
            score = compute_cost_score(model_id)
            assert 0.0 <= score <= 1.0, f"score={score} for {model_id}"
            assert math.isfinite(score)

    def test_inference_cost_empty_model_id(self) -> None:
        result = estimate_inference_cost("")
        assert isinstance(result, dict)
        assert "estimated_usd_per_hour" in result


# ── concurrent cost tracking ─────────────────────────────────────────────


class TestConcurrentCost:
    """Thread safety and concurrent access to cost functions."""

    def test_concurrent_compute_cost_score(self) -> None:
        errors: list[BaseException] = []
        threads = 8
        calls_per_thread = 50

        def worker() -> None:
            try:
                for _ in range(calls_per_thread):
                    score = compute_cost_score("phi-2")
                    assert 0.0 <= score <= 1.0
            except BaseException as exc:
                errors.append(exc)

        ts = [threading.Thread(target=worker) for _ in range(threads)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        assert not errors, f"threads raised: {errors!r}"

    def test_concurrent_estimate_inference_cost(self) -> None:
        errors: list[BaseException] = []
        models = list(_MODEL_SIZE_GB.keys())

        def worker() -> None:
            try:
                for m in models * 10:
                    result = estimate_inference_cost(m)
                    assert isinstance(result, dict)
                    assert result["model_id"] == m
            except BaseException as exc:
                errors.append(exc)

        ts = [threading.Thread(target=worker) for _ in range(4)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        assert not errors, f"threads raised: {errors!r}"

    def test_concurrent_download_cost(self) -> None:
        errors: list[BaseException] = []

        def worker() -> None:
            try:
                for _ in range(100):
                    result = estimate_download_cost("phi-2", size_gb=2.7)
                    assert cast(float, result["data_transfer_usd"]) >= 0.0
            except BaseException as exc:
                errors.append(exc)

        ts = [threading.Thread(target=worker) for _ in range(4)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        assert not errors, f"threads raised: {errors!r}"

    def test_concurrent_quantize_cost(self) -> None:
        errors: list[BaseException] = []

        def worker() -> None:
            try:
                for _ in range(100):
                    result = estimate_quantize_cost("phi-2", size_gb=2.7)
                    assert isinstance(cast(float, result["estimated_cost_usd"]), float)
            except BaseException as exc:
                errors.append(exc)

        ts = [threading.Thread(target=worker) for _ in range(4)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        assert not errors, f"threads raised: {errors!r}"


# ── rounding precision ───────────────────────────────────────────────────


class TestRoundingPrecision:
    """Tests for rounding behavior at edge-case precision boundaries."""

    def test_inference_cost_has_expected_precision(self) -> None:
        result = estimate_inference_cost("phi-2")
        inp = cast(float, result["input_usd_per_1m_tokens"])
        out = cast(float, result["output_usd_per_1m_tokens"])
        est = cast(float, result["estimated_usd_per_hour"])
        assert inp == round(inp, 6)
        assert out == round(out, 6)
        assert est == round(est, 6)

    def test_download_cost_has_expected_precision(self) -> None:
        result = estimate_download_cost("test", size_gb=1.23456789)
        assert cast(float, result["data_transfer_usd"]) == round(1.23456789 * 0.09, 4)
        assert cast(float, result["size_gb"]) == round(1.23456789, 2)

    def test_quantize_cost_has_expected_precision(self) -> None:
        result = estimate_quantize_cost("test", size_gb=1.0 / 3.0, method="q4_k_m")
        cost = cast(float, result["estimated_cost_usd"])
        assert cost == round(cost, 6)

    def test_compute_cost_score_always_4_decimals(self) -> None:
        score = compute_cost_score("phi-2")
        assert score == round(score, 4)

    def test_very_small_rounding_does_not_zero_out(self) -> None:
        result = estimate_inference_cost("qwen2.5-0.5b")
        est = cast(float, result["estimated_usd_per_hour"])
        assert est > 0.0

    def test_gpu_usd_per_hour_rounding(self) -> None:
        result = estimate_inference_cost("phi-2")
        gpu = cast(float, result["estimated_gpu_usd_per_hour"])
        assert gpu == round(gpu, 4)


# ── multi-source cost comparison ─────────────────────────────────────────


class TestMultiSourceCostComparison:
    """Cross-function consistency and multi-source cost ranking."""

    def test_larger_model_more_expensive_download(self) -> None:
        small = estimate_download_cost("phi-2", size_gb=2.7)
        large = estimate_download_cost("llama3.1-70b", size_gb=140.0)
        assert cast(float, large["data_transfer_usd"]) > cast(float, small["data_transfer_usd"])
        assert cast(float, large["estimated_storage_usd_per_month"]) > cast(
            float, small["estimated_storage_usd_per_month"]
        )

    def test_larger_model_more_expensive_quantize(self) -> None:
        small = estimate_quantize_cost("phi-2", size_gb=2.7, method="q4_k_m")
        large = estimate_quantize_cost("llama3.1-70b", size_gb=140.0, method="q4_k_m")
        assert cast(float, large["estimated_cost_usd"]) > cast(float, small["estimated_cost_usd"])
        assert cast(float, large["estimated_gpu_hours"]) > cast(float, small["estimated_gpu_hours"])

    def test_quantize_methods_rank_by_effort(self) -> None:
        size = 10.0
        results = {
            m: cast(float, estimate_quantize_cost("test", size_gb=size, method=m)["estimated_cost_usd"])
            for m in ("f16", "q4_0", "q4_k_m", "q5_k_m", "q8_0")
        }
        assert results["f16"] < results["q4_0"] < results["q8_0"]
        assert results["q4_0"] < results["q4_k_m"] < results["q5_k_m"]

    def test_cost_ranking_consistent_across_models(self) -> None:
        models = sorted(_MODEL_SIZE_GB.keys(), key=lambda m: compute_cost_score(m), reverse=True)
        phi_idx = next(i for i, m in enumerate(models) if "phi-2" in m and "phi-3" not in m)
        llama_idx = next(i for i, m in enumerate(models) if "70b" in m)
        assert phi_idx < llama_idx, f"phi-2 should rank better (cheaper) than 70b: {models}"

    def test_tier_assignment_consistent_with_size(self) -> None:
        small_models = [m for m in _MODEL_SIZE_GB if _MODEL_SIZE_GB[m] <= 4.0]
        medium_models = [m for m in _MODEL_SIZE_GB if 4.0 < _MODEL_SIZE_GB[m] < 20.0]
        large_models = [m for m in _MODEL_SIZE_GB if _MODEL_SIZE_GB[m] >= 20.0]

        for m in small_models:
            assert _infer_tier(m) == "small_local", f"{m} should be small_local"
        for m in medium_models:
            assert _infer_tier(m) == "medium_api", f"{m} should be medium_api"
        for m in large_models:
            assert _infer_tier(m) == "large_api", f"{m} should be large_api"

    def test_download_cost_matches_total_cost(self) -> None:
        size = 10.0
        result = estimate_download_cost("test", size_gb=size)
        expected_transfer = round(size * 0.09, 4)
        expected_storage = round(size * 0.10, 4)
        assert cast(float, result["data_transfer_usd"]) == expected_transfer
        assert cast(float, result["estimated_storage_usd_per_month"]) == expected_storage

    def test_defer_threshold_exactly_at_boundary(self) -> None:
        noon = datetime(2026, 8, 4, 12, 0, 0, tzinfo=UTC)
        r_eq = should_defer_download(5.0, now=noon)
        r_above = should_defer_download(5.0001, now=noon)
        r_below = should_defer_download(4.9999, now=noon)
        assert r_above["defer"] is True
        assert r_below["defer"] is False
        assert r_eq["defer"] is True


# ── off-peak boundary conditions ─────────────────────────────────────────


class TestOffPeakBoundaries:
    """Off-peak scheduling edge cases: DST, weekday boundaries, midnight."""

    def test_friday_23_59_is_off_peak(self) -> None:
        friday_late = datetime(2026, 8, 7, 23, 59, 0, tzinfo=UTC)
        assert is_off_peak(friday_late) is True

    def test_saturday_00_00_is_off_peak(self) -> None:
        saturday_early = datetime(2026, 8, 8, 0, 0, 0, tzinfo=UTC)
        assert is_off_peak(saturday_early) is True

    def test_sunday_23_59_is_off_peak(self) -> None:
        sunday_late = datetime(2026, 8, 9, 23, 59, 0, tzinfo=UTC)
        assert is_off_peak(sunday_late) is True

    def test_monday_00_00_is_off_peak(self) -> None:
        monday_early = datetime(2026, 8, 10, 0, 0, 0, tzinfo=UTC)
        assert is_off_peak(monday_early) is True

    def test_all_weekend_hours_off_peak(self) -> None:
        for day_offset in (5, 6):
            for hour in range(24):
                dt = datetime(2026, 8, 3 + day_offset, hour, 0, 0, tzinfo=UTC)
                assert is_off_peak(dt) is True, f"day={day_offset} hour={hour}"

    def test_all_weekday_peak_hours_on_peak(self) -> None:
        for hour in range(6, 18):
            dt = datetime(2026, 8, 4, hour, 0, 0, tzinfo=UTC)
            assert is_off_peak(dt) is False, f"hour={hour}"

    def test_all_weekday_off_peak_hours_are_off_peak(self) -> None:
        for hour in list(range(0, 6)) + list(range(18, 24)):
            dt = datetime(2026, 8, 4, hour, 0, 0, tzinfo=UTC)
            assert is_off_peak(dt) is True, f"hour={hour}"

    def test_next_off_peak_window_already_off_peak_afternoon(self) -> None:
        evening = datetime(2026, 8, 4, 20, 0, 0, tzinfo=UTC)
        window = next_off_peak_window(evening)
        assert window["is_off_peak_now"] is True
        assert window["seconds_until"] == 0

    def test_next_off_peak_window_already_off_peak_early_morning(self) -> None:
        early = datetime(2026, 8, 4, 4, 0, 0, tzinfo=UTC)
        window = next_off_peak_window(early)
        assert window["is_off_peak_now"] is True
        assert window["seconds_until"] == 0

    def test_next_off_peak_window_seconds_positive_during_peak(self) -> None:
        noon = datetime(2026, 8, 4, 12, 0, 0, tzinfo=UTC)
        window = next_off_peak_window(noon)
        assert window["is_off_peak_now"] is False
        assert cast(int, window["seconds_until"]) > 0
        assert cast(int, window["seconds_until"]) <= 6 * 3600

    def test_next_off_peak_window_friday_afternoon_seconds_reasonable(self) -> None:
        friday_noon = datetime(2026, 8, 7, 12, 0, 0, tzinfo=UTC)
        window = next_off_peak_window(friday_noon)
        assert window["is_off_peak_now"] is False
        seconds = cast(int, window["seconds_until"])
        assert 0 < seconds <= 6 * 3600

    def test_next_off_peak_window_near_midnight_peak(self) -> None:
        just_before = datetime(2026, 8, 4, 17, 59, 59, tzinfo=UTC)
        window = next_off_peak_window(just_before)
        assert window["is_off_peak_now"] is False
        assert cast(int, window["seconds_until"]) == 1

    def test_is_off_peak_now_defaults_to_utc(self) -> None:
        result = is_off_peak()
        assert isinstance(result, bool)

    def test_next_off_peak_window_defaults_to_utc(self) -> None:
        result = next_off_peak_window()
        assert isinstance(result, dict)
        assert "is_off_peak_now" in result
        assert "seconds_until" in result

    def test_next_off_peak_window_exactly_at_6am(self) -> None:
        boundary = datetime(2026, 8, 4, 6, 0, 0, tzinfo=UTC)
        window = next_off_peak_window(boundary)
        assert window["is_off_peak_now"] is False

    def test_next_off_peak_window_exactly_at_6pm(self) -> None:
        boundary = datetime(2026, 8, 4, 18, 0, 0, tzinfo=UTC)
        window = next_off_peak_window(boundary)
        assert window["is_off_peak_now"] is True
        assert window["seconds_until"] == 0


# ── internal resolver edge cases ─────────────────────────────────────────


class TestInternalResolvers:
    """Edge cases for _resolve_model_size and _infer_tier."""

    def test_resolve_model_size_empty_string(self) -> None:
        assert _resolve_model_size("") == 4.0

    def test_resolve_model_size_zero_b_model(self) -> None:
        assert _resolve_model_size("0b") == 0.0

    def test_resolve_model_size_partial_match_70b(self) -> None:
        assert _resolve_model_size("70b-model") == 140.0

    def test_resolve_model_size_partial_match_8b(self) -> None:
        assert _resolve_model_size("8b") == 16.0

    def test_resolve_model_size_unknown_fallback(self) -> None:
        assert _resolve_model_size("xyz-model") == 4.0

    def test_resolve_model_size_case_insensitive(self) -> None:
        assert _resolve_model_size("PHI-2") == 2.7
        assert _resolve_model_size("LlAmA3.1-8B") == 16.0

    def test_resolve_model_size_decimal_param_count(self) -> None:
        size = _resolve_model_size("model-3.5b")
        assert size == 7.0

    def test_resolve_model_size_param_in_non_b_suffix(self) -> None:
        assert _resolve_model_size("model-10b-experiment") == 20.0

    def test_infer_tier_size_boundary_at_4_gb(self) -> None:
        assert _infer_tier("2b-model") == "small_local"

    def test_infer_tier_size_above_4_is_medium(self) -> None:
        assert _infer_tier("3.1b-model") == "medium_api"

    def test_infer_tier_size_boundary_at_20(self) -> None:
        assert _infer_tier("10b-model") == "large_api"

    def test_infer_tier_size_between_4_and_20_is_medium(self) -> None:
        assert _infer_tier("5b-model") == "medium_api"

    def test_infer_tier_gpt4_is_large_api(self) -> None:
        assert _infer_tier("gpt-4") == "large_api"
        assert _infer_tier("gpt-4-turbo") == "large_api"

    def test_infer_tier_claude_is_large_api(self) -> None:
        assert _infer_tier("claude-sonnet") == "large_api"
        assert _infer_tier("claude-opus") == "large_api"
        assert _infer_tier("claude-haiku") == "large_api"

    def test_infer_tier_gpt3_is_medium_api(self) -> None:
        assert _infer_tier("gpt-3.5-turbo") == "medium_api"

    def test_infer_tier_mini_keyword_is_small_local(self) -> None:
        assert _infer_tier("some-mini-model") == "small_local"

    def test_infer_tier_ambiguous_name_prefers_known_keyword(self) -> None:
        assert _infer_tier("gpt-3") == "medium_api"
        assert _infer_tier("claude-3-haiku") == "large_api"


# ── compute_cost_score detailed ──────────────────────────────────────────


class TestComputeCostScoreDetailed:
    """Detailed edge cases for compute_cost_score."""

    def test_score_monotonic_with_estimated_cost(self) -> None:
        """Cheaper models score higher — verify monotonic for all known models."""
        for m1 in _MODEL_SIZE_GB:
            for m2 in _MODEL_SIZE_GB:
                s1 = compute_cost_score(m1)
                s2 = compute_cost_score(m2)
                info1 = estimate_inference_cost(m1)
                info2 = estimate_inference_cost(m2)
                e1 = cast(float, info1["estimated_usd_per_hour"])
                e2 = cast(float, info2["estimated_usd_per_hour"])
                if e1 < e2:
                    assert s1 >= s2, f"{m1} cheaper but scored lower: {s1} vs {s2}"

    def test_tier_multiplier_reduces_score_for_large_api(self) -> None:
        with patch("general_ludd.small_models.cost.estimate_inference_cost") as mock_est:
            mock_est.return_value = {
                "model_id": "test",
                "tier": "large_api",
                "estimated_usd_per_hour": 0.01,
                "input_usd_per_1m_tokens": 0.0001,
                "output_usd_per_1m_tokens": 0.0002,
                "estimated_tokens_per_hour": 200000,
                "estimated_gpu_usd_per_hour": 3.0,
            }
            score = compute_cost_score("large")
            assert score == 0.4

    def test_tier_multiplier_for_medium_api(self) -> None:
        with patch("general_ludd.small_models.cost.estimate_inference_cost") as mock_est:
            mock_est.return_value = {
                "model_id": "test",
                "tier": "medium_api",
                "estimated_usd_per_hour": 0.01,
                "input_usd_per_1m_tokens": 0.0001,
                "output_usd_per_1m_tokens": 0.0002,
                "estimated_tokens_per_hour": 500000,
                "estimated_gpu_usd_per_hour": 3.0,
            }
            score = compute_cost_score("medium")
            assert score == 0.7

    def test_score_for_models_with_different_gpu_fallback(self) -> None:
        info = estimate_inference_cost("llama3.1-8b")
        assert isinstance(info["estimated_gpu_usd_per_hour"], float)
        assert cast(float, info["estimated_gpu_usd_per_hour"]) > 0.0


# ── inference cost edge cases ────────────────────────────────────────────


class TestInferenceCostEdgeCases:
    """Additional edge cases for estimate_inference_cost."""

    def test_known_claude_model_uses_pricing(self) -> None:
        with patch(
            "general_ludd.small_models.cost.PRICING",
            {
                "claude-sonnet-4": (3.0, 15.0),
            },
        ):
            result = estimate_inference_cost("claude-sonnet-4")
            assert cast(float, result["input_usd_per_1m_tokens"]) == 3.0
            assert cast(float, result["output_usd_per_1m_tokens"]) == 15.0
            assert result["tier"] == "large_api"

    def test_known_gpt_model_uses_pricing(self) -> None:
        with patch(
            "general_ludd.small_models.cost.PRICING",
            {
                "gpt-4o": (5.0, 15.0),
            },
        ):
            result = estimate_inference_cost("gpt-4o")
            assert cast(float, result["input_usd_per_1m_tokens"]) == 5.0
            assert cast(float, result["output_usd_per_1m_tokens"]) == 15.0

    def test_small_model_pricing_substring_match(self) -> None:
        result = estimate_inference_cost("phi-2-special-edition")
        assert cast(float, result["input_usd_per_1m_tokens"]) == 0.0001
        assert cast(float, result["output_usd_per_1m_tokens"]) == 0.0002

    def test_small_local_tier_no_gpu_comparison(self) -> None:
        result = estimate_inference_cost("phi-2")
        est = cast(float, result["estimated_usd_per_hour"])
        assert est == round(est, 6)

    def test_return_keys_present_for_all_models(self) -> None:
        for model_id in ("phi-2", "qwen2.5-7b", "llama3.1-70b", "gpt-4"):
            result = estimate_inference_cost(model_id)
            for key in (
                "model_id",
                "tier",
                "input_usd_per_1m_tokens",
                "output_usd_per_1m_tokens",
                "estimated_tokens_per_hour",
                "estimated_usd_per_hour",
                "estimated_gpu_usd_per_hour",
            ):
                assert key in result, f"{key} missing for {model_id}"


# ── should_defer and off-peak interaction ────────────────────────────────


class TestDeferOffPeakInteraction:
    """Interaction between should_defer_download and off-peak functions."""

    def test_should_defer_consistent_with_is_off_peak(self) -> None:
        for day_offset in range(7):
            for hour in range(0, 24, 4):
                dt = datetime(2026, 8, 3 + day_offset, hour, 0, 0, tzinfo=UTC)
                off_peak = is_off_peak(dt)
                result = should_defer_download(50.0, now=dt)
                if off_peak:
                    assert result["defer"] is False, f"Should not defer during off-peak: {dt}"
                else:
                    assert result["defer"] is True, f"Should defer during peak: {dt}"

    def test_should_defer_small_download_never_defers(self) -> None:
        for day_offset in range(7):
            for hour in range(0, 24, 4):
                dt = datetime(2026, 8, 3 + day_offset, hour, 0, 0, tzinfo=UTC)
                result = should_defer_download(1.0, now=dt)
                assert result["defer"] is False, f"deferred at {dt}"

    def test_next_off_peak_window_starts_at_correct_time(self) -> None:
        noon = datetime(2026, 8, 4, 12, 0, 0, tzinfo=UTC)
        window = next_off_peak_window(noon)
        starts_at = datetime.fromisoformat(cast(str, window["starts_at"]))
        assert starts_at.hour == 18
        assert starts_at.minute == 0

    def test_next_off_peak_window_during_peak_returns_future(self) -> None:
        for hour in range(6, 18):
            dt = datetime(2026, 8, 4, hour, 0, 0, tzinfo=UTC)
            window = next_off_peak_window(dt)
            assert window["is_off_peak_now"] is False
            seconds = cast(int, window["seconds_until"])
            assert seconds > 0

    def test_next_off_peak_during_off_peak_returns_now(self) -> None:
        for hour in list(range(0, 6)) + list(range(18, 24)):
            dt = datetime(2026, 8, 4, hour, 0, 0, tzinfo=UTC)
            window = next_off_peak_window(dt)
            assert window["is_off_peak_now"] is True, f"hour={hour}"
            assert window["seconds_until"] == 0, f"hour={hour}"


# ── download cost edge cases ─────────────────────────────────────────────


class TestDownloadCostEdgeCases:
    """Edge cases for estimate_download_cost."""

    def test_all_known_models_have_reasonable_download_cost(self) -> None:
        for model_id in _MODEL_SIZE_GB:
            result = estimate_download_cost(model_id)
            size = cast(float, result["size_gb"])
            expected_size = _MODEL_SIZE_GB[model_id]
            assert size == expected_size, f"{model_id}: {size} != {expected_size}"
            assert cast(float, result["data_transfer_usd"]) == round(expected_size * 0.09, 4)
            assert cast(float, result["estimated_storage_usd_per_month"]) == round(expected_size * 0.10, 4)

    def test_off_peak_preference_consistent_with_size(self) -> None:
        for model_id in _MODEL_SIZE_GB:
            result = estimate_download_cost(model_id)
            size = cast(float, result["size_gb"])
            assert result["prefer_off_peak"] is (size >= 10.0), (
                f"{model_id}: {result['prefer_off_peak']} vs size {size}"
            )

    def test_explicit_size_override_prefer_off_peak(self) -> None:
        result = estimate_download_cost("tiny", size_gb=100.0)
        assert result["prefer_off_peak"] is True
        assert cast(float, result["size_gb"]) == 100.0
