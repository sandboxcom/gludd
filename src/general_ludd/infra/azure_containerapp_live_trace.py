"""Content-free trace construction and publication for Container App proofs."""

from __future__ import annotations

from collections.abc import Callable

from general_ludd.infra.azure_containerapp_live_types import (
    AzureContainerAppLiveProofError,
    AzureContainerAppLiveProofFailure,
    AzureContainerAppLiveProofPolicy,
    LiveProofEvent,
    LiveProofTrace,
)
from general_ludd.self_improve.azure_backend import AzureCandidateResponse


def emit_live_proof_trace(
    sink: Callable[[LiveProofTrace], None],
    trace: LiveProofTrace,
) -> None:
    """Publish one trace or convert sink failure to a censored proof error."""
    try:
        sink(trace)
    except Exception:
        raise AzureContainerAppLiveProofError(
            AzureContainerAppLiveProofFailure.TRACE
        ) from None


def cleanup_emit_live_proof_trace(
    sink: Callable[[LiveProofTrace], None],
    trace: LiveProofTrace,
) -> bool:
    """Publish cleanup evidence and report failure without masking cleanup."""
    try:
        emit_live_proof_trace(sink, trace)
    except AzureContainerAppLiveProofError:
        return False
    return True


def build_live_proof_trace(
    event: LiveProofEvent,
    policy: AzureContainerAppLiveProofPolicy,
    *,
    candidate_digest: str | None = None,
    failure: AzureContainerAppLiveProofFailure | None = None,
    resource_change_count: int = 0,
    response: AzureCandidateResponse | None = None,
) -> LiveProofTrace:
    """Build content-free lifecycle evidence with optional token accounting."""
    return LiveProofTrace(
        event=event,
        operation_digest=policy.operation_digest,
        candidate_identity_digest=candidate_digest,
        failure=failure,
        resource_change_count=resource_change_count,
        input_tokens=0 if response is None else response.input_tokens,
        output_tokens=0 if response is None else response.output_tokens,
        total_tokens=0 if response is None else response.total_tokens,
    )


def discard_live_proof_trace(_trace: LiveProofTrace) -> None:
    """Accept one trace for callers that deliberately need no persistence."""
    return None


__all__ = (
    "build_live_proof_trace",
    "cleanup_emit_live_proof_trace",
    "discard_live_proof_trace",
    "emit_live_proof_trace",
)
