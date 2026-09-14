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
    ContainerAppTraceEvent,
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
        emit_trace(
            self._trace_sink,
            ContainerAppBackendTrace(
                ContainerAppTraceEvent.GPU_ATTESTATION_STARTED,
                candidate_digest=self._identity.identity_digest,
                envelope_digest=envelope_digest,
                request_number=request_number,
            ),
        )
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
            )
            raise BackendInfrastructureError(error.failure) from None
        except Exception:
            failure = BackendFailure.INTERNAL
            self._attestation_failed(failure, request_number, envelope_digest)
            raise BackendInfrastructureError(failure) from None
        if not _valid_gpu_evidence(evidence, self._identity):
            failure = BackendFailure.INVALID_RESPONSE
            self._attestation_failed(
                failure,
                request_number,
                envelope_digest,
                AzureGPUMetricResponseReason.EVIDENCE_CONTRACT_INVALID.value,
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
)
