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
    ContainerAppTraceEvent,
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
