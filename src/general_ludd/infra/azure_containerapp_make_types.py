"""Public errors and content-free events for the Container App Make runtime."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

MAKE_TARGET = "azure-containerapp-terraform-phase"


class AzureContainerAppMakeRuntimeError(RuntimeError):
    """Fixed-context refusal from one Make-mediated infrastructure phase."""

    def __init__(self, phase: str) -> None:
        """Initialize a censored failure for one named runtime phase."""
        super().__init__(f"Azure Container App runtime failed: {phase}")
        self.phase = phase


class MakeRuntimeState(StrEnum):
    """Content-free state for a Make-mediated Terraform phase."""

    STARTED = "started"
    HEARTBEAT = "heartbeat"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class MakeRuntimeEvent:
    """Content-free infrastructure progress safe for durable traces."""

    phase: str
    state: MakeRuntimeState
    operation_digest: str
    elapsed_seconds: int = 0
    target: str = MAKE_TARGET


__all__ = (
    "MAKE_TARGET",
    "AzureContainerAppMakeRuntimeError",
    "MakeRuntimeEvent",
    "MakeRuntimeState",
)
