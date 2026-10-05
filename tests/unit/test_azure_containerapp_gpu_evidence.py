"""Direct contracts for exact vLLM GPU-runtime evidence."""

from __future__ import annotations

from typing import cast

import pytest

from general_ludd.self_improve.azure_backend import AzureCandidateResponse
from general_ludd.self_improve.azure_containerapp_gpu_evidence import (
    attest_vllm_runtime_gpu,
)
from general_ludd.self_improve.azure_containerapp_transport import HTTPClient
from general_ludd.self_improve.model_candidates import (
    AzureContainerAppCandidateIdentity,
)


class _Response:
    def __init__(self) -> None:
        self.status_code = 200
        self.headers = {"content-type": "text/plain"}
        self.content = (
            b'vllm:prompt_tokens_total{model_name="trusted/model"} 11\n'
            b'vllm:generation_tokens_total{model_name="trusted/model"} 7\n'
            b'vllm:request_success_total{model_name="trusted/model"} 1\n'
        )


class _Client:
    def get(
        self,
        path: str,
        *,
        timeout: float,
        follow_redirects: bool,
    ) -> _Response:
        assert (path, timeout, follow_redirects) == ("/metrics", 4.0, False)
        return _Response()


def _identity() -> AzureContainerAppCandidateIdentity:
    return AzureContainerAppCandidateIdentity(
        endpoint="https://trusted.westus.azurecontainerapps.io",
        resource_id=(
            "/subscriptions/11111111-2222-3333-4444-555555555555/"
            "resourceGroups/gludd/providers/Microsoft.App/containerApps/proof"
        ),
        revision_name="proof--0000001",
        image_digest="sha256:" + "a" * 64,
        model_name="trusted/model",
        model_revision="b" * 40,
        workload_profile_type="Consumption-GPU-NC8as-T4",
    )


def test_direct_gpu_evidence_binds_identity_tokens_and_success_count() -> None:
    evidence = attest_vllm_runtime_gpu(
        cast(HTTPClient, _Client()),
        _identity(),
        AzureCandidateResponse("private", 11, 7, 18),
        timeout_seconds=4.0,
    )

    assert evidence.candidate_digest == _identity().identity_digest
    assert (evidence.prompt_tokens, evidence.generation_tokens) == (11, 7)
    assert evidence.successful_requests == 1
    assert evidence.estimated_flops_per_gpu is None


@pytest.mark.parametrize("timeout", [True, 0, 121])
def test_direct_gpu_evidence_rejects_invalid_limits_before_http(timeout: object) -> None:
    with pytest.raises(ValueError, match="runtime GPU evidence limits"):
        attest_vllm_runtime_gpu(
            cast(HTTPClient, _Client()),
            _identity(),
            AzureCandidateResponse("private", 11, 7, 18),
            timeout_seconds=cast(float, timeout),
        )
