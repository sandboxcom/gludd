"""Translate one verified model/profile pair into an immutable compute plan."""

from __future__ import annotations

from collections.abc import Callable

from general_ludd.infra.azure_containerapp_gpu import (
    A100_PROFILE,
    T4_PROFILE,
    AzureContainerAppGPUUnavailable,
    ModelServingRequirement,
    select_smallest_sufficient_profile,
)
from general_ludd.infra.azure_containerapp_live_types import (
    AzureContainerAppLiveProofPolicy,
)
from general_ludd.infra.azure_containerapp_make_types import (
    AzureContainerAppMakeRuntimeError,
)
from general_ludd.infra.compute import (
    ComputeConfig,
    ComputeProvider,
    GPUType,
    InferenceEngine,
)

_PROFILE_FACTS = {
    T4_PROFILE.workload_profile_type: (T4_PROFILE, GPUType.T4),
    A100_PROFILE.workload_profile_type: (A100_PROFILE, GPUType.A100_80),
}
_MIN_VLLM_CONTEXT_TOKENS = 512
_MAX_VLLM_CONTEXT_TOKENS = 32_768


def build_containerapp_compute_config(
    policy: AzureContainerAppLiveProofPolicy,
    requirement: ModelServingRequirement,
    *,
    config_factory: Callable[..., ComputeConfig] = ComputeConfig,
) -> ComputeConfig:
    """Build a pinned vLLM configuration or return only a safe failure phase."""
    try:
        profile, gpu_type = _PROFILE_FACTS[policy.workload_profile_type]
    except KeyError:
        raise AzureContainerAppMakeRuntimeError("sizing") from None
    if (
        requirement.model_id != policy.model_name
        or requirement.revision != policy.model_revision
        or profile.workload_profile_name != policy.workload_profile_name
    ):
        raise AzureContainerAppMakeRuntimeError("sizing")
    try:
        select_smallest_sufficient_profile(
            requirement,
            hardware_profiles=(profile,),
        )
    except AzureContainerAppGPUUnavailable:
        raise AzureContainerAppMakeRuntimeError("sizing") from None
    context_length = max(
        _MIN_VLLM_CONTEXT_TOKENS,
        policy.call_budget.max_total_tokens,
    )
    if context_length > _MAX_VLLM_CONTEXT_TOKENS:
        raise AzureContainerAppMakeRuntimeError("sizing")
    try:
        return config_factory(
            provider=ComputeProvider.AZURE,
            gpu_type=gpu_type,
            gpu_count=1,
            engine=InferenceEngine.VLLM,
            model_name=policy.model_name,
            region=policy.location,
            spot=False,
            max_cost_usd=policy.max_cost_usd,
            timeout_minutes=float(policy.ttl_minutes),
            container_image=policy.container_image,
            model_revision=policy.model_revision,
            azure_subscription_id=policy.subscription_id,
            azure_resource_group=policy.resource_group,
            azure_containerapp_environment=policy.environment_name,
            azure_workload_profile_name=policy.workload_profile_name,
            azure_min_replicas=policy.min_replicas,
            azure_max_replicas=policy.max_replicas,
            azure_http_concurrent_requests=policy.http_concurrent_requests,
            deploy_type="containerapp",
            allowed_cidr=policy.allowed_cidr,
            deployment_profile={
                "context_length": context_length,
                "max_num_seqs": 1,
                "gpu_memory_utilization": 0.9,
                "enforce_eager": False,
                "enable_prefix_caching": True,
                "enable_chunked_prefill": True,
                "kv_cache_dtype": "auto",
                "quantization": "",
            },
        )
    except Exception:
        raise AzureContainerAppMakeRuntimeError("configuration") from None


__all__ = ("build_containerapp_compute_config",)
