"""Ownership-safe Terraform lifecycle for Azure Container Apps environments.

Terraform is the sole mutation authority. Independent ARM reads establish every
precondition and postcondition without becoming a second infrastructure writer.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import replace

from general_ludd.infra.azure_containerapp_environment_types import (
    AzureContainerAppEnvironmentRuntime,
    AzureEnvironmentLifecycleError,
    AzureEnvironmentLifecyclePolicy,
    AzureEnvironmentLifecycleResult,
    AzureEnvironmentProfile,
    EnvironmentLifecycleDisposition,
    EnvironmentLifecycleEvent,
    EnvironmentLifecycleTrace,
)
from general_ludd.infra.azure_containerapp_environment_validation import (
    audit_environment_plan,
    validated_environment_profiles,
)

_APP_NAME_PATTERN = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,30}[a-z0-9])?")
_TraceSink = Callable[[EnvironmentLifecycleTrace], None]


def _discard_trace(_trace: EnvironmentLifecycleTrace) -> None:
    return None


def _emit(
    sink: _TraceSink,
    event: EnvironmentLifecycleEvent,
    policy: AzureEnvironmentLifecyclePolicy,
    *,
    active_app_count: int = 0,
) -> None:
    try:
        sink(
            EnvironmentLifecycleTrace(
                event=event,
                operation_digest=policy.operation_digest,
                profile_count=len(policy.profiles),
                active_app_count=active_app_count,
            )
        )
    except Exception:
        raise AzureEnvironmentLifecycleError("trace") from None


def _validated_boundaries(
    policy: AzureEnvironmentLifecyclePolicy,
    runtime: AzureContainerAppEnvironmentRuntime,
    trace_sink: _TraceSink,
) -> None:
    if not isinstance(policy, AzureEnvironmentLifecyclePolicy):
        raise ValueError("policy must be AzureEnvironmentLifecyclePolicy")
    if not isinstance(runtime, AzureContainerAppEnvironmentRuntime):
        raise ValueError("runtime must implement AzureContainerAppEnvironmentRuntime")
    if not callable(trace_sink):
        raise ValueError("trace_sink must be callable")


def _read_environment(
    runtime: AzureContainerAppEnvironmentRuntime,
    policy: AzureEnvironmentLifecyclePolicy,
    *,
    expect_absent: bool,
    phase: str,
) -> object | None:
    try:
        return runtime.read_environment(policy, expect_absent=expect_absent)
    except Exception:
        raise AzureEnvironmentLifecycleError(phase) from None


def _merged_policy(
    policy: AzureEnvironmentLifecyclePolicy,
    existing_profiles: tuple[AzureEnvironmentProfile, ...],
) -> AzureEnvironmentLifecyclePolicy:
    profiles = tuple(sorted(set(existing_profiles) | set(policy.profiles)))
    return replace(policy, profiles=profiles)


def _recover_new_environment(
    policy: AzureEnvironmentLifecyclePolicy,
    runtime: AzureContainerAppEnvironmentRuntime,
) -> bool:
    """Best-effort recovery for a create that may have partially succeeded."""
    try:
        result = release_azure_containerapp_environment(
            policy,
            runtime=runtime,
            trace_sink=_discard_trace,
        )
        return result.disposition in {
            EnvironmentLifecycleDisposition.ABSENT,
            EnvironmentLifecycleDisposition.DESTROYED,
        }
    except Exception:
        return False


def _cleanup_failed_create(
    policy: AzureEnvironmentLifecyclePolicy,
    runtime: AzureContainerAppEnvironmentRuntime,
    *,
    existed_before: bool,
    failure_phase: str,
) -> None:
    if not existed_before and not _recover_new_environment(policy, runtime):
        raise AzureEnvironmentLifecycleError("cleanup") from None
    raise AzureEnvironmentLifecycleError(failure_phase) from None


def ensure_azure_containerapp_environment(
    policy: AzureEnvironmentLifecyclePolicy,
    *,
    runtime: AzureContainerAppEnvironmentRuntime,
    trace_sink: _TraceSink = _discard_trace,
) -> AzureEnvironmentLifecycleResult:
    """Create or reconcile an owned environment through an audited Terraform plan."""
    _validated_boundaries(policy, runtime, trace_sink)
    _emit(trace_sink, EnvironmentLifecycleEvent.INSPECTION_STARTED, policy)
    existing = _read_environment(
        runtime,
        policy,
        expect_absent=False,
        phase="inspection",
    )
    existed_before = existing is not None
    effective_policy = policy
    if existing is None:
        _emit(trace_sink, EnvironmentLifecycleEvent.ENVIRONMENT_ABSENT, policy)
    else:
        existing_profiles = validated_environment_profiles(
            existing,
            policy,
            require_desired_profiles=False,
            require_current_tags=False,
        )
        effective_policy = _merged_policy(policy, existing_profiles)
        _emit(trace_sink, EnvironmentLifecycleEvent.OWNERSHIP_VERIFIED, effective_policy)

    _emit(trace_sink, EnvironmentLifecycleEvent.PLAN_STARTED, effective_policy)
    try:
        plan = runtime.plan(effective_policy)
        changed = audit_environment_plan(
            plan,
            effective_policy,
            existed_before=existed_before,
        )
    except AzureEnvironmentLifecycleError:
        raise
    except Exception:
        raise AzureEnvironmentLifecycleError("plan") from None
    _emit(trace_sink, EnvironmentLifecycleEvent.PLAN_AUDITED, effective_policy)

    _emit(trace_sink, EnvironmentLifecycleEvent.APPLY_STARTED, effective_policy)
    try:
        runtime.apply(effective_policy)
    except Exception:
        _cleanup_failed_create(
            effective_policy,
            runtime,
            existed_before=existed_before,
            failure_phase="apply",
        )
    _emit(trace_sink, EnvironmentLifecycleEvent.APPLY_SUCCEEDED, effective_policy)

    verified = _read_environment(
        runtime,
        effective_policy,
        expect_absent=False,
        phase="readiness",
    )
    try:
        if verified is None:
            raise AzureEnvironmentLifecycleError("ownership")
        validated_environment_profiles(
            verified,
            effective_policy,
            require_desired_profiles=True,
            require_current_tags=True,
        )
    except AzureEnvironmentLifecycleError:
        _cleanup_failed_create(
            effective_policy,
            runtime,
            existed_before=existed_before,
            failure_phase="readiness",
        )
    _emit(trace_sink, EnvironmentLifecycleEvent.READINESS_VERIFIED, effective_policy)

    if not existed_before:
        disposition = EnvironmentLifecycleDisposition.CREATED
    elif changed:
        disposition = EnvironmentLifecycleDisposition.RECONCILED
    else:
        disposition = EnvironmentLifecycleDisposition.REUSED
    return AzureEnvironmentLifecycleResult(
        disposition=disposition,
        environment_id=effective_policy.environment_id,
        effective_profiles=effective_policy.profiles,
    )


def _validated_app_inventory(
    inventory: object,
    policy: AzureEnvironmentLifecyclePolicy,
) -> tuple[str, ...]:
    if not isinstance(inventory, tuple) or not all(
        isinstance(resource_id, str) for resource_id in inventory
    ):
        raise AzureEnvironmentLifecycleError("inventory")
    prefix = f"{policy.resource_group_id}/providers/Microsoft.App/containerApps/"
    folded_prefix = prefix.casefold()
    for resource_id in inventory:
        if not resource_id.casefold().startswith(folded_prefix):
            raise AzureEnvironmentLifecycleError("inventory")
        if _APP_NAME_PATTERN.fullmatch(resource_id[len(prefix) :]) is None:
            raise AzureEnvironmentLifecycleError("inventory")
    return inventory


def _retained_result(
    policy: AzureEnvironmentLifecyclePolicy,
    *,
    active_app_count: int = 0,
) -> AzureEnvironmentLifecycleResult:
    return AzureEnvironmentLifecycleResult(
        disposition=EnvironmentLifecycleDisposition.RETAINED,
        environment_id=policy.environment_id,
        effective_profiles=policy.profiles,
        active_app_count=active_app_count,
    )


def release_azure_containerapp_environment(
    policy: AzureEnvironmentLifecyclePolicy,
    *,
    runtime: AzureContainerAppEnvironmentRuntime,
    trace_sink: _TraceSink = _discard_trace,
) -> AzureEnvironmentLifecycleResult:
    """Destroy an idle owned environment through Terraform and prove absence."""
    _validated_boundaries(policy, runtime, trace_sink)
    _emit(trace_sink, EnvironmentLifecycleEvent.RELEASE_STARTED, policy)
    existing = _read_environment(
        runtime,
        policy,
        expect_absent=False,
        phase="inspection",
    )
    if existing is None:
        _emit(trace_sink, EnvironmentLifecycleEvent.ABSENCE_VERIFIED, policy)
        return AzureEnvironmentLifecycleResult(
            disposition=EnvironmentLifecycleDisposition.ABSENT,
            environment_id=policy.environment_id,
            effective_profiles=policy.profiles,
        )
    profiles = validated_environment_profiles(
        existing,
        policy,
        require_desired_profiles=False,
        require_current_tags=False,
    )
    effective_policy = _merged_policy(policy, profiles)
    _emit(trace_sink, EnvironmentLifecycleEvent.OWNERSHIP_VERIFIED, effective_policy)
    if not policy.teardown_when_idle:
        _emit(trace_sink, EnvironmentLifecycleEvent.ENVIRONMENT_RETAINED, effective_policy)
        return _retained_result(effective_policy)

    try:
        apps = _validated_app_inventory(
            runtime.list_environment_apps(effective_policy),
            effective_policy,
        )
    except AzureEnvironmentLifecycleError:
        raise
    except Exception:
        raise AzureEnvironmentLifecycleError("inventory") from None
    _emit(
        trace_sink,
        EnvironmentLifecycleEvent.APP_INVENTORY_VERIFIED,
        effective_policy,
        active_app_count=len(apps),
    )
    if apps:
        _emit(
            trace_sink,
            EnvironmentLifecycleEvent.ENVIRONMENT_RETAINED,
            effective_policy,
            active_app_count=len(apps),
        )
        return _retained_result(effective_policy, active_app_count=len(apps))

    _emit(trace_sink, EnvironmentLifecycleEvent.DESTROY_STARTED, effective_policy)
    try:
        runtime.destroy(effective_policy)
    except Exception:
        raise AzureEnvironmentLifecycleError("destroy") from None
    _emit(trace_sink, EnvironmentLifecycleEvent.DESTROY_SUCCEEDED, effective_policy)
    remaining = _read_environment(
        runtime,
        effective_policy,
        expect_absent=True,
        phase="absence",
    )
    if remaining is not None:
        raise AzureEnvironmentLifecycleError("absence")
    _emit(trace_sink, EnvironmentLifecycleEvent.ABSENCE_VERIFIED, effective_policy)
    return AzureEnvironmentLifecycleResult(
        disposition=EnvironmentLifecycleDisposition.DESTROYED,
        environment_id=policy.environment_id,
        effective_profiles=effective_policy.profiles,
    )


__all__ = [
    "AzureContainerAppEnvironmentRuntime",
    "AzureEnvironmentLifecycleError",
    "AzureEnvironmentLifecyclePolicy",
    "AzureEnvironmentLifecycleResult",
    "AzureEnvironmentProfile",
    "EnvironmentLifecycleDisposition",
    "EnvironmentLifecycleEvent",
    "EnvironmentLifecycleTrace",
    "audit_environment_plan",
    "ensure_azure_containerapp_environment",
    "release_azure_containerapp_environment",
]
