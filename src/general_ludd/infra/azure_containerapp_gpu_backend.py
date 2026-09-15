"""Release Azure candidate output only after exact-revision GPU attestation."""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import Protocol

from general_ludd.infra.azure_containerapp_sdk import (
    AzureGPUMetricResponseReason,
    AzureGPUUtilizationAttestationError,
    AzureGPUUtilizationEvidence,
)
from general_ludd.self_improve.azure_backend import (
    AzureApprovedPrompt,
    AzureCandidateResponse,
)
from general_ludd.self_improve.azure_containerapp_backend import (
    ContainerAppBackendTrace,
)
from general_ludd.self_improve.azure_containerapp_transport import (
    emit_failure,
    emit_trace,
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


class _ClosableBackend(Protocol):
    @property
    def candidate_identity(self) -> AzureContainerAppCandidateIdentity: ...

    def generate(
        self,
        request: AzureApprovedPrompt,
        *,
        max_output_tokens: int,
        timeout_seconds: float,
    ) -> AzureCandidateResponse: ...

    def close(self) -> None: ...


class _GPUAttestor(Protocol):
    def attest(
        self,
        identity: AzureContainerAppCandidateIdentity,
    ) -> AzureGPUUtilizationEvidence: ...

    def close(self) -> None: ...


def _valid_gpu_evidence(
    evidence: object,
    identity: AzureContainerAppCandidateIdentity,
) -> bool:
    """Accept only positive, exact-revision, bounded utilization evidence."""
    return (
        isinstance(evidence, AzureGPUUtilizationEvidence)
        and evidence.metric_name == "GpuUtilizationPercentage"
        and evidence.revision_name == identity.revision_name
        and not isinstance(evidence.maximum_percent, bool)
        and isinstance(evidence.maximum_percent, (int, float))
        and math.isfinite(evidence.maximum_percent)
        and 0 < evidence.maximum_percent <= 100
        and not isinstance(evidence.positive_sample_count, bool)
        and isinstance(evidence.positive_sample_count, int)
        and 1 <= evidence.positive_sample_count <= 10_000
    )


def _valid_vllm_runtime_evidence(
    evidence: object,
    identity: AzureContainerAppCandidateIdentity,
    response: AzureCandidateResponse,
) -> bool:
    """Bind exact-model vLLM counters to the one withheld response."""
    flops = (
        evidence.estimated_flops_per_gpu
        if isinstance(evidence, VLLMRuntimeGPUEvidence)
        else None
    )
    return (
        isinstance(evidence, VLLMRuntimeGPUEvidence)
        and evidence.candidate_digest == identity.identity_digest
        and not isinstance(evidence.prompt_tokens, bool)
        and isinstance(evidence.prompt_tokens, int)
        and evidence.prompt_tokens == response.input_tokens
        and not isinstance(evidence.generation_tokens, bool)
        and isinstance(evidence.generation_tokens, int)
        and evidence.generation_tokens == response.output_tokens
        and not isinstance(evidence.successful_requests, bool)
        and isinstance(evidence.successful_requests, int)
        and 1 <= evidence.successful_requests <= 10_000
        and (
            flops is None
            or (
                not isinstance(flops, bool)
                and isinstance(flops, (int, float))
                and math.isfinite(flops)
                and 0 <= flops <= 1e30
            )
        )
    )


class _GPUAttestedBackend:
    """Withhold one validated Container App response until GPU use is proven."""

    def __init__(
        self,
        delegate: _ClosableBackend,
        attestor: _GPUAttestor,
        trace_sink: Callable[[ContainerAppBackendTrace], None],
    ) -> None:
        if not callable(getattr(delegate, "generate", None)) or not callable(
            getattr(delegate, "close", None)
        ):
            raise ValueError("delegate must be one closable candidate backend")
        identity = delegate.candidate_identity
        if not isinstance(identity, AzureContainerAppCandidateIdentity):
            raise ValueError("delegate must bind one Container App identity")
        if not callable(getattr(attestor, "attest", None)) or not callable(trace_sink):
            raise ValueError("GPU attestation callbacks must be callable")
        self._delegate = delegate
        self._attestor = attestor
        self._trace_sink = trace_sink
        self._identity = identity
        self._closed = False
        self._request_number = 0

    @property
    def candidate_identity(self) -> AzureContainerAppCandidateIdentity:
        return self._identity

    def _attestation_failed(
        self,
        failure: BackendFailure,
        request_number: int,
        envelope_digest: str | None,
        reason: str | None = None,
        http_status: int = 0,
        source: ContainerAppGPUAttestationSource | None = None,
    ) -> None:
        emit_failure(
            self._trace_sink,
            ContainerAppBackendTrace(
                ContainerAppTraceEvent.GPU_ATTESTATION_FAILED,
                candidate_digest=self._identity.identity_digest,
                envelope_digest=envelope_digest,
                request_number=request_number,
                failure=failure,
                reason=reason,
                http_status=http_status,
                gpu_attestation_source=source,
            ),
        )

    def generate(
        self,
        request: AzureApprovedPrompt,
        *,
        max_output_tokens: int,
        timeout_seconds: float,
    ) -> AzureCandidateResponse:
        """Run inference once, attest its exact revision, then release output."""
        if self._closed:
            raise BackendInfrastructureError(BackendFailure.UNAVAILABLE)
        response = self._delegate.generate(
            request,
            max_output_tokens=max_output_tokens,
            timeout_seconds=timeout_seconds,
        )
        self._request_number += 1
        request_number = self._request_number
        envelope_digest = (
            request.envelope_digest if isinstance(request, AzureApprovedPrompt) else None
        )
        runtime_attestor = getattr(self._delegate, "attest_runtime_gpu", None)
        source = (
            ContainerAppGPUAttestationSource.STARTUP_CUDA_VLLM_METRICS
            if callable(runtime_attestor)
            else ContainerAppGPUAttestationSource.AZURE_MONITOR
        )
        emit_trace(
            self._trace_sink,
            ContainerAppBackendTrace(
                ContainerAppTraceEvent.GPU_ATTESTATION_STARTED,
                candidate_digest=self._identity.identity_digest,
                envelope_digest=envelope_digest,
                request_number=request_number,
                gpu_attestation_source=source,
            ),
        )
        if callable(runtime_attestor):
            try:
                runtime_evidence = runtime_attestor(
                    response,
                    timeout_seconds=min(float(timeout_seconds), 120.0),
                )
            except BackendInfrastructureError as error:
                self._attestation_failed(
                    error.failure,
                    request_number,
                    envelope_digest,
                    "runtime_evidence_unavailable",
                    source=source,
                )
                raise BackendInfrastructureError(error.failure) from None
            except Exception:
                failure = BackendFailure.INTERNAL
                self._attestation_failed(
                    failure,
                    request_number,
                    envelope_digest,
                    "runtime_evidence_internal",
                    source=source,
                )
                raise BackendInfrastructureError(failure) from None
            if not _valid_vllm_runtime_evidence(
                runtime_evidence,
                self._identity,
                response,
            ):
                failure = BackendFailure.INVALID_RESPONSE
                self._attestation_failed(
                    failure,
                    request_number,
                    envelope_digest,
                    "runtime_evidence_contract_invalid",
                    source=source,
                )
                raise BackendInfrastructureError(failure)
            flops = runtime_evidence.estimated_flops_per_gpu
            emit_trace(
                self._trace_sink,
                ContainerAppBackendTrace(
                    ContainerAppTraceEvent.GPU_ATTESTATION_SUCCEEDED,
                    candidate_digest=self._identity.identity_digest,
                    envelope_digest=envelope_digest,
                    request_number=request_number,
                    gpu_attestation_source=source,
                    gpu_prompt_tokens=runtime_evidence.prompt_tokens,
                    gpu_generation_tokens=runtime_evidence.generation_tokens,
                    gpu_successful_requests=runtime_evidence.successful_requests,
                    gpu_estimated_flops_per_gpu=(
                        0.0 if flops is None else float(flops)
                    ),
                ),
            )
            return response
        try:
            evidence = self._attestor.attest(self._identity)
        except BackendInfrastructureError as error:
            reason = (
                error.reason
                if isinstance(error, AzureGPUUtilizationAttestationError)
                else None
            )
            http_status = (
                error.http_status
                if isinstance(error, AzureGPUUtilizationAttestationError)
                else 0
            )
            self._attestation_failed(
                error.failure,
                request_number,
                envelope_digest,
                reason,
                http_status,
                source,
            )
            raise BackendInfrastructureError(error.failure) from None
        except Exception:
            failure = BackendFailure.INTERNAL
            self._attestation_failed(
                failure,
                request_number,
                envelope_digest,
                source=source,
            )
            raise BackendInfrastructureError(failure) from None
        if not _valid_gpu_evidence(evidence, self._identity):
            failure = BackendFailure.INVALID_RESPONSE
            self._attestation_failed(
                failure,
                request_number,
                envelope_digest,
                AzureGPUMetricResponseReason.EVIDENCE_CONTRACT_INVALID.value,
                source=source,
            )
            raise BackendInfrastructureError(failure)
        emit_trace(
            self._trace_sink,
            ContainerAppBackendTrace(
                ContainerAppTraceEvent.GPU_ATTESTATION_SUCCEEDED,
                candidate_digest=self._identity.identity_digest,
                envelope_digest=envelope_digest,
                request_number=request_number,
                gpu_maximum_percent=float(evidence.maximum_percent),
                gpu_positive_sample_count=evidence.positive_sample_count,
                gpu_attestation_source=source,
            ),
        )
        return response

    def close(self) -> None:
        """Idempotently release the underlying inference transport."""
        if self._closed:
            return
        self._closed = True
        try:
            self._delegate.close()
        except BackendInfrastructureError:
            raise
        except Exception:
            raise BackendInfrastructureError(BackendFailure.INTERNAL) from None


__all__ = (
    "_ClosableBackend",
    "_GPUAttestedBackend",
    "_GPUAttestor",
    "_valid_gpu_evidence",
    "_valid_vllm_runtime_evidence",
)
