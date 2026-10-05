"""Dependency-safe retention and teardown for owned Azure environments."""

from __future__ import annotations

import re
from datetime import UTC, datetime

from general_ludd.infra.azure_containerapp_environment_operations import (
    EnvironmentTraceSink,
    discard_environment_trace,
    emit_environment_trace,
    merged_environment_policy,
    read_environment,
    validate_environment_boundaries,
)
from general_ludd.infra.azure_containerapp_environment_types import (
    AzureContainerAppEnvironmentRuntime,
    AzureEnvironmentLifecycleError,
    AzureEnvironmentLifecyclePolicy,
    AzureEnvironmentLifecycleResult,
    EnvironmentLifecycleDisposition,
    EnvironmentLifecycleEvent,
)
from general_ludd.infra.azure_containerapp_environment_validation import (
    validated_environment_profiles,
)
from general_ludd.infra.azure_idle_retention import (
    AzureIdleRetentionPlan,
    AzureRetentionDisposition,
    AzureRetentionLayerKind,
)

_APP_NAME_PATTERN = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,30}[a-z0-9])?")


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


def _retention_deadline(
    policy: AzureEnvironmentLifecyclePolicy,
    retention_plan: AzureIdleRetentionPlan | None,
    now: datetime | None,
) -> tuple[AzureIdleRetentionPlan | None, datetime]:
    current = datetime.now(UTC) if now is None else now
    if current.tzinfo is None or current.utcoffset() is None:
        raise AzureEnvironmentLifecycleError("retention")
    current = current.astimezone(UTC)
    if retention_plan is None:
        return None, current
    if (
        not isinstance(retention_plan, AzureIdleRetentionPlan)
        or retention_plan.scope_digest != policy.operation_digest
    ):
        raise AzureEnvironmentLifecycleError("retention")
    return retention_plan, current


def _retains_environment(plan: AzureIdleRetentionPlan) -> bool:
    try:
        decision = plan.decision_for(AzureRetentionLayerKind.MANAGED_ENVIRONMENT)
    except KeyError:
        return False
    return decision.disposition is AzureRetentionDisposition.RETAIN


def release_azure_containerapp_environment(
    policy: AzureEnvironmentLifecyclePolicy,
    *,
    runtime: AzureContainerAppEnvironmentRuntime,
    trace_sink: EnvironmentTraceSink = discard_environment_trace,
    retention_plan: AzureIdleRetentionPlan | None = None,
    now: datetime | None = None,
) -> AzureEnvironmentLifecycleResult:
    """Destroy or boundedly retain an idle environment and prove the outcome."""
    validate_environment_boundaries(policy, runtime, trace_sink)
    authorized_retention, current = _retention_deadline(
        policy,
        retention_plan,
        now,
    )
    emit_environment_trace(trace_sink, EnvironmentLifecycleEvent.RELEASE_STARTED, policy)
    existing = read_environment(
        runtime,
        policy,
        expect_absent=False,
        phase="inspection",
    )
    if existing is None:
        emit_environment_trace(
            trace_sink,
            EnvironmentLifecycleEvent.ABSENCE_VERIFIED,
            policy,
        )
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
    effective_policy = merged_environment_policy(policy, profiles)
    emit_environment_trace(
        trace_sink,
        EnvironmentLifecycleEvent.OWNERSHIP_VERIFIED,
        effective_policy,
    )
    try:
        apps = _validated_app_inventory(
            runtime.list_environment_apps(effective_policy),
            effective_policy,
        )
    except AzureEnvironmentLifecycleError:
        raise
    except Exception:
        raise AzureEnvironmentLifecycleError("inventory") from None
    emit_environment_trace(
        trace_sink,
        EnvironmentLifecycleEvent.APP_INVENTORY_VERIFIED,
        effective_policy,
        active_app_count=len(apps),
    )
    if apps:
        emit_environment_trace(
            trace_sink,
            EnvironmentLifecycleEvent.ENVIRONMENT_RETAINED,
            effective_policy,
            active_app_count=len(apps),
        )
        return _retained_result(effective_policy, active_app_count=len(apps))
    if authorized_retention is not None and _retains_environment(
        authorized_retention
    ):
        remaining = int(
            (authorized_retention.reconcile_at - current).total_seconds()
        )
        if remaining > 0:
            emit_environment_trace(
                trace_sink,
                EnvironmentLifecycleEvent.ENVIRONMENT_RETAINED,
                effective_policy,
                retention_plan=authorized_retention,
                retention_seconds_remaining=remaining,
            )
            return _retained_result(effective_policy)
        emit_environment_trace(
            trace_sink,
            EnvironmentLifecycleEvent.RETENTION_EXPIRED,
            effective_policy,
            retention_plan=authorized_retention,
        )

    emit_environment_trace(
        trace_sink,
        EnvironmentLifecycleEvent.DESTROY_STARTED,
        effective_policy,
    )
    try:
        runtime.destroy(effective_policy)
    except Exception:
        raise AzureEnvironmentLifecycleError("destroy") from None
    emit_environment_trace(
        trace_sink,
        EnvironmentLifecycleEvent.DESTROY_SUCCEEDED,
        effective_policy,
    )
    remaining_environment = read_environment(
        runtime,
        effective_policy,
        expect_absent=True,
        phase="absence",
    )
    if remaining_environment is not None:
        raise AzureEnvironmentLifecycleError("absence")
    emit_environment_trace(
        trace_sink,
        EnvironmentLifecycleEvent.ABSENCE_VERIFIED,
        effective_policy,
    )
    return AzureEnvironmentLifecycleResult(
        disposition=EnvironmentLifecycleDisposition.DESTROYED,
        environment_id=policy.environment_id,
        effective_profiles=effective_policy.profiles,
    )


__all__ = ["release_azure_containerapp_environment"]
