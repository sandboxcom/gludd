"""Content-free telemetry contracts for the FreeLLMAPI workload adapter."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from general_ludd.self_improve.model_candidates import BackendFailure


class FreeLLMAPITraceEvent(StrEnum):
    """Transitions for one explicitly admitted catalog trial."""

    IDENTITY_DRIFT = "freellmapi_identity_drift"
    APPROVAL_BLOCKED = "freellmapi_approval_blocked"
    REQUEST_STARTED = "freellmapi_request_started"
    RESPONSE_ACCEPTED = "freellmapi_response_accepted"
    REQUEST_FAILED = "freellmapi_request_failed"


@dataclass(frozen=True, slots=True)
class FreeLLMAPIBackendTrace:
    """One content-free native-gateway backend transition."""

    event: FreeLLMAPITraceEvent
    candidate_digest: str
    envelope_digest: str | None = None
    request_number: int = 0
    failure: BackendFailure | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0


__all__ = ("FreeLLMAPIBackendTrace", "FreeLLMAPITraceEvent")
