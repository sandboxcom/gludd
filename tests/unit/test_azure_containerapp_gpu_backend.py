"""Focused contracts for the GPU-attested Azure candidate backend."""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
from typing import Any, cast

import pytest

from general_ludd.infra.azure_containerapp_gpu_backend import (
    _GPUAttestedBackend,
    _valid_gpu_evidence,
)
from general_ludd.infra.azure_containerapp_sdk import AzureGPUUtilizationEvidence
from general_ludd.self_improve.azure_backend import (
    AzureApprovedPrompt,
    AzureCandidateResponse,
)
from general_ludd.self_improve.azure_containerapp_transport_types import (
    ContainerAppGPUAttestationSource,
    ContainerAppTraceEvent,
    VLLMRuntimeGPUEvidence,
)
from general_ludd.self_improve.model_candidates import (
    AzureContainerAppCandidateIdentity,
    BackendFailure,
    BackendInfrastructureError,
)


def _identity() -> AzureContainerAppCandidateIdentity:
    return AzureContainerAppCandidateIdentity(
        endpoint=(
            "https://app.kindstone.eastus.azurecontainerapps.io"
        ),
        resource_id=(
            "/subscriptions/12345678-1234-1234-1234-123456789abc/"
            "resourceGroups/gludd-models-eastus/providers/"
            "Microsoft.App/containerApps/gludd-vllm-managed-abc123"
        ),
        revision_name="gludd-vllm-managed-abc123--0000001",
        image_digest="sha256:" + "b" * 64,
        model_name="Qwen/Qwen2.5-0.5B-Instruct",
        model_revision="c" * 40,
        workload_profile_type="Consumption-GPU-NC24-A100",
    )


def _prompt() -> AzureApprovedPrompt:
    return cast(
        AzureApprovedPrompt,
        SimpleNamespace(envelope_digest="c" * 64),
    )


def test_backend_releases_response_only_after_positive_exact_revision_proof() -> None:
    """A successful inference remains withheld until Monitor proves GPU use."""
    identity = _identity()
    response = AzureCandidateResponse("patch", 10, 3, 13)
    calls: list[str] = []
    traces: list[Any] = []

    class Backend:
        candidate_identity = identity

        def generate(self, *_args: object, **_kwargs: object) -> AzureCandidateResponse:
            calls.append("generate")
            return response

        def close(self) -> None:
            calls.append("backend.close")

    class Attestor:
        def attest(
            self, observed: AzureContainerAppCandidateIdentity
        ) -> AzureGPUUtilizationEvidence:
            assert observed is identity
            calls.append("attest")
            return AzureGPUUtilizationEvidence(
                metric_name="GpuUtilizationPercentage",
                maximum_percent=42.0,
                positive_sample_count=1,
                revision_name=identity.revision_name,
            )

    backend = _GPUAttestedBackend(Backend(), cast(Any, Attestor()), traces.append)

    assert backend.generate(_prompt(), max_output_tokens=64, timeout_seconds=30) is response
    assert calls == ["generate", "attest"]
    assert [trace.event for trace in traces] == [
        ContainerAppTraceEvent.GPU_ATTESTATION_STARTED,
        ContainerAppTraceEvent.GPU_ATTESTATION_SUCCEEDED,
    ]


def test_evidence_validation_rejects_zero_and_foreign_revision() -> None:
    """Ambiguous evidence cannot satisfy the fail-closed release boundary."""
    identity = _identity()
    valid = AzureGPUUtilizationEvidence(
        metric_name="GpuUtilizationPercentage",
        maximum_percent=1.0,
        positive_sample_count=1,
        revision_name=identity.revision_name,
    )

    assert _valid_gpu_evidence(valid, identity)
    assert not _valid_gpu_evidence(replace(valid, maximum_percent=0.0), identity)
    assert not _valid_gpu_evidence(
        replace(valid, revision_name="app--foreign"), identity
    )


def test_backend_prefers_exact_cuda_vllm_evidence_without_monitor_delay() -> None:
    """The exact endpoint counters release output without polling delayed Monitor."""
    identity = _identity()
    response = AzureCandidateResponse("patch", 10, 3, 13)
    calls: list[str] = []
    traces: list[Any] = []

    class Backend:
        candidate_identity = identity

        def generate(self, *_args: object, **_kwargs: object) -> AzureCandidateResponse:
            calls.append("generate")
            return response

        def attest_runtime_gpu(
            self,
            observed: AzureCandidateResponse,
            *,
            timeout_seconds: float,
        ) -> VLLMRuntimeGPUEvidence:
            assert observed is response
            assert timeout_seconds == 30.0
            calls.append("runtime")
            return VLLMRuntimeGPUEvidence(
                candidate_digest=identity.identity_digest,
                prompt_tokens=10,
                generation_tokens=3,
                successful_requests=1,
                estimated_flops_per_gpu=None,
            )

        def close(self) -> None:
            calls.append("backend.close")

    class Attestor:
        def attest(self, _identity: AzureContainerAppCandidateIdentity) -> object:
            raise AssertionError("Azure Monitor must not delay primary evidence")

    backend = _GPUAttestedBackend(Backend(), cast(Any, Attestor()), traces.append)

    assert backend.generate(_prompt(), max_output_tokens=64, timeout_seconds=30) is response
    assert calls == ["generate", "runtime"]
    assert traces[-1].event is ContainerAppTraceEvent.GPU_ATTESTATION_SUCCEEDED
    assert traces[-1].gpu_attestation_source is (
        ContainerAppGPUAttestationSource.STARTUP_CUDA_VLLM_METRICS
    )
    assert (traces[-1].gpu_prompt_tokens, traces[-1].gpu_generation_tokens) == (
        10,
        3,
    )


