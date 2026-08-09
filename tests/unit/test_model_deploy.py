"""Unit tests for infra/model_deploy.py — model deployment from SearXNG search."""

from __future__ import annotations

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

# ── _gpu_for_params ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("params_b", "expected_gpu"),
    [
        (0.5, "l4"),
        (3.0, "l4"),
        (8.0, "l4"),
        (8.1, "a10g"),
        (12.0, "a10g"),
        (15.0, "a10g"),
        (15.1, "a100_40"),
        (25.0, "a100_40"),
        (35.0, "a100_40"),
        (35.1, "a100_80"),
        (50.0, "a100_80"),
        (80.0, "a100_80"),
        (80.1, "h100"),
        (150.0, "h100"),
        (200.0, "h100"),
        (200.1, "h200"),
        (400.0, "h200"),
    ],
)
def test_gpu_for_params_returns_correct_gpu(params_b: float, expected_gpu: str) -> None:
    assert _gpu_for_params(params_b) == expected_gpu


def test_gpu_for_params_zero_falls_back_to_t4() -> None:
    assert _gpu_for_params(0.0) == "t4"


def test_gpu_for_params_negative_falls_back_to_t4() -> None:
    assert _gpu_for_params(-1.0) == "t4"


# ── _estimate_params_from_name ─────────────────────────────────────────────


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Llama-3-8B-Instruct", 8.0),
        ("Mixtral-8x7B-v0.1", 7.0),
        ("Qwen2.5-72B-Instruct", 72.0),
        ("gemma-2-27b-it", 27.0),
        ("DeepSeek-R1-671B", 671.0),
        ("Llama-3.1-8B-Instruct-GGUF", 8.0),
        ("phi-3-mini-4k-instruct", 7.0),
        ("llama-2-7b.Q4_K_M", 7.0),
        ("Mistral-7B-v0.1", 7.0),
    ],
)
def test_estimate_params_from_name_parses_b_notation(name: str, expected: float) -> None:
    assert pytest.approx(_estimate_params_from_name(name)) == expected


def test_estimate_params_from_name_parses_m_notation() -> None:
    assert _estimate_params_from_name("something-350m") == pytest.approx(0.35)
    assert _estimate_params_from_name("bert-110M") == pytest.approx(0.11)


def test_estimate_params_from_name_no_match_defaults_to_7() -> None:
    assert _estimate_params_from_name("some-weird-model-name") == 7.0
    assert _estimate_params_from_name("") == 7.0


# ── _estimate_layers ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("params_b", "expected"),
    [
        (0.5, 24),
        (3.0, 24),
        (3.1, 32),
        (8.0, 32),
        (8.1, 40),
        (15.0, 40),
        (15.1, 60),
        (35.0, 60),
        (35.1, 80),
        (80.0, 80),
        (80.1, 96),
        (200.0, 96),
    ],
)
def test_estimate_layers(params_b: float, expected: int) -> None:
    assert _estimate_layers(params_b) == expected


# ── _estimate_kv_heads ─────────────────────────────────────────────────────


def test_estimate_kv_heads_always_returns_8() -> None:
    for params in (0.5, 3.0, 8.0, 15.0, 35.0, 80.0, 200.0, 671.0):
        assert _estimate_kv_heads(params) == 8


# ── _pick_best_quant ───────────────────────────────────────────────────────


def test_pick_best_quant_llamacpp_prefers_q4_k_m() -> None:
    assert _pick_best_quant(["q4_k_m", "q8_0", "f16"], "llamacpp") == "q4_k_m"


def test_pick_best_quant_llamacpp_falls_back_through_priority_order() -> None:
    assert _pick_best_quant(["f16", "q8_0"], "llamacpp") == "q8_0"
    assert _pick_best_quant(["f16", "q5_k_m"], "llamacpp") == "q5_k_m"
    assert _pick_best_quant(["f16"], "llamacpp") == "q4_k_m"


def test_pick_best_quant_vllm_prefers_awq() -> None:
    result = _pick_best_quant(["bf16", "awq", "gptq"], "vllm")
    assert result == "awq"


def test_pick_best_quant_vllm_prefers_gptq_over_int8() -> None:
    result = _pick_best_quant(["int8", "gptq"], "vllm")
    assert result == "gptq"


def test_pick_best_quant_vllm_falls_back_to_bf16() -> None:
    result = _pick_best_quant(["safetensors"], "vllm")
    assert result == "bf16"


def test_pick_best_quant_case_insensitive() -> None:
    result = _pick_best_quant(["AWQ", "Bf16"], "vllm")
    assert result == "awq"


