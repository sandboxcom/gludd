"""Per-task cost/quality routing-role weights for the adaptive router."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from general_ludd.routing_roles.roles import TaskRole

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


def __getattr__(name: str) -> Any:
    if name in _WEIGHT_LAZY:
        from general_ludd.routing_roles import weights

        return getattr(weights, name)
    if name in _POLICY_LAZY:
        from general_ludd.routing_roles import small_model_policy

        return getattr(small_model_policy, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
