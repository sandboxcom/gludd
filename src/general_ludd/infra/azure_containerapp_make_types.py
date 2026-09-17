"""Public errors and content-free events for Container App Terraform runtimes.

Legacy names remain exported for callers created before provisioning moved out of
Make.  Event provenance always identifies the actual Terraform execution layer.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

MAKE_TARGET = "azure-containerapp-terraform-phase"
TERRAFORM_TARGET = "terraform"
AZURE_RESOURCE_MANAGER_TARGET = "azure-resource-manager"
LIVE_PROOF_RUNTIME_FAILURE_DETAILS = frozenset(
    {
        "runtime_app_name",
        "runtime_configuration",
        "runtime_credentials",
        "runtime_init",
        "runtime_materialize",
        "runtime_plan",
        "runtime_policy",
        "runtime_policy_drift",
        "runtime_show_plan",
        "runtime_sizing",
        "runtime_trace",
        "runtime_validate",
    }
)
_RUNTIME_PHASE_FAILURE_DETAILS = {
    "app-name": "runtime_app_name",
    "configuration": "runtime_configuration",
    "credentials": "runtime_credentials",
    "init": "runtime_init",
    "materialize": "runtime_materialize",
    "plan": "runtime_plan",
    "policy": "runtime_policy",
    "policy-drift": "runtime_policy_drift",
    "show-plan": "runtime_show_plan",
    "sizing": "runtime_sizing",
    "trace": "runtime_trace",
    "validate": "runtime_validate",
}


class AzureContainerAppMakeRuntimeError(RuntimeError):
    """Fixed-context refusal from one owned infrastructure phase."""

    def __init__(self, phase: str) -> None:
        """Initialize a censored failure for one named runtime phase."""
        super().__init__(f"Azure Container App runtime failed: {phase}")
        self.phase = phase


def live_proof_runtime_failure_detail(error: Exception) -> str:
    """Map a censored runtime phase to a fixed safe live-proof detail."""
    if isinstance(error, AzureContainerAppMakeRuntimeError):
        return _RUNTIME_PHASE_FAILURE_DETAILS.get(error.phase, "runtime_plan")
    return "runtime_plan"


class MakeRuntimeState(StrEnum):
    """Content-free state for a direct Terraform phase."""

    STARTED = "started"
    HEARTBEAT = "heartbeat"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


_AZURE_PROVISIONING_STATES = {
    "succeeded": ("succeeded", MakeRuntimeState.SUCCEEDED),
    "failed": ("failed", MakeRuntimeState.FAILED),
    "canceled": ("canceled", MakeRuntimeState.FAILED),
    "waiting": ("waiting", MakeRuntimeState.HEARTBEAT),
    "inprogress": ("in-progress", MakeRuntimeState.HEARTBEAT),
    "provisioning": ("provisioning", MakeRuntimeState.HEARTBEAT),
    "deleting": ("deleting", MakeRuntimeState.HEARTBEAT),
    "initializationinprogress": (
        "initialization-in-progress",
        MakeRuntimeState.HEARTBEAT,
    ),
    "infrastructuresetupinprogress": (
        "infrastructure-setup-in-progress",
        MakeRuntimeState.HEARTBEAT,
    ),
    "infrastructuresetupcomplete": (
        "infrastructure-setup-complete",
        MakeRuntimeState.HEARTBEAT,
    ),
    "scheduledfordelete": ("scheduled-for-delete", MakeRuntimeState.HEARTBEAT),
    "upgraderequested": ("upgrade-requested", MakeRuntimeState.HEARTBEAT),
    "upgradefailed": ("upgrade-failed", MakeRuntimeState.FAILED),
}


def azure_provisioning_fact(
    document: object,
) -> tuple[str, MakeRuntimeState] | None:
    """Extract one finite, non-identifying ARM provisioning-state fact."""
    if not isinstance(document, Mapping):
        return None
    properties = document.get("properties")
    if not isinstance(properties, Mapping):
        return None
    value = properties.get("provisioningState")
    if not isinstance(value, str):
        return None
    return _AZURE_PROVISIONING_STATES.get(value.casefold())


@dataclass(frozen=True, slots=True)
class MakeRuntimeEvent:
    """Content-free infrastructure progress safe for durable traces."""

    phase: str
    state: MakeRuntimeState
    operation_digest: str
    elapsed_seconds: int = 0
    target: str = TERRAFORM_TARGET
    event_source: str | None = None
    resource_type: str | None = None
    action: str | None = None
    event_kind: str | None = None
    provisioning_state: str | None = None


__all__ = (
    "AZURE_RESOURCE_MANAGER_TARGET",
    "LIVE_PROOF_RUNTIME_FAILURE_DETAILS",
    "MAKE_TARGET",
    "TERRAFORM_TARGET",
    "AzureContainerAppMakeRuntimeError",
    "MakeRuntimeEvent",
    "MakeRuntimeState",
    "azure_provisioning_fact",
    "live_proof_runtime_failure_detail",
)
