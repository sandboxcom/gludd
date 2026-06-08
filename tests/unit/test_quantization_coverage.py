from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestPrecisionFromStringAdditional:
    def test_gptq_maps_to_int4(self):
        from general_ludd.models.quantization import Precision

        assert Precision.from_string("gptq") == Precision.INT4

    def test_awq_maps_to_int4(self):
        from general_ludd.models.quantization import Precision

        assert Precision.from_string("awq") == Precision.INT4

    def test_quantiz_maps_to_unknown(self):
        from general_ludd.models.quantization import Precision

        assert Precision.from_string("quantiz") == Precision.UNKNOWN

    def test_gguf_without_q_prefix(self):
        from general_ludd.models.quantization import Precision

        assert Precision.from_string("gguf") == Precision.GGUF_Q4


class TestFireworksDetectorAdditional:
    @pytest.mark.asyncio
    async def test_detect_non_200_status(self):
        from general_ludd.models.quantization import FireworksDetector

        mock_response = MagicMock()
        mock_response.status_code = 403
        with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=mock_response):
            detector = FireworksDetector(api_key="test-key")
            results = await detector.detect("some-model")
        assert results == []

    @pytest.mark.asyncio
    async def test_detect_exception_returns_empty(self):
        from general_ludd.models.quantization import FireworksDetector

        with patch("httpx.AsyncClient.get", new_callable=AsyncMock, side_effect=ConnectionError("fail")):
            detector = FireworksDetector(api_key="test-key")
            results = await detector.detect("some-model")
        assert results == []

    @pytest.mark.asyncio
    async def test_detect_skips_non_matching_models(self):
        from general_ludd.models.quantization import FireworksDetector

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "models": [
                {"name": "other-model", "baseModelDetails": {"default_precision": "FP16"}},
                {"name": "target-model", "baseModelDetails": {"default_precision": "FP8"}},
            ]
        }
        with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=mock_response):
            detector = FireworksDetector(api_key="k")
            results = await detector.detect("target-model")
        assert len(results) == 1
        assert results[0].precision == "fp8"


class TestHuggingFaceDetectorAdditional:
    @pytest.mark.asyncio
    async def test_detect_exception_returns_empty(self):
        from general_ludd.models.quantization import HuggingFaceDetector

        with patch("httpx.AsyncClient.get", new_callable=AsyncMock, side_effect=ConnectionError("timeout")):
            detector = HuggingFaceDetector()
            results = await detector.detect("any/model")
        assert results == []

    @pytest.mark.asyncio
    async def test_detect_quantized_by_without_quant_tags(self):
        from general_ludd.models.quantization import HuggingFaceDetector

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "tags": ["safetensors"],
            "cardData": {"quantized_by": "TheBloke"},
        }
        with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=mock_response):
            detector = HuggingFaceDetector()
            results = await detector.detect("some/quantized-model")
        assert len(results) == 1
        assert results[0].precision == "int4"
        assert results[0].confidence == 0.60


class TestSelfProbeDetectorAdditional:
    def test_score_no_numbers_returns_zero(self):
        from general_ludd.models.quantization import SelfProbeDetector

        detector = SelfProbeDetector()
        score = detector.score_arithmetic_response("no numbers here!", expected=42.0)
        assert score == 0.0

    def test_score_expected_zero_skips(self):
        from general_ludd.models.quantization import SelfProbeDetector

        detector = SelfProbeDetector()
        score = detector.score_arithmetic_response("result is 5.0", expected=0)
        assert score == 0.0

    def test_logprob_entropy_empty_list(self):
        from general_ludd.models.quantization import SelfProbeDetector

        detector = SelfProbeDetector()
        assert detector.logprob_entropy_score([]) == 0.5


class TestOpenRouterEndpointDetectorAdditional:
    @pytest.mark.asyncio
    async def test_detect_exception_returns_empty(self):
        from general_ludd.models.quantization import OpenRouterEndpointDetector

        with patch("httpx.AsyncClient.get", new_callable=AsyncMock, side_effect=TimeoutError("timed out")):
            detector = OpenRouterEndpointDetector()
            results = await detector.detect("some/model")
        assert results == []


class TestQuantizationTrackerAdditional:
    def test_check_drift_no_existing_returns_false(self):
        from general_ludd.models.quantization import QuantizationInfo, QuantizationTracker

        tracker = QuantizationTracker()
        result = tracker.check_drift("unknown-model", QuantizationInfo(precision="fp16"))
        assert result is False


class TestAdjustQualityAdditional:
    def test_unknown_precision_falls_through(self):
        from general_ludd.models.quantization import adjust_quality_for_quantization

        assert adjust_quality_for_quantization("high", "totally_unknown_precision") == "high"


class TestTaskPrecisionSuitabilityAdditional:
    def test_medium_requirement_caution(self):
        from general_ludd.models.quantization import task_precision_suitability

        assert task_precision_suitability("optimization", "int4") == "caution"

    def test_medium_requirement_unsuitable(self):
        from general_ludd.models.quantization import task_precision_suitability

        assert task_precision_suitability("optimization", "fp4") == "unsuitable"

    def test_high_requirement_caution(self):
        from general_ludd.models.quantization import task_precision_suitability

        assert task_precision_suitability("security_fix", "fp8") == "caution"

    def test_high_requirement_unsuitable(self):
        from general_ludd.models.quantization import task_precision_suitability

        assert task_precision_suitability("security_fix", "fp4") == "unsuitable"


class TestRankModelsAdditional:
    def test_zero_max_cost_handled(self):
        from general_ludd.models.quantization import rank_models_for_task

        models = [
            {"profile_id": "free", "precision": "fp16", "cost": 0},
            {"profile_id": "also-free", "precision": "int4", "cost": 0},
        ]
        ranked = rank_models_for_task("bug_fix", models)
        assert len(ranked) == 2

    def test_none_requirement_prefers_cheapest(self):
        from general_ludd.models.quantization import rank_models_for_task

        models = [
            {"profile_id": "expensive-fp16", "precision": "fp16", "cost": 0.01},
            {"profile_id": "cheap-int4", "precision": "int4", "cost": 0.001},
        ]
        ranked = rank_models_for_task("documentation", models)
        assert ranked[0]["profile_id"] == "cheap-int4"
