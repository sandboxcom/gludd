"""Unit tests for model_deploy search-to-config pipeline."""

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


class TestEstimateParamsFromName:
    def test_b_params(self):
        assert _estimate_params_from_name("llama-7b") == 7.0

    def test_m_params(self):
        assert _estimate_params_from_name("tiny-500m") == 0.5

    def test_default(self):
        assert _estimate_params_from_name("unknown") == 7.0


class TestEstimateLayers:
    def test_tiers(self):
        assert _estimate_layers(2) == 24
        assert _estimate_layers(7) == 32
        assert _estimate_layers(12) == 40
        assert _estimate_layers(25) == 60
        assert _estimate_layers(50) == 80
        assert _estimate_layers(100) == 96


class TestEstimateKvHeads:
    def test_always_eight(self):
        assert _estimate_kv_heads(1) == 8
        assert _estimate_kv_heads(100) == 8


class TestGpuForParams:
    def test_tiers(self):
        assert _gpu_for_params(4) == "l4"
        assert _gpu_for_params(10) == "a10g"
        assert _gpu_for_params(20) == "a100_40"
        assert _gpu_for_params(50) == "a100_80"
        assert _gpu_for_params(100) == "h100"
        assert _gpu_for_params(250) == "h200"
        assert _gpu_for_params(0.1) == "l4"


class TestPickBestQuant:
    def test_llamacpp(self):
        assert _pick_best_quant(["Q4_K_M", "Q5_K_M"], "llamacpp") == "q4_k_m"

    def test_vllm(self):
        assert _pick_best_quant(["AWQ", "GPTQ"], "vllm") == "awq"

    def test_fallback(self):
        assert _pick_best_quant(["unknown"], "vllm") == "bf16"


class TestSearchResultToProfile:
    def test_moe_detection(self):
        result = ModelSearchResult(
            name="mixtral-8x7b",
            source_url="",
            download_urls=[],
            quantizations_available=["Q4_K_M"],
            params_count=47.0,
        )
        profile = _search_result_to_profile(result)
        assert profile.is_moe is True
        assert profile.params_b == 47.0

    def test_fp8_quant(self):
        result = ModelSearchResult(
            name="model-fp8",
            source_url="",
            download_urls=[],
            quantizations_available=["FP8"],
        )
        profile = _search_result_to_profile(result)
        assert profile.native_quant == "fp8"


class TestDeployFromSearch:
    @patch("general_ludd.infra.model_deploy.SearXModelSearch")
    @patch("general_ludd.infra.model_deploy.ModelIndex")
    def test_deploy_new_model(self, mock_index_cls, mock_search_cls):
        index = MagicMock()
        index.get.return_value = None
        mock_index_cls.return_value = index

        searcher = MagicMock()
        result = ModelSearchResult(
            name="test-model",
            source_url="http://example.com",
            download_urls=["http://example.com/model.gguf"],
            quantizations_available=["Q4_K_M"],
            params_count=7.0,
        )
        searcher.find_model.return_value = result
        mock_search_cls.return_value = searcher

        with patch("general_ludd.infra.model_deploy.recommend_config") as mock_recommend:
            mock_recommend.return_value = {"gpu_count": 1}
            out = deploy_from_search("test-model")
            assert out["model"] == "test-model"
            assert out["params_b"] == 7.0
            assert "compute_config" in out

    @patch("general_ludd.infra.model_deploy.SearXModelSearch")
    @patch("general_ludd.infra.model_deploy.ModelIndex")
    def test_deploy_not_found(self, mock_index_cls, mock_search_cls):
        index = MagicMock()
        index.get.return_value = None
        mock_index_cls.return_value = index
        searcher = MagicMock()
        searcher.find_model.return_value = None
        mock_search_cls.return_value = searcher

        with pytest.raises(ValueError, match="not found"):
            deploy_from_search("missing-model")


class TestProfileFromSearch:
    @patch("general_ludd.infra.model_deploy.SearXModelSearch")
    @patch("general_ludd.infra.model_deploy.ModelIndex")
    def test_profile_found(self, mock_index_cls, mock_search_cls):
        index = MagicMock()
        index.search.return_value = []
        mock_index_cls.return_value = index
        searcher = MagicMock()
        result = ModelSearchResult(
            name="test-model",
            source_url="",
            download_urls=[],
            quantizations_available=["Q4_K_M"],
        )
        searcher.search_models.return_value = [result]
        mock_search_cls.return_value = searcher

        profile = profile_from_search("test-model")
        assert profile is not None
        assert profile.quantization == "bf16"

    @patch("general_ludd.infra.model_deploy.SearXModelSearch")
    @patch("general_ludd.infra.model_deploy.ModelIndex")
    def test_profile_not_found(self, mock_index_cls, mock_search_cls):
        index = MagicMock()
        index.search.return_value = []
        mock_index_cls.return_value = index
        searcher = MagicMock()
        searcher.search_models.return_value = []
        mock_search_cls.return_value = searcher

        assert profile_from_search("missing-model") is None
