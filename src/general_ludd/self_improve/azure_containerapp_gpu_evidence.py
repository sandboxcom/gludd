"""Exact vLLM metric evidence for one Azure Container App response."""

from __future__ import annotations

import math

from prometheus_client.parser import text_string_to_metric_families

from general_ludd.self_improve.azure_backend import AzureCandidateResponse
from general_ludd.self_improve.azure_containerapp_transport import (
    HTTPClient,
    request_text,
)
from general_ludd.self_improve.azure_containerapp_transport_types import (
    VLLMRuntimeGPUEvidence,
)
from general_ludd.self_improve.model_candidates import (
    AzureContainerAppCandidateIdentity,
    BackendFailure,
    BackendInfrastructureError,
)

_MAX_METRICS_BYTES = 8 * 1024 * 1024
_MAX_METRIC_SAMPLES = 100_000
_METRICS_CONTENT_TYPES = frozenset({"text/plain", "application/openmetrics-text"})
_PROMPT_TOKENS = "vllm:prompt_tokens_total"
_GENERATION_TOKENS = "vllm:generation_tokens_total"
_SUCCESSFUL_REQUESTS = "vllm:request_success_total"
_ESTIMATED_FLOPS = "vllm:estimated_flops_per_gpu_total"
_REQUIRED_METRICS = frozenset(
    {_PROMPT_TOKENS, _GENERATION_TOKENS, _SUCCESSFUL_REQUESTS}
)
_SELECTED_METRICS = _REQUIRED_METRICS | {_ESTIMATED_FLOPS}


def _vllm_metric_totals(payload: str, model_name: str) -> dict[str, float]:
    """Parse only bounded exact-model counters through prometheus-client."""
    totals: dict[str, float] = {}
    sample_count = 0
    for family in text_string_to_metric_families(payload):
        for sample in family.samples:
            sample_count += 1
            if sample_count > _MAX_METRIC_SAMPLES:
                raise ValueError
            if sample.name not in _SELECTED_METRICS:
                continue
            if sample.labels.get("model_name") != model_name:
                raise ValueError
            value = float(sample.value)
            if not math.isfinite(value) or value < 0 or value > 1e30:
                raise ValueError
            totals[sample.name] = totals.get(sample.name, 0.0) + value
    if not _REQUIRED_METRICS.issubset(totals):
        raise ValueError
    return totals


def _exact_counter(totals: dict[str, float], name: str, expected: int) -> int:
    value = totals[name]
    if not value.is_integer() or int(value) != expected:
        raise ValueError
    return int(value)


def attest_vllm_runtime_gpu(
    client: HTTPClient,
    identity: AzureContainerAppCandidateIdentity,
    response: AzureCandidateResponse,
    *,
    timeout_seconds: float,
) -> VLLMRuntimeGPUEvidence:
    """Bind exact vLLM counters to one response from the selected app revision."""
    if not isinstance(response, AzureCandidateResponse) or (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, (int, float))
        or not 0.0 < float(timeout_seconds) <= 120.0
    ):
        raise ValueError("runtime GPU evidence limits are invalid")
    try:
        payload = request_text(
            client.get,
            "/metrics",
            timeout_seconds=float(timeout_seconds),
            maximum_bytes=_MAX_METRICS_BYTES,
            content_types=_METRICS_CONTENT_TYPES,
        )
        totals = _vllm_metric_totals(payload, identity.model_name)
        prompt_tokens = _exact_counter(totals, _PROMPT_TOKENS, response.input_tokens)
        generation_tokens = _exact_counter(
            totals,
            _GENERATION_TOKENS,
            response.output_tokens,
        )
        successful = totals[_SUCCESSFUL_REQUESTS]
        if not successful.is_integer() or not 1 <= int(successful) <= 10_000:
            raise ValueError
        return VLLMRuntimeGPUEvidence(
            candidate_digest=identity.identity_digest,
            prompt_tokens=prompt_tokens,
            generation_tokens=generation_tokens,
            successful_requests=int(successful),
            estimated_flops_per_gpu=totals.get(_ESTIMATED_FLOPS),
        )
    except BackendInfrastructureError:
        raise
    except Exception:
        raise BackendInfrastructureError(BackendFailure.INVALID_RESPONSE) from None


__all__ = ["attest_vllm_runtime_gpu"]
