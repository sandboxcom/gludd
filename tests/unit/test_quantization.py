"""Tests for quantization detection, tracking, and routing integration.

TDD: Define expected behavior BEFORE implementation.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestQuantizationInfo:
    def test_quantization_info_creation(self):
        from general_ludd.models.quantization import QuantizationInfo

        qi = QuantizationInfo(
            precision="fp16",
            source="provider_api",
            confidence=0.95,
            provider_name="fireworks",
            detected_at=1000.0,
        )
        assert qi.precision == "fp16"
        assert qi.source == "provider_api"
        assert qi.confidence == 0.95
        assert qi.provider_name == "fireworks"

    def test_quantization_info_defaults(self):
        from general_ludd.models.quantization import QuantizationInfo

        qi = QuantizationInfo(precision="unknown")
        assert qi.source == "unknown"
        assert qi.confidence == 0.0
        assert qi.provider_name is None
        assert qi.bits_estimate is None
        assert qi.raw_data is None

    def test_precision_enum_values(self):
        from general_ludd.models.quantization import Precision

        assert Precision.FP32 == "fp32"
        assert Precision.BF16 == "bf16"
        assert Precision.FP16 == "fp16"
        assert Precision.FP8 == "fp8"
        assert Precision.INT8 == "int8"
        assert Precision.INT4 == "int4"
        assert Precision.NF4 == "nf4"
        assert Precision.FP4 == "fp4"
        assert Precision.GGUF_Q4 == "gguf_q4"
        assert Precision.GGUF_Q5 == "gguf_q5"
        assert Precision.GGUF_Q8 == "gguf_q8"
        assert Precision.UNKNOWN == "unknown"

    def test_bits_estimate_from_precision(self):
        from general_ludd.models.quantization import Precision

        assert Precision.FP32.bits() == 32
        assert Precision.BF16.bits() == 16
        assert Precision.FP16.bits() == 16
        assert Precision.FP8.bits() == 8
        assert Precision.INT8.bits() == 8
        assert Precision.INT4.bits() == 4
        assert Precision.NF4.bits() == 4
        assert Precision.FP4.bits() == 4
        assert Precision.GGUF_Q4.bits() == 4
        assert Precision.GGUF_Q5.bits() == 5
        assert Precision.GGUF_Q8.bits() == 8
        assert Precision.UNKNOWN.bits() == 0

    def test_quality_degradation_score(self):
        from general_ludd.models.quantization import Precision

        assert Precision.FP32.quality_score() == 1.0
        assert Precision.BF16.quality_score() == 0.98
        assert Precision.FP16.quality_score() == 0.98
        assert Precision.FP8.quality_score() == 0.85
        assert Precision.INT8.quality_score() == 0.80
        assert Precision.INT4.quality_score() == 0.55
        assert Precision.NF4.quality_score() == 0.60
        assert Precision.FP4.quality_score() == 0.50
        assert Precision.GGUF_Q4.quality_score() == 0.55
        assert Precision.GGUF_Q5.quality_score() == 0.65
        assert Precision.GGUF_Q8.quality_score() == 0.80
        assert Precision.UNKNOWN.quality_score() == 0.70

    def test_from_string_normalizes_variants(self):
        from general_ludd.models.quantization import Precision

        assert Precision.from_string("fp16") == Precision.FP16
        assert Precision.from_string("FP16") == Precision.FP16
        assert Precision.from_string("float16") == Precision.FP16
        assert Precision.from_string("bf16") == Precision.BF16
        assert Precision.from_string("bfloat16") == Precision.BF16
        assert Precision.from_string("fp8") == Precision.FP8
        assert Precision.from_string("int4") == Precision.INT4
        assert Precision.from_string("nf4") == Precision.NF4
        assert Precision.from_string("Q4_K_M") == Precision.GGUF_Q4
        assert Precision.from_string("Q5_K_S") == Precision.GGUF_Q5
        assert Precision.from_string("Q8_0") == Precision.GGUF_Q8
        assert Precision.from_string("something_weird") == Precision.UNKNOWN


class TestFireworksDetector:
    @pytest.mark.asyncio
    async def test_detect_from_fireworks_api(self):
        from general_ludd.models.quantization import FireworksDetector

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "models": [
                {
                    "name": "llama-3.1-8b",
                    "baseModelDetails": {
                        "default_precision": "FP8",
                        "parameterCount": 8000000000,
                    },
                },
            ]
        }
        with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=mock_response):
            detector = FireworksDetector(api_key="test-key")
            results = await detector.detect("llama-3.1-8b")
        assert len(results) == 1
        assert results[0].precision == "fp8"
        assert results[0].source == "provider_api"
        assert results[0].confidence >= 0.9
        assert results[0].provider_name == "fireworks"

    @pytest.mark.asyncio
    async def test_detect_fireworks_auth_required(self):
        from general_ludd.models.quantization import FireworksDetector

        detector = FireworksDetector(api_key=None)
        results = await detector.detect("any-model")
        assert results == []

    @pytest.mark.asyncio
    async def test_detect_fireworks_all_precisions(self):
        from general_ludd.models.quantization import FireworksDetector

        precisions = ["FP16", "FP8", "FP8_MM", "NF4", "FP4", "BF16"]
        for p in precisions:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {
                "models": [
                    {
                        "name": "test-model",
                        "baseModelDetails": {"default_precision": p},
                    },
                ]
            }
            with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=mock_response):
                detector = FireworksDetector(api_key="k")
                results = await detector.detect("test-model")
            assert len(results) == 1
            assert results[0].precision != "unknown"


class TestHuggingFaceDetector:
    @pytest.mark.asyncio
    async def test_detect_safetensors_dtype(self):
        from general_ludd.models.quantization import HuggingFaceDetector

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "safetensors": {"parameters": {"BF16": 7248023552}, "total": 7248023552},
            "tags": ["safetensors"],
        }
        with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=mock_response):
            detector = HuggingFaceDetector()
            results = await detector.detect("mistralai/Mistral-7B")
        assert len(results) == 1
        assert results[0].precision == "bf16"
        assert results[0].source == "huggingface_metadata"
        assert results[0].confidence >= 0.8

    @pytest.mark.asyncio
    async def test_detect_gguf_quantization(self):
        from general_ludd.models.quantization import HuggingFaceDetector

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "gguf": {"total": 4099657152},
            "tags": ["gguf", "base_model:quantized:meta-llama/Llama-2-7b"],
            "siblings": [
                {"rfilename": "model.Q4_K_M.gguf"},
                {"rfilename": "model.Q8_0.gguf"},
            ],
            "cardData": {"quantized_by": "TheBloke"},
        }
        with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=mock_response):
            detector = HuggingFaceDetector()
            results = await detector.detect("TheBloke/Llama-2-7B-Chat-GGUF")
        assert len(results) >= 1
        quants = {r.precision for r in results}
        assert "gguf_q4" in quants or "gguf_q8" in quants

    @pytest.mark.asyncio
    async def test_detect_quantized_tag(self):
        from general_ludd.models.quantization import HuggingFaceDetector

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "tags": ["gptq", "base_model:quantized:meta-llama/Llama-2-7b"],
            "cardData": {"quantized_by": "AutoGPTQ"},
        }
        with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=mock_response):
            detector = HuggingFaceDetector()
            results = await detector.detect("some/gptq-model")
        assert len(results) >= 1
        assert any(r.precision == "int4" for r in results)

    @pytest.mark.asyncio
    async def test_detect_no_quantization_info(self):
        from general_ludd.models.quantization import HuggingFaceDetector

        mock_response = MagicMock()
        mock_response.status_code = 404
        with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=mock_response):
            detector = HuggingFaceDetector()
            results = await detector.detect("nonexistent/model")
        assert results == []


class TestSelfProbeDetector:
    def test_arithmetic_probe_prompt(self):
        from general_ludd.models.quantization import SelfProbeDetector

        detector = SelfProbeDetector()
        prompt = detector.arithmetic_probe_prompt()
        assert isinstance(prompt, str)
        assert len(prompt) > 50

    def test_score_arithmetic_response_high_precision(self):
        from general_ludd.models.quantization import SelfProbeDetector

        detector = SelfProbeDetector()
        score = detector.score_arithmetic_response(
            "The result is 98.9699345827.",
            expected=98.9699345827,
        )
        assert score > 0.9

    def test_score_arithmetic_response_low_precision(self):
        from general_ludd.models.quantization import SelfProbeDetector

        detector = SelfProbeDetector()
        score = detector.score_arithmetic_response(
            "The result is approximately 82.1.",
            expected=98.9699345827,
        )
        assert score < 0.5

    def test_score_arithmetic_response_partial(self):
        from general_ludd.models.quantization import SelfProbeDetector

        detector = SelfProbeDetector()
        score = detector.score_arithmetic_response(
            "Step 1: 7.8391 x 4.2173 = 33.06... Step 2: 33.06 x 2.9961 = 99.01",
            expected=98.9699345827,
        )
        assert score > 0.3

    def test_infer_precision_from_score(self):
        from general_ludd.models.quantization import SelfProbeDetector

        detector = SelfProbeDetector()
        assert detector.infer_precision_from_score(0.95) == "fp16"
        assert detector.infer_precision_from_score(0.85) == "fp8"
        assert detector.infer_precision_from_score(0.60) == "int8"
        assert detector.infer_precision_from_score(0.30) == "int4"

    def test_logprob_entropy_score(self):
        from general_ludd.models.quantization import SelfProbeDetector

        detector = SelfProbeDetector()
        high_confidence_logprobs = [-0.01, -0.05, -0.02, -0.03, -0.01]
        low_confidence_logprobs = [-2.5, -2.8, -3.1, -2.6, -2.9]
        high_score = detector.logprob_entropy_score(high_confidence_logprobs)
        low_score = detector.logprob_entropy_score(low_confidence_logprobs)
        assert high_score > low_score


class TestOpenRouterEndpointDetector:
    @pytest.mark.asyncio
    async def test_detect_from_endpoints(self):
        from general_ludd.models.quantization import OpenRouterEndpointDetector

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "data": {
                "endpoints": [
                    {
                        "provider_name": "DeepInfra",
                        "tag": "deepinfra/fp8",
                        "quantization": "fp8",
                        "pricing": {"prompt": "0.00000002", "completion": "0.00000003"},
                    },
                    {
                        "provider_name": "DeepInfra",
                        "tag": "deepinfra/bf16",
                        "quantization": "bf16",
                        "pricing": {"prompt": "0.00000002", "completion": "0.00000005"},
                    },
                ],
            }
        }
        with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=mock_response):
            detector = OpenRouterEndpointDetector()
            results = await detector.detect("meta-llama/llama-3.1-8b-instruct")
        assert len(results) == 2
        precisions = {r.precision for r in results}
        assert "fp8" in precisions
        assert "bf16" in precisions
        for r in results:
            assert r.source == "openrouter_endpoints"

    @pytest.mark.asyncio
    async def test_detect_unknown_quantization(self):
        from general_ludd.models.quantization import OpenRouterEndpointDetector

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "data": {
                "endpoints": [
                    {
                        "provider_name": "Groq",
                        "quantization": "unknown",
                    },
                ],
            }
        }
        with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=mock_response):
            detector = OpenRouterEndpointDetector()
            results = await detector.detect("some/model")
        assert len(results) == 1
        assert results[0].precision == "unknown"

    @pytest.mark.asyncio
    async def test_detect_failure_returns_empty(self):
        from general_ludd.models.quantization import OpenRouterEndpointDetector

        mock_response = MagicMock()
        mock_response.status_code = 404
        with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=mock_response):
            detector = OpenRouterEndpointDetector()
            results = await detector.detect("nonexistent/model")
        assert results == []


class TestQuantizationTracker:
    def test_tracker_stores_results(self):
        from general_ludd.models.quantization import QuantizationInfo, QuantizationTracker

        tracker = QuantizationTracker()
        qi = QuantizationInfo(precision="fp8", source="provider_api", confidence=0.9)
        tracker.update("model-1", qi)
        result = tracker.get("model-1")
        assert result is not None
        assert result.precision == "fp8"

    def test_tracker_returns_none_for_unknown(self):
        from general_ludd.models.quantization import QuantizationTracker

        tracker = QuantizationTracker()
        assert tracker.get("unknown-model") is None

    def test_tracker_highest_confidence_wins(self):
        from general_ludd.models.quantization import QuantizationInfo, QuantizationTracker

        tracker = QuantizationTracker()
        tracker.update("m1", QuantizationInfo(precision="unknown", source="openrouter_endpoints", confidence=0.3))
        tracker.update("m1", QuantizationInfo(precision="fp8", source="provider_api", confidence=0.95))
        result = tracker.get("m1")
        assert result.precision == "fp8"

    def test_tracker_does_not_overwrite_with_lower_confidence(self):
        from general_ludd.models.quantization import QuantizationInfo, QuantizationTracker

        tracker = QuantizationTracker()
        tracker.update("m1", QuantizationInfo(precision="fp8", source="provider_api", confidence=0.95))
        tracker.update("m1", QuantizationInfo(precision="unknown", source="openrouter_endpoints", confidence=0.3))
        result = tracker.get("m1")
        assert result.precision == "fp8"

    def test_tracker_list_all(self):
        from general_ludd.models.quantization import QuantizationInfo, QuantizationTracker

        tracker = QuantizationTracker()
        tracker.update("m1", QuantizationInfo(precision="fp16"))
        tracker.update("m2", QuantizationInfo(precision="int4"))
        all_results = tracker.list_all()
        assert len(all_results) == 2
        assert "m1" in all_results
        assert "m2" in all_results

    def test_tracker_detects_drift(self):
        from general_ludd.models.quantization import QuantizationInfo, QuantizationTracker

        tracker = QuantizationTracker()
        tracker.update("m1", QuantizationInfo(precision="fp16", confidence=0.9))
        old = tracker.get("m1")
        assert old is not None
        drift = tracker.check_drift("m1", QuantizationInfo(precision="fp8", confidence=0.85))
        assert drift is True

    def test_tracker_no_drift_same_precision(self):
        from general_ludd.models.quantization import QuantizationInfo, QuantizationTracker

        tracker = QuantizationTracker()
        tracker.update("m1", QuantizationInfo(precision="fp16", confidence=0.9))
        drift = tracker.check_drift("m1", QuantizationInfo(precision="fp16", confidence=0.9))
        assert drift is False

    def test_tracker_serialization(self):
        from general_ludd.models.quantization import QuantizationInfo, QuantizationTracker

        tracker = QuantizationTracker()
        tracker.update(
            "m1",
            QuantizationInfo(
                precision="fp8",
                source="provider_api",
                confidence=0.9,
                detected_at=1000.0,
            ),
        )
        data = tracker.to_dict()
        assert "m1" in data
        restored = QuantizationTracker.from_dict(data)
        result = restored.get("m1")
        assert result is not None
        assert result.precision == "fp8"


class TestQuantizationAwareRouting:
    def test_adjust_quality_class_high_precision(self):
        from general_ludd.models.quantization import adjust_quality_for_quantization

        assert adjust_quality_for_quantization("high", "fp16") == "high"
        assert adjust_quality_for_quantization("high", "bf16") == "high"

    def test_adjust_quality_class_medium_precision(self):
        from general_ludd.models.quantization import adjust_quality_for_quantization

        assert adjust_quality_for_quantization("high", "fp8") == "medium"
        assert adjust_quality_for_quantization("medium", "fp8") == "medium"

    def test_adjust_quality_class_low_precision(self):
        from general_ludd.models.quantization import adjust_quality_for_quantization

        assert adjust_quality_for_quantization("high", "int4") == "low"
        assert adjust_quality_for_quantization("medium", "nf4") == "low"
        assert adjust_quality_for_quantization("low", "int4") == "low"

    def test_adjust_quality_unknown_preserves(self):
        from general_ludd.models.quantization import adjust_quality_for_quantization

        assert adjust_quality_for_quantization("high", "unknown") == "high"
        assert adjust_quality_for_quantization("medium", "unknown") == "medium"

    def test_task_suitability_precision_sensitive_tasks(self):
        from general_ludd.models.quantization import task_precision_suitability

        assert task_precision_suitability("security_fix", "fp16") == "suitable"
        assert task_precision_suitability("security_fix", "int4") == "unsuitable"
        assert task_precision_suitability("bug_fix", "fp8") == "suitable"
        assert task_precision_suitability("documentation", "int4") == "suitable"
        assert task_precision_suitability("optimization", "int4") == "caution"

    def test_prefer_higher_precision_for_complex_tasks(self):
        from general_ludd.models.quantization import rank_models_for_task

        models = [
            {"profile_id": "cheap-int4", "precision": "int4", "cost": 0.001},
            {"profile_id": "mid-fp8", "precision": "fp8", "cost": 0.003},
            {"profile_id": "best-fp16", "precision": "fp16", "cost": 0.01},
        ]
        ranked = rank_models_for_task("security_fix", models)
        assert ranked[0]["profile_id"] == "best-fp16"

    def test_prefer_cheaper_for_simple_tasks(self):
        from general_ludd.models.quantization import rank_models_for_task

        models = [
            {"profile_id": "cheap-int4", "precision": "int4", "cost": 0.001},
            {"profile_id": "mid-fp8", "precision": "fp8", "cost": 0.003},
            {"profile_id": "best-fp16", "precision": "fp16", "cost": 0.01},
        ]
        ranked = rank_models_for_task("documentation", models)
        assert ranked[0]["profile_id"] == "cheap-int4"
