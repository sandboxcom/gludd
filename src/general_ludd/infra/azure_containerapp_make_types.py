"""Public errors and content-free events for Container App Terraform runtimes.

Legacy names remain exported for callers created before provisioning moved out of
Make.  Event provenance always identifies the actual Terraform execution layer.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

MAKE_TARGET = "azure-containerapp-terraform-phase"
TERRAFORM_TARGET = "terraform"


class AzureContainerAppMakeRuntimeError(RuntimeError):
    """Fixed-context refusal from one owned infrastructure phase."""

    def __init__(self, phase: str) -> None:
        """Initialize a censored failure for one named runtime phase."""
        super().__init__(f"Azure Container App runtime failed: {phase}")
        self.phase = phase


class MakeRuntimeState(StrEnum):
    """Content-free state for a direct Terraform phase."""

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
    target: str = TERRAFORM_TARGET


__all__ = (
    "MAKE_TARGET",
    "TERRAFORM_TARGET",
    "AzureContainerAppMakeRuntimeError",
    "MakeRuntimeEvent",
    "MakeRuntimeState",
)
