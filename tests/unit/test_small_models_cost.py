"""Tests for cost module — model inference/download/quantize cost estimation and off-peak scheduling."""

from __future__ import annotations

from datetime import UTC, datetime, timezone
from unittest.mock import patch

# — estimate_inference_cost ————————————————————————————————————————————


def test_estimate_inference_cost_known_small_model():
    from general_ludd.small_models.cost import estimate_inference_cost

    result = estimate_inference_cost("phi-2")
    assert isinstance(result, dict)
    assert result["model_id"] == "phi-2"
    assert "input_usd_per_1m_tokens" in result
    assert "output_usd_per_1m_tokens" in result
    assert "estimated_usd_per_hour" in result
    assert result["tier"] in ("free", "small_local", "medium_api", "large_api")
    assert result["input_usd_per_1m_tokens"] > 0.0


def test_estimate_inference_cost_small_local_model():
    from general_ludd.small_models.cost import estimate_inference_cost

    result = estimate_inference_cost("qwen2.5-0.5b")
    assert result["tier"] == "small_local"
    assert result["estimated_usd_per_hour"] < 1.0


def test_estimate_inference_cost_large_api_model():
    from general_ludd.small_models.cost import estimate_inference_cost

    result = estimate_inference_cost("gpt-4")
    assert result["tier"] == "large_api"


def test_estimate_inference_cost_gpu_second_backup():
    from general_ludd.small_models.cost import estimate_inference_cost

    result = estimate_inference_cost("llama3.1-8b")
    assert "estimated_gpu_usd_per_hour" in result


def test_estimate_inference_cost_unknown_model_returns_local():
    from general_ludd.small_models.cost import estimate_inference_cost

    result = estimate_inference_cost("some-unknown-model-v7")
    assert result["model_id"] == "some-unknown-model-v7"
    assert result["tier"] == "small_local"


# — estimate_download_cost ——————————————————————————————————————————————


def test_estimate_download_cost_small_model():
    from general_ludd.small_models.cost import estimate_download_cost

    result = estimate_download_cost("phi-2", size_gb=0.5)
    assert isinstance(result, dict)
    assert "data_transfer_usd" in result
    assert result["data_transfer_usd"] >= 0.0
    assert result["model_id"] == "phi-2"
    assert result["size_gb"] == 0.5


def test_estimate_download_cost_large_model_prefers_off_peak():
    from general_ludd.small_models.cost import estimate_download_cost

    result = estimate_download_cost("llama3.1-70b", size_gb=40.0)
    assert result["prefer_off_peak"] is True
    assert result["estimated_storage_usd_per_month"] > 0


def test_estimate_download_cost_small_model_no_off_peak():
    from general_ludd.small_models.cost import estimate_download_cost

    result = estimate_download_cost("phi-2", size_gb=0.1)
    assert result["prefer_off_peak"] is False


def test_estimate_download_cost_infers_size_from_model_name():
    from general_ludd.small_models.cost import estimate_download_cost

    result = estimate_download_cost("qwen2.5-7b")
    assert result["size_gb"] > 0.0


def test_estimate_download_cost_storage_cost():
    from general_ludd.small_models.cost import estimate_download_cost

    result = estimate_download_cost("model", size_gb=10.0)
    assert result["estimated_storage_usd_per_month"] > 0
    assert result["data_transfer_usd"] >= 0


# — estimate_quantize_cost ——————————————————————————————————————————————


def test_estimate_quantize_cost():
    from general_ludd.small_models.cost import estimate_quantize_cost

    result = estimate_quantize_cost("phi-2", size_gb=2.0, method="q4_k_m")
    assert isinstance(result, dict)
    assert "estimated_gpu_hours" in result
    assert "estimated_cost_usd" in result
    assert result["estimated_cost_usd"] >= 0.0
    assert result["method"] == "q4_k_m"


def test_estimate_quantize_cost_larger_model_costs_more():
    from general_ludd.small_models.cost import estimate_quantize_cost

    small = estimate_quantize_cost("small", size_gb=1.0, method="q4_k_m")
    large = estimate_quantize_cost("large", size_gb=10.0, method="q4_k_m")
    assert large["estimated_cost_usd"] > small["estimated_cost_usd"]


# — off-peak scheduling ——————————————————————————————————————————————————


def test_is_off_peak_weekend_always_off_peak():
    from general_ludd.small_models.cost import is_off_peak

    weekend_3am = datetime(2026, 8, 2, 3, 0, 0, tzinfo=UTC)
    with patch("general_ludd.small_models.cost.datetime") as mock_dt:
        mock_dt.now.return_value = weekend_3am
        mock_dt.timezone = timezone
        mock_dt.UTC = UTC
        assert is_off_peak() is True


