"""Shared, fail-closed operations for Azure environment lifecycles."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

from general_ludd.infra.azure_containerapp_environment_types import (
    AzureContainerAppEnvironmentRuntime,
    AzureEnvironmentLifecycleError,
    AzureEnvironmentLifecyclePolicy,
    AzureEnvironmentProfile,
    EnvironmentLifecycleEvent,
    EnvironmentLifecycleTrace,
)
from general_ludd.infra.azure_idle_retention import AzureIdleRetentionPlan

EnvironmentTraceSink = Callable[[EnvironmentLifecycleTrace], None]


def discard_environment_trace(_trace: EnvironmentLifecycleTrace) -> None:
    """Provide a side-effect-free default sink for optional lifecycle traces."""
    return None


def emit_environment_trace(
    sink: EnvironmentTraceSink,
    event: EnvironmentLifecycleEvent,
    policy: AzureEnvironmentLifecyclePolicy,
    *,
    active_app_count: int = 0,
    retention_plan: AzureIdleRetentionPlan | None = None,
    retention_seconds_remaining: int = 0,
    failure_reason: str | None = None,
) -> None:
    """Emit a bounded lifecycle event without leaking untrusted provider data."""
    try:
        sink(
            EnvironmentLifecycleTrace(
                event=event,
                operation_digest=policy.operation_digest,
                profile_count=len(policy.profiles),
                active_app_count=active_app_count,
                retention_plan_digest=(
                    None if retention_plan is None else retention_plan.plan_digest
                ),
                retention_seconds_remaining=retention_seconds_remaining,
                retention_hourly_cost_microusd=(
                    0
                    if retention_plan is None
                    else retention_plan.hourly_cost_microusd
                ),
                failure_reason=failure_reason,
            )
        )
    except Exception:
        raise AzureEnvironmentLifecycleError("trace") from None


def validate_environment_boundaries(
    policy: AzureEnvironmentLifecyclePolicy,
    runtime: AzureContainerAppEnvironmentRuntime,
    trace_sink: EnvironmentTraceSink,
) -> None:
    """Reject malformed lifecycle dependencies before any provider operation."""
    if not isinstance(policy, AzureEnvironmentLifecyclePolicy):
        raise ValueError("policy must be AzureEnvironmentLifecyclePolicy")
    if not isinstance(runtime, AzureContainerAppEnvironmentRuntime):
        raise ValueError("runtime must implement AzureContainerAppEnvironmentRuntime")
    if not callable(trace_sink):
        raise ValueError("trace_sink must be callable")


def read_environment(
    runtime: AzureContainerAppEnvironmentRuntime,
    policy: AzureEnvironmentLifecyclePolicy,
    *,
    expect_absent: bool,
    phase: str,
) -> object | None:
    """Convert provider read failures into a stable lifecycle phase failure."""
    try:
        return runtime.read_environment(policy, expect_absent=expect_absent)
    except Exception:
        raise AzureEnvironmentLifecycleError(phase) from None


def merged_environment_policy(
    policy: AzureEnvironmentLifecyclePolicy,
    existing_profiles: tuple[AzureEnvironmentProfile, ...],
) -> AzureEnvironmentLifecyclePolicy:
    """Preserve compatible existing profiles while adding desired profiles."""
    profiles = tuple(sorted(set(existing_profiles) | set(policy.profiles)))
    return replace(policy, profiles=profiles)


__all__ = [
    "EnvironmentTraceSink",
    "discard_environment_trace",
    "emit_environment_trace",
    "merged_environment_policy",
    "read_environment",
    "validate_environment_boundaries",
]
