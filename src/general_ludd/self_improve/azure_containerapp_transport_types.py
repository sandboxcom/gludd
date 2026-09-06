"""Immutable trace and accounting values for the Container App transport."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from general_ludd.self_improve.model_candidates import BackendFailure


class ContainerAppTraceEvent(StrEnum):
    """Content-free backend transitions for durable tracing."""

    DISCOVERY_STARTED = "azure_containerapp_discovery_started"
    DISCOVERY_SUCCEEDED = "azure_containerapp_discovery_succeeded"
    DISCOVERY_FAILED = "azure_containerapp_discovery_failed"
    IDENTITY_DRIFT = "azure_containerapp_identity_drift"
    APPROVAL_BLOCKED = "azure_containerapp_approval_blocked"
    REQUEST_STARTED = "azure_containerapp_request_started"
    RESPONSE_ACCEPTED = "azure_containerapp_response_accepted"
    REQUEST_FAILED = "azure_containerapp_request_failed"


@dataclass(frozen=True, slots=True)
class ContainerAppBackendTrace:
    """Request-, response-, endpoint-, and credential-free trace evidence."""

    event: ContainerAppTraceEvent
    candidate_digest: str | None = None
    request_number: int = 0
    failure: BackendFailure | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0


@dataclass(frozen=True, slots=True)
class ContainerAppBackendAccounting:
    """Cumulative provider call and token counters."""

    requests_started: int = 0
    responses_received: int = 0
    responses_accepted: int = 0
    requests_failed: int = 0
    provider_input_tokens: int = 0
    provider_output_tokens: int = 0
    provider_total_tokens: int = 0


__all__ = (
    "ContainerAppBackendAccounting",
    "ContainerAppBackendTrace",
    "ContainerAppTraceEvent",
)