def test_is_off_peak_weekday_3am_off_peak():
    from general_ludd.small_models.cost import is_off_peak

    weekday_3am = datetime(2026, 7, 29, 3, 0, 0, tzinfo=UTC)
    with patch("general_ludd.small_models.cost.datetime") as mock_dt:
        mock_dt.now.return_value = weekday_3am
        mock_dt.timezone = timezone
        mock_dt.UTC = UTC
        assert is_off_peak() is True


def test_is_off_peak_weekday_noon_on_peak():
    from general_ludd.small_models.cost import is_off_peak

    weekday_noon = datetime(2026, 7, 29, 12, 0, 0, tzinfo=UTC)
    with patch("general_ludd.small_models.cost.datetime") as mock_dt:
        mock_dt.now.return_value = weekday_noon
        mock_dt.timezone = timezone
        mock_dt.UTC = UTC
        assert is_off_peak() is False


def test_is_off_peak_boundary_6am_on_peak():
    from general_ludd.small_models.cost import is_off_peak

    boundary = datetime(2026, 7, 29, 6, 0, 0, tzinfo=UTC)
    with patch("general_ludd.small_models.cost.datetime") as mock_dt:
        mock_dt.now.return_value = boundary
        mock_dt.timezone = timezone
        mock_dt.UTC = UTC
        assert is_off_peak() is False


def test_is_off_peak_boundary_6pm_off_peak():
    from general_ludd.small_models.cost import is_off_peak

    boundary = datetime(2026, 7, 29, 18, 0, 0, tzinfo=UTC)
    with patch("general_ludd.small_models.cost.datetime") as mock_dt:
        mock_dt.now.return_value = boundary
        mock_dt.timezone = timezone
        mock_dt.UTC = UTC
        assert is_off_peak() is True


def test_next_off_peak_window():
    from general_ludd.small_models.cost import next_off_peak_window

    tuesday_8am = datetime(2026, 7, 28, 8, 0, 0, tzinfo=UTC)
    with patch("general_ludd.small_models.cost.datetime") as mock_dt:
        mock_dt.now.return_value = tuesday_8am
        mock_dt.timezone = timezone
        mock_dt.UTC = UTC
        result = next_off_peak_window()
        assert isinstance(result, dict)
        assert "starts_at" in result
        assert "seconds_until" in result
        assert result["seconds_until"] > 0
        assert "is_off_peak_now" in result
        assert result["is_off_peak_now"] is False


def test_should_defer_download_large_file():
    from general_ludd.small_models.cost import should_defer_download

    weekday_noon = datetime(2026, 7, 29, 12, 0, 0, tzinfo=UTC)
    with patch("general_ludd.small_models.cost.datetime") as mock_dt:
        mock_dt.now.return_value = weekday_noon
        mock_dt.timezone = timezone
        mock_dt.UTC = UTC
        result = should_defer_download(size_gb=5.0)
        assert result["defer"] is True
        assert result["reason"] == "large_download_during_peak"


def test_should_defer_download_small_file():
    from general_ludd.small_models.cost import should_defer_download

    weekday_noon = datetime(2026, 7, 29, 12, 0, 0, tzinfo=UTC)
    with patch("general_ludd.small_models.cost.datetime") as mock_dt:
        mock_dt.now.return_value = weekday_noon
        mock_dt.timezone = timezone
        mock_dt.UTC = UTC
        result = should_defer_download(size_gb=0.1)
        assert result["defer"] is False


def test_should_defer_download_off_peak_large():
    from general_ludd.small_models.cost import should_defer_download

    weekend_3am = datetime(2026, 8, 2, 3, 0, 0, tzinfo=UTC)
    with patch("general_ludd.small_models.cost.datetime") as mock_dt:
        mock_dt.now.return_value = weekend_3am
        mock_dt.timezone = timezone
        mock_dt.UTC = UTC
        result = should_defer_download(size_gb=10.0)
        assert result["defer"] is False


# — cost score for ranking ———————————————————————————————————————————————


def test_compute_cost_score_cheap_local_is_high():
    from general_ludd.small_models.cost import compute_cost_score

    result = compute_cost_score("phi-2")
    assert 0.0 <= result <= 1.0
    assert result > 0.7


def test_compute_cost_score_expensive_api_is_low():
    from general_ludd.small_models.cost import compute_cost_score

    result = compute_cost_score("gpt-4")
    assert 0.0 <= result <= 1.0
    assert result < 0.5
