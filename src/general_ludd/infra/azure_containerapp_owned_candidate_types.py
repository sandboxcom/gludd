"""Public, content-free contracts for the owned Azure candidate lifecycle."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class OwnedCandidateLifecycleError(RuntimeError):
    """Fixed-context failure that never retains provider or prompt content."""

    def __init__(self, operation: str) -> None:
        """Initialize a censored failure for one lifecycle operation."""
        super().__init__(f"owned Azure candidate lifecycle failed: {operation}")
        self.operation = operation


class OwnedCandidateLifecycleEvent(StrEnum):
    """Observable states for candidate acquisition and release."""

    ENVIRONMENT_ACQUIRE_STARTED = "environment_acquire_started"
    ENVIRONMENT_ACQUIRED = "environment_acquired"
    APP_PLAN_STARTED = "app_plan_started"
    APP_PLAN_AUDITED = "app_plan_audited"
    APP_PREFLIGHT_STARTED = "app_preflight_started"
    APP_PREFLIGHT_SUCCEEDED = "app_preflight_succeeded"
    APP_APPLY_STARTED = "app_apply_started"
    APP_APPLIED = "app_applied"
    BACKEND_ACQUIRED = "backend_acquired"
    BACKEND_CLOSE_STARTED = "backend_close_started"
    BACKEND_CLOSED = "backend_closed"
    APP_DESTROY_STARTED = "app_destroy_started"
    APP_DESTROYED = "app_destroyed"
    APP_ABSENCE_VERIFIED = "app_absence_verified"
    ENVIRONMENT_RELEASE_STARTED = "environment_release_started"
    ENVIRONMENT_RELEASED = "environment_released"
    RESOURCES_RELEASED = "resources_released"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class OwnedCandidateLifecycleTrace:
    """Content-free lifecycle evidence safe for logs and durable event stores."""

    event: OwnedCandidateLifecycleEvent
    operation_digest: str
    candidate_identity_digest: str | None = None


__all__ = (
    "OwnedCandidateLifecycleError",
    "OwnedCandidateLifecycleEvent",
    "OwnedCandidateLifecycleTrace",
)