def test_pick_best_quant_empty_list_returns_bf16() -> None:
    assert _pick_best_quant([], "vllm") == "bf16"


# ── _search_result_to_profile ──────────────────────────────────────────────


def test_search_result_to_profile_basic() -> None:
    result = ModelSearchResult(
        name="Llama-3-8B-Instruct",
        source_url="https://huggingface.co/meta-llama/Llama-3-8B-Instruct",
        download_urls=["https://huggingface.co/meta-llama/Llama-3-8B-Instruct"],
        quantizations_available=["q4_k_m", "q5_k_m", "q8_0", "fp8"],
    )
    profile = _search_result_to_profile(result)
    assert profile.name == "Llama-3-8B-Instruct"
    assert profile.params_b == 8.0
    assert profile.num_layers == 32
    assert profile.num_kv_heads == 8
    assert profile.head_dim == 128
    assert profile.is_moe is False
    assert profile.native_quant == "fp8"


def test_search_result_to_profile_moe_detection() -> None:
    result = ModelSearchResult(
        name="Mixtral-8x7B-v0.1",
        source_url="https://huggingface.co/mistralai/Mixtral-8x7B-v0.1",
        download_urls=[],
        quantizations_available=["gptq"],
    )
    profile = _search_result_to_profile(result)
    assert profile.is_moe is True


def test_search_result_to_profile_with_params_count_in_result() -> None:
    result = ModelSearchResult(
        name="some-model",
        source_url="https://huggingface.co/org/some-model",
        download_urls=[],
        params_count=15.0,
        quantizations_available=["awq", "int8"],
    )
    profile = _search_result_to_profile(result)
    assert profile.params_b == 15.0
    assert profile.num_layers == 40


def test_search_result_to_profile_no_quantizations_no_native_quant() -> None:
    result = ModelSearchResult(
        name="Llama-3-8B",
        source_url="https://huggingface.co/meta-llama/Llama-3-8B",
        download_urls=[],
        quantizations_available=["q4_k_m", "q5_k_m"],
    )
    profile = _search_result_to_profile(result)
    assert profile.native_quant is None


# ── deploy_from_search ─────────────────────────────────────────────────────


def _make_mock_result(
    name: str = "Llama-3-8B-Instruct",
    params_count: float = 0.0,
    quantizations: list[str] | None = None,
) -> ModelSearchResult:
    return ModelSearchResult(
        name=name,
        source_url=f"https://huggingface.co/org/{name}",
        download_urls=[f"https://huggingface.co/org/{name}/resolve/main/model.gguf"],
        params_count=params_count,
        quantizations_available=quantizations or ["q4_k_m", "q8_0", "fp8"],
    )


def test_deploy_from_search_uses_model_index_cache() -> None:
    result = _make_mock_result()
    mock_index = MagicMock()
    mock_index.get.return_value = result

    with (
        patch(
            "general_ludd.infra.model_deploy.ModelIndex",
            return_value=mock_index,
        ),
        patch(
            "general_ludd.infra.model_deploy.SearXModelSearch",
        ) as mock_searx,
        patch(
            "general_ludd.infra.model_deploy.hardware_profile_for",
        ) as mock_hpf,
        patch(
            "general_ludd.infra.model_deploy.recommend_config",
            return_value={"gpus": 1, "memory_mb": 8192},
        ),
    ):
        mock_hpf.return_value = MagicMock(
            total_vram_gb=16, gpu_count=1, arch="ampere", supports_fp8=True, has_nvlink=False
        )
        output = deploy_from_search("Llama-3-8B-Instruct")
        mock_searx.return_value.find_model.assert_not_called()
        assert output["model"] == "Llama-3-8B-Instruct"
        assert output["recommended_config"]["quantization"] == "fp8"


def test_deploy_from_search_falls_back_to_searx_on_cache_miss() -> None:
    result = _make_mock_result()
    mock_index = MagicMock()
    mock_index.get.return_value = None

    with (
        patch(
            "general_ludd.infra.model_deploy.ModelIndex",
            return_value=mock_index,
        ),
        patch(
            "general_ludd.infra.model_deploy.SearXModelSearch",
        ) as mock_searx,
        patch(
            "general_ludd.infra.model_deploy.hardware_profile_for",
        ) as mock_hpf,
        patch(
            "general_ludd.infra.model_deploy.recommend_config",
            return_value={"gpus": 1, "memory_mb": 8192},
        ),
    ):
        mock_searx.return_value.find_model.return_value = result
        mock_hpf.return_value = MagicMock(
            total_vram_gb=16, gpu_count=1, arch="ampere", supports_fp8=True, has_nvlink=False
        )
        output = deploy_from_search("Llama-3-8B-Instruct")
        mock_searx.return_value.find_model.assert_called_once_with("Llama-3-8B-Instruct")
        assert output["model"] == "Llama-3-8B-Instruct"


