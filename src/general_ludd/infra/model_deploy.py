"""Deploy models discovered via SearXNG search.

Finds model metadata via SearXNG, downloads the best available quantized
GGUF, then deploys via terraform through the existing DeploymentManager,
DeploymentOptimizer, and TerraformGenerator pipeline.
"""

from __future__ import annotations

import logging

from general_ludd.infra.compute import ComputeConfig, ComputeProvider, GPUType, InferenceEngine
from general_ludd.infra.deployment_optimizer import (
    ModelDeploymentProfile,
    ModelProfile,
    WorkloadType,
    hardware_profile_for,
    recommend_config,
)
from general_ludd.infra.model_search import ModelIndex, ModelSearchResult, SearXModelSearch

logger = logging.getLogger(__name__)

_DEFAULT_GPU_FOR_PARAMS: dict[tuple[float, float], str] = {
    (0, 8): "l4",
    (8, 15): "a10g",
    (15, 35): "a100_40",
    (35, 80): "a100_80",
    (80, 200): "h100",
    (200, float("inf")): "h200",
}


def _gpu_for_params(params_b: float) -> str:
    for (low, high), gpu in sorted(_DEFAULT_GPU_FOR_PARAMS.items()):
        if low < params_b <= high:
            return gpu
    return "t4"


def _search_result_to_profile(result: ModelSearchResult) -> ModelProfile:
    params_b = result.params_count or _estimate_params_from_name(result.name)
    num_kv_heads = _estimate_kv_heads(params_b)
    num_layers = _estimate_layers(params_b)
    head_dim = 128

    native_quant: str | None = None
    if "fp8" in [q.lower() for q in result.quantizations_available]:
        native_quant = "fp8"

    return ModelProfile(
        name=result.name,
        num_layers=num_layers,
        num_kv_heads=num_kv_heads,
        head_dim=head_dim,
        params_b=params_b,
        is_moe=("moe" in result.name.lower() or "mixtral" in result.name.lower()),
        native_quant=native_quant,
    )


def _estimate_params_from_name(name: str) -> float:
    import re

    m = re.search(r"(\d+\.?\d*)\s*[bB]", name)
    if m:
        return float(m.group(1))
    m = re.search(r"(\d+)[mM]\b", name)
    if m:
        return float(m.group(1)) / 1000.0
    return 7.0


def _estimate_layers(params_b: float) -> int:
    if params_b <= 3:
        return 24
    if params_b <= 8:
        return 32
    if params_b <= 15:
        return 40
    if params_b <= 35:
        return 60
    if params_b <= 80:
        return 80
    return 96


def _estimate_kv_heads(params_b: float) -> int:
    if params_b <= 8:
        return 8
    if params_b <= 35:
        return 8
    return 8


def _pick_best_quant(quants: list[str], engine: str) -> str:
    quants_lower = [q.lower() for q in quants]
    if engine == "llamacpp":
        for q in ("q4_k_m", "q5_k_m", "q6_k", "q8_0"):
            if q in quants_lower:
                return q
        return "q4_k_m"
    for q in ("awq", "gptq", "int8", "fp8", "bf16"):
        if q in quants_lower:
            return q
    return "bf16"


def deploy_from_search(
    model_name: str,
    provider: str = "aws",
    engine: str = "vllm",
    workload_type: str = "realtime_api",
    *,
    searx_url: str | None = None,
    region: str | None = None,
    gpu_count: int = 1,
    max_cost: float = 10.0,
) -> dict[str, object]:
    searcher = SearXModelSearch(base_url=searx_url)
    index = ModelIndex()

    cached = index.get(model_name)
    if cached is not None:
        result = cached
    else:
        result = searcher.find_model(model_name)
        if result is None:
            raise ValueError(f"Model {model_name!r} not found via SearXNG")
        index.put(result)

    assert result is not None  # guaranteed by the None check above
    profile = _search_result_to_profile(result)

    try:
        wt = WorkloadType(workload_type)
    except ValueError:
        wt = WorkloadType.REALTIME_API

    gpu_type_str = _gpu_for_params(profile.params_b)
    try:
        gpu_type = GPUType(gpu_type_str)
    except ValueError:
        gpu_type = GPUType.T4

    hw = hardware_profile_for(gpu_type_str, gpu_count=gpu_count)

    config = recommend_config(profile, hw, engine, workload_type=wt)

    try:
        prov = ComputeProvider(provider)
    except ValueError:
        prov = ComputeProvider.AWS

    compute_config = ComputeConfig(
        provider=prov,
        gpu_type=gpu_type,
        gpu_count=gpu_count,
        model_name=profile.name,
        engine=InferenceEngine(engine),
        deploy_type="vm",
        spot=True,
        max_cost_usd=max_cost,
        region=region,
    )

    if result.quantizations_available:
        best_quant = _pick_best_quant(result.quantizations_available, engine)
        config["quantization"] = best_quant

    return {
        "model": profile.name,
        "source_url": result.source_url,
        "download_urls": result.download_urls,
        "params_b": profile.params_b,
        "quantizations": result.quantizations_available,
        "recommended_config": config,
        "compute_config": {
            "provider": compute_config.provider.value,
            "gpu_type": compute_config.gpu_type.value,
            "gpu_count": compute_config.gpu_count,
            "engine": compute_config.engine.value,
            "region": compute_config.region,
            "max_cost_usd": compute_config.max_cost_usd,
            "spot": compute_config.spot,
        },
    }


def profile_from_search(query: str, searx_url: str | None = None) -> ModelDeploymentProfile | None:
    searcher = SearXModelSearch(base_url=searx_url)
    index = ModelIndex()

    cached = index.search(query)
    if cached:
        result = cached[0]
    else:
        results = searcher.search_models(query, source="huggingface")
        if not results:
            return None
        result = results[0]
        index.put(result)

    try:
        return ModelDeploymentProfile(
            quantization=_pick_best_quant(
                result.quantizations_available, "vllm"
            ) if result.quantizations_available else None,
        )
    except Exception:
        return None