def test_backend_censors_malformed_attestation_and_never_releases_output() -> None:
    """Provider output stays private when an attestor violates its contract."""
    identity = _identity()

    class Backend:
        candidate_identity = identity

        def generate(self, *_args: object, **_kwargs: object) -> AzureCandidateResponse:
            return AzureCandidateResponse("provider-private-output", 1, 1, 2)

        def close(self) -> None:
            return None

    class Attestor:
        def attest(self, _identity: AzureContainerAppCandidateIdentity) -> object:
            return object()

    backend = _GPUAttestedBackend(
        Backend(),
        cast(Any, Attestor()),
        lambda _trace: None,
    )

    with pytest.raises(BackendInfrastructureError) as captured:
        backend.generate(_prompt(), max_output_tokens=8, timeout_seconds=30)

    assert captured.value.failure is BackendFailure.INVALID_RESPONSE
    assert "provider-private-output" not in str(captured.value)


def test_backend_rejects_incomplete_delegate_identity_and_callbacks() -> None:
    class WrongIdentityBackend:
        candidate_identity = object()

        def generate(self, *_args: object, **_kwargs: object) -> object:
            return object()

        def close(self) -> None:
            return None

    class ValidBackend(WrongIdentityBackend):
        candidate_identity = _identity()

    class Attestor:
        def attest(self, _identity: AzureContainerAppCandidateIdentity) -> object:
            return object()

    with pytest.raises(ValueError, match="closable"):
        _GPUAttestedBackend(cast(Any, object()), cast(Any, Attestor()), lambda _: None)
    with pytest.raises(ValueError, match="identity"):
        _GPUAttestedBackend(
            cast(Any, WrongIdentityBackend()),
            cast(Any, Attestor()),
            lambda _: None,
        )
    with pytest.raises(ValueError, match="callbacks"):
        _GPUAttestedBackend(
            cast(Any, ValidBackend()),
            cast(Any, object()),
            lambda _: None,
        )


@pytest.mark.parametrize(
    ("runtime_error", "expected_failure", "expected_reason"),
    (
        (
            BackendInfrastructureError(BackendFailure.UNAVAILABLE),
            BackendFailure.UNAVAILABLE,
            "runtime_evidence_unavailable",
        ),
        (
            RuntimeError("private runtime failure"),
            BackendFailure.INTERNAL,
            "runtime_evidence_internal",
        ),
    ),
)
def test_runtime_attestation_failures_are_typed_and_censored(
    runtime_error: Exception,
    expected_failure: BackendFailure,
    expected_reason: str,
) -> None:
    identity = _identity()
    traces: list[Any] = []

    class Backend:
        candidate_identity = identity

        def generate(self, *_args: object, **_kwargs: object) -> AzureCandidateResponse:
            return AzureCandidateResponse("private-response", 2, 1, 3)

        def attest_runtime_gpu(self, *_args: object, **_kwargs: object) -> object:
            raise runtime_error

        def close(self) -> None:
            return None

    class Attestor:
        def attest(self, _identity: AzureContainerAppCandidateIdentity) -> object:
            raise AssertionError("Monitor fallback must not run")

    backend = _GPUAttestedBackend(Backend(), cast(Any, Attestor()), traces.append)
    with pytest.raises(BackendInfrastructureError) as captured:
        backend.generate(_prompt(), max_output_tokens=8, timeout_seconds=30)

    assert captured.value.failure is expected_failure
    assert traces[-1].event is ContainerAppTraceEvent.GPU_ATTESTATION_FAILED
    assert traces[-1].reason == expected_reason
    assert "private" not in str(captured.value)


def test_invalid_runtime_evidence_never_releases_provider_output() -> None:
    identity = _identity()
    traces: list[Any] = []

    class Backend:
        candidate_identity = identity

        def generate(self, *_args: object, **_kwargs: object) -> AzureCandidateResponse:
            return AzureCandidateResponse("private-response", 2, 1, 3)

        def attest_runtime_gpu(self, *_args: object, **_kwargs: object) -> object:
            return object()

        def close(self) -> None:
            return None

    class Attestor:
        def attest(self, _identity: AzureContainerAppCandidateIdentity) -> object:
            raise AssertionError("Monitor fallback must not run")

    backend = _GPUAttestedBackend(Backend(), cast(Any, Attestor()), traces.append)
    with pytest.raises(BackendInfrastructureError) as captured:
        backend.generate(_prompt(), max_output_tokens=8, timeout_seconds=30)

    assert captured.value.failure is BackendFailure.INVALID_RESPONSE
    assert traces[-1].reason == "runtime_evidence_contract_invalid"
    assert "private-response" not in str(captured.value)
