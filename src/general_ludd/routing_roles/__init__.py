"""Per-task cost/quality routing-role weights for the adaptive router."""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

from general_ludd.routing_roles.roles import TaskRole

# ``importlib.reload`` re-executes a module without clearing its dictionary.
# Drop cached exports and import-created submodule attributes before rebuilding
# the lazy contract so a reload behaves exactly like a fresh import.
for _stale_lazy_name in (
    "CapabilityEvidence",
    "CompletionAction",
    "CompletionEvidence",
    "DispatchAction",
    "ModelIdentity",
    "PolicyConfig",
    "RoleWeights",
    "SmallModelTaskPolicy",
    "SmallModelTaskSpec",
    "TaskContract",
    "TaskImpact",
    "small_model_policy",
    "task_weights",
    "weights",
    "weights_for",
):
    globals().pop(_stale_lazy_name, None)
del _stale_lazy_name

if TYPE_CHECKING:
    from general_ludd.routing_roles.small_model_policy import (
        CapabilityEvidence,
        CompletionAction,
        CompletionEvidence,
        DispatchAction,
        ModelIdentity,
        PolicyConfig,
        SmallModelTaskPolicy,
        SmallModelTaskSpec,
        TaskContract,
        TaskImpact,
    )
    from general_ludd.routing_roles.weights import (
        RoleWeights,
        task_weights,
        weights_for,
    )

# `weights` is imported lazily (PEP 562 __getattr__) rather than at package-init
# time. Eagerly importing it here created an import cycle: schemas.benchmark
# imports routing_roles.roles, which forces this __init__ to run, which imported
# weights, which imports TaskType back from the half-initialized schemas.benchmark
# -> ImportError. Deferring the weights import until first attribute access keeps
# `routing_roles.RoleWeights` / `task_weights` / `weights_for` available without
# the cycle.
__all__ = [
    "CapabilityEvidence",
    "CompletionAction",
    "CompletionEvidence",
    "DispatchAction",
    "ModelIdentity",
    "PolicyConfig",
    "RoleWeights",
    "SmallModelTaskPolicy",
    "SmallModelTaskSpec",
    "TaskContract",
    "TaskImpact",
    "TaskRole",
    "task_weights",
    "weights_for",
]

_WEIGHT_LAZY = {"RoleWeights", "task_weights", "weights_for"}
_POLICY_LAZY = {
    "CapabilityEvidence",
    "CompletionAction",
    "CompletionEvidence",
    "DispatchAction",
    "ModelIdentity",
    "PolicyConfig",
    "SmallModelTaskPolicy",
    "SmallModelTaskSpec",
    "TaskContract",
    "TaskImpact",
}


def _load_lazy_group(module_path: str, names: set[str], requested: str) -> Any:
    """Import and cache one complete export group deterministically."""
    module = import_module(module_path)
    namespace = globals()
    for export_name in sorted(names):
        namespace[export_name] = getattr(module, export_name)
    return namespace[requested]


def __getattr__(name: str) -> Any:
    if name in _WEIGHT_LAZY:
        return _load_lazy_group(
            "general_ludd.routing_roles.weights",
            _WEIGHT_LAZY,
            name,
        )
    if name in _POLICY_LAZY:
        return _load_lazy_group(
            "general_ludd.routing_roles.small_model_policy",
            _POLICY_LAZY,
            name,
        )
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
