"""E2E tests for SearX-based model deployment pipeline."""

from __future__ import annotations

import tempfile
from unittest.mock import MagicMock, patch

import pytest

from general_ludd.infra.model_deploy import (
    _estimate_kv_heads,
    _estimate_layers,
    _estimate_params_from_name,
    _gpu_for_params,
    _pick_best_quant,
    _search_result_to_profile,
    deploy_from_search,
    profile_from_search,
)
from general_ludd.infra.model_search import ModelSearchResult


class TestEstimateHelpers:
    def test_estimate_params_from_name_b(self):
        assert _estimate_params_from_name("llama-3-8b") == 8.0
        assert _estimate_params_from_name("mixtral-8x7B") == 7.0

    def test_estimate_params_from_name_m(self):
        assert _estimate_params_from_name("bert-110M") == 0.11

    def test_estimate_params_default(self):
        assert _estimate_params_from_name("unknown-model") == 7.0

    def test_estimate_layers(self):
        assert _estimate_layers(1.0) == 24
        assert _estimate_layers(7.0) == 32
        assert _estimate_layers(13.0) == 40
        assert _estimate_layers(34.0) == 60
        assert _estimate_layers(70.0) == 80
        assert _estimate_layers(100.0) == 96

    def test_estimate_kv_heads(self):
        assert _estimate_kv_heads(7.0) == 8
        assert _estimate_kv_heads(70.0) == 8

    def test_gpu_for_params(self):
        assert _gpu_for_params(7.0) == "l4"
        assert _gpu_for_params(13.0) == "a10g"
        assert _gpu_for_params(34.0) == "a100_40"
        assert _gpu_for_params(70.0) == "a100_80"
        assert _gpu_for_params(100.0) == "h100"
        assert _gpu_for_params(300.0) == "h200"

    def test_pick_best_quant_vllm(self):
        assert _pick_best_quant(["q4_k_m", "fp8", "bf16"], "vllm") == "fp8"
        assert _pick_best_quant(["q4_k_m", "q8_0"], "vllm") == "bf16"

    def test_pick_best_quant_llamacpp(self):
        assert _pick_best_quant(["q4_k_m", "q8_0", "fp16"], "llamacpp") == "q4_k_m"
        assert _pick_best_quant(["q8_0", "q6_k"], "llamacpp") == "q6_k"


class TestSearchResultToProfile:
    def test_basic_profile(self):
        result = ModelSearchResult(
            name="test-org__test-model",
            params_count=7.0,
            quantizations_available=["q4_k_m", "fp16"],
        )
        profile = _search_result_to_profile(result)
        assert profile.name == "test-org__test-model"
        assert profile.params_b == 7.0
        assert profile.num_layers == 32
        assert profile.num_kv_heads == 8
        assert profile.head_dim == 128

    def test_moe_detection(self):
        result = ModelSearchResult(
            name="mistralai__Mixtral-8x7B-v0.1",
            params_count=47.0,
            quantizations_available=["q4_k_m"],
        )
        profile = _search_result_to_profile(result)
        assert profile.is_moe is True

    def test_native_quant_detection(self):
        result = ModelSearchResult(
            name="deepseek-ai__DeepSeek-V3",
            params_count=671.0,
            quantizations_available=["fp8", "q4_k_m"],
        )
        profile = _search_result_to_profile(result)
        assert profile.native_quant == "fp8"


class TestDeployFromSearch:
    def test_cached_lookup(self):
        with tempfile.TemporaryDirectory(), \
             patch("general_ludd.infra.model_deploy.ModelIndex") as mock_idx_cls, \
             patch("general_ludd.infra.model_deploy.recommend_config") as mock_rec, \
             patch("general_ludd.infra.model_deploy.hardware_profile_for") as mock_hw:
            mock_idx = MagicMock()
            mock_idx.get.return_value = ModelSearchResult(
                name="meta-llama__Llama-3-8B",
                params_count=8.0,
                quantizations_available=["q4_k_m", "fp16"],
                source_url="https://huggingface.co/meta-llama/Llama-3-8B",
            )
            mock_idx_cls.return_value = mock_idx

            mock_rec.return_value = {
                "engine": "vllm",
                "model": "meta-llama__Llama-3-8B",
                "tensor_parallel_size": 1,
                "gpu_memory_utilization": 0.9,
                "max_model_len": 32768,
                "dtype": "bf16",
                "quantization": None,
            }
            mock_hw.return_value = MagicMock()

            result = deploy_from_search("Llama-3-8B", engine="llamacpp")
            assert result["model"] == "meta-llama__Llama-3-8B"
            assert result["params_b"] == 8.0

    def test_not_found_raises(self):
        with patch("general_ludd.infra.model_deploy.SearXModelSearch") as mock_search:
            mock_searcher = MagicMock()
            mock_searcher.find_model.return_value = None
            mock_search.return_value = mock_searcher
            with patch("general_ludd.infra.model_deploy.ModelIndex") as mock_idx:
                mock_idx_inst = MagicMock()
                mock_idx_inst.get.return_value = None
                mock_idx.return_value = mock_idx_inst
                with pytest.raises(ValueError):
                    deploy_from_search("nonexistent-model-xyz")


class TestProfileFromSearch:
    def test_cached_profile(self):
        with patch("general_ludd.infra.model_deploy.ModelIndex") as mock_idx:
            mock_idx_inst = MagicMock()
            mock_idx_inst.search.return_value = [
                ModelSearchResult(name="test", quantizations_available=["fp8"])
            ]
            mock_idx.return_value = mock_idx_inst

            profile = profile_from_search("test-query")
            assert profile is not None
            assert profile.quantization == "fp8"

    def test_no_results(self):
        with patch("general_ludd.infra.model_deploy.SearXModelSearch") as mock_search, \
             patch("general_ludd.infra.model_deploy.ModelIndex") as mock_idx:
            mock_idx_inst = MagicMock()
            mock_idx_inst.search.return_value = []
            mock_idx.return_value = mock_idx_inst

            mock_searcher = MagicMock()
            mock_searcher.search_models.return_value = []
            mock_search.return_value = mock_searcher

            profile = profile_from_search("nonexistent")
            assert profile is None