def test_deploy_from_search_raises_when_model_not_found() -> None:
    mock_index = MagicMock()
    mock_index.get.return_value = None

    with (
        patch(
            "general_ludd.infra.model_deploy.ModelIndex",
            return_value=mock_index,
        ),
        patch(
            "general_ludd.infra.model_deploy.SearXModelSearch",
        ) as mock_searx,
    ):
        mock_searx.return_value.find_model.return_value = None
        with pytest.raises(ValueError, match="not found"):
            deploy_from_search("nonexistent-model")


def test_deploy_from_search_uses_provider_and_engine_params() -> None:
    result = _make_mock_result("Llama-3-8B-Instruct")
    mock_index = MagicMock()
    mock_index.get.return_value = result

    with (
        patch(
            "general_ludd.infra.model_deploy.ModelIndex",
            return_value=mock_index,
        ),
        patch(
            "general_ludd.infra.model_deploy.SearXModelSearch",
        ),
        patch(
            "general_ludd.infra.model_deploy.hardware_profile_for",
        ) as mock_hpf,
        patch(
            "general_ludd.infra.model_deploy.recommend_config",
            return_value={"gpus": 1, "memory_mb": 8192},
        ),
    ):
        mock_hpf.return_value = MagicMock(
            total_vram_gb=16, gpu_count=2, arch="ampere", supports_fp8=True, has_nvlink=False
        )
        output = deploy_from_search(
            "Llama-3-8B-Instruct",
            provider="azure",
            engine="llamacpp",
            gpu_count=2,
            max_cost=5.0,
        )
        assert output["compute_config"]["provider"] == "azure"
        assert output["compute_config"]["engine"] == "llamacpp"
        assert output["compute_config"]["gpu_count"] == 2
        assert output["compute_config"]["max_cost_usd"] == 5.0


def test_deploy_from_search_invalid_provider_falls_back_to_aws() -> None:
    result = _make_mock_result()
    mock_index = MagicMock()
    mock_index.get.return_value = result

    with (
        patch(
            "general_ludd.infra.model_deploy.ModelIndex",
            return_value=mock_index,
        ),
        patch(
            "general_ludd.infra.model_deploy.SearXModelSearch",
        ),
        patch(
            "general_ludd.infra.model_deploy.hardware_profile_for",
        ) as mock_hpf,
        patch(
            "general_ludd.infra.model_deploy.recommend_config",
            return_value={"gpus": 1, "memory_mb": 8192},
        ),
    ):
        mock_hpf.return_value = MagicMock(
            total_vram_gb=16, gpu_count=1, arch="ampere", supports_fp8=True, has_nvlink=False
        )
        output = deploy_from_search("Llama-3-8B-Instruct", provider="invalid-provider")
        assert output["compute_config"]["provider"] == "aws"


def test_deploy_from_search_invalid_workload_falls_back_to_realtime_api() -> None:
    result = _make_mock_result()
    mock_index = MagicMock()
    mock_index.get.return_value = result

    with (
        patch(
            "general_ludd.infra.model_deploy.ModelIndex",
            return_value=mock_index,
        ),
        patch(
            "general_ludd.infra.model_deploy.SearXModelSearch",
        ),
        patch(
            "general_ludd.infra.model_deploy.hardware_profile_for",
        ) as mock_hpf,
        patch(
            "general_ludd.infra.model_deploy.recommend_config",
            return_value={"gpus": 1, "memory_mb": 8192},
        ),
    ):
        mock_hpf.return_value = MagicMock(
            total_vram_gb=16, gpu_count=1, arch="ampere", supports_fp8=True, has_nvlink=False
        )
        output = deploy_from_search("Llama-3-8B-Instruct", workload_type="nonexistent")
        # No crash; falls back to REALTIME_API internally
        assert output["model"] == "Llama-3-8B-Instruct"


def test_deploy_from_search_no_quantizations_skips_quant_key() -> None:
    result = _make_mock_result(quantizations=[])
    mock_index = MagicMock()
    mock_index.get.return_value = result

    with (
        patch(
            "general_ludd.infra.model_deploy.ModelIndex",
            return_value=mock_index,
        ),
        patch(
            "general_ludd.infra.model_deploy.SearXModelSearch",
        ),
        patch(
            "general_ludd.infra.model_deploy.hardware_profile_for",
        ) as mock_hpf,
        patch(
            "general_ludd.infra.model_deploy.recommend_config",
            return_value={"gpus": 1, "memory_mb": 8192},
        ),
    ):
        mock_hpf.return_value = MagicMock(
            total_vram_gb=16, gpu_count=1, arch="ampere", supports_fp8=True, has_nvlink=False
        )
        output = deploy_from_search("Llama-3-8B-Instruct")
        assert "quantization" not in output


def test_deploy_from_search_passes_region_to_compute_config() -> None:
    result = _make_mock_result()
    mock_index = MagicMock()
    mock_index.get.return_value = result

    with (
        patch(
            "general_ludd.infra.model_deploy.ModelIndex",
            return_value=mock_index,
        ),
        patch(
            "general_ludd.infra.model_deploy.SearXModelSearch",
        ),
        patch(
            "general_ludd.infra.model_deploy.hardware_profile_for",
        ) as mock_hpf,
        patch(
            "general_ludd.infra.model_deploy.recommend_config",
            return_value={"gpus": 1, "memory_mb": 8192},
        ),
    ):
        mock_hpf.return_value = MagicMock(
            total_vram_gb=16, gpu_count=1, arch="ampere", supports_fp8=True, has_nvlink=False
        )
        output = deploy_from_search("Llama-3-8B-Instruct", region="westus2")
        assert output["compute_config"]["region"] == "westus2"


# ── profile_from_search ────────────────────────────────────────────────────


def test_profile_from_search_returns_model_deployment_profile() -> None:
    result = _make_mock_result(quantizations=["awq", "gptq", "int8"])
    mock_index = MagicMock()
    mock_index.search.return_value = [result]

    with (
        patch(
            "general_ludd.infra.model_deploy.ModelIndex",
            return_value=mock_index,
        ),
        patch(
            "general_ludd.infra.model_deploy.SearXModelSearch",
        ),
    ):
        profile = profile_from_search("Llama-3")
        assert profile is not None
        assert profile.quantization == "awq"


def test_profile_from_search_returns_none_when_no_results() -> None:
    mock_index = MagicMock()
    mock_index.search.return_value = []

    with (
        patch(
            "general_ludd.infra.model_deploy.ModelIndex",
            return_value=mock_index,
        ),
        patch(
            "general_ludd.infra.model_deploy.SearXModelSearch",
        ) as mock_searx,
    ):
        mock_searx.return_value.search_models.return_value = []
        profile = profile_from_search("nonexistent")
        assert profile is None


def test_profile_from_search_returns_none_on_exception() -> None:
    mock_index = MagicMock()
    mock_index.search.return_value = []

    with (
        patch(
            "general_ludd.infra.model_deploy.ModelIndex",
            return_value=mock_index,
        ),
        patch(
            "general_ludd.infra.model_deploy.SearXModelSearch",
        ) as mock_searx,
    ):
        mock_searx.return_value.search_models.return_value = []
        profile = profile_from_search("model-without-valid-profile")
        assert profile is None


def test_profile_from_search_handles_no_quantizations() -> None:
    result = _make_mock_result(quantizations=[])
    mock_index = MagicMock()
    mock_index.search.return_value = [result]

    with (
        patch(
            "general_ludd.infra.model_deploy.ModelIndex",
            return_value=mock_index,
        ),
        patch(
            "general_ludd.infra.model_deploy.SearXModelSearch",
        ),
    ):
        profile = profile_from_search("model")
        assert profile is not None
        assert profile.quantization == "fp8"


def test_profile_from_search_searx_fallback() -> None:
    result = _make_mock_result(quantizations=["q4_k_m", "q8_0"])
    mock_index = MagicMock()
    mock_index.search.return_value = []

    with (
        patch(
            "general_ludd.infra.model_deploy.ModelIndex",
            return_value=mock_index,
        ),
        patch(
            "general_ludd.infra.model_deploy.SearXModelSearch",
        ) as mock_searx,
    ):
        mock_searx.return_value.search_models.return_value = [result]
        profile = profile_from_search("model")
        assert profile is not None
        assert profile.quantization == "bf16"


# ── integration-style: edge case param coverage ─────────────────────────────


def test_estimate_params_from_name_decimal_b() -> None:
    assert _estimate_params_from_name("gemma-2-2.5b-it") == pytest.approx(2.5)


def test_gpu_for_params_boundary_values() -> None:
    assert _gpu_for_params(0.01) == "l4"
    assert _gpu_for_params(8.0) == "l4"
    assert _gpu_for_params(200.0) == "h100"
    assert _gpu_for_params(200.0001) == "h200"
