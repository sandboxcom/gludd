"""Ownership-safe Terraform lifecycle for Azure Container Apps environments.

Terraform is the sole mutation authority. Independent ARM reads establish every
precondition and postcondition without becoming a second infrastructure writer.
"""

from __future__ import annotations

from general_ludd.infra.azure_containerapp_environment_operations import (
    EnvironmentTraceSink as _TraceSink,
)
from general_ludd.infra.azure_containerapp_environment_operations import (
    discard_environment_trace as _discard_trace,
)
from general_ludd.infra.azure_containerapp_environment_operations import (
    emit_environment_trace as _emit,
)
from general_ludd.infra.azure_containerapp_environment_operations import (
    merged_environment_policy as _merged_policy,
)
from general_ludd.infra.azure_containerapp_environment_operations import (
    read_environment as _read_environment,
)
from general_ludd.infra.azure_containerapp_environment_operations import (
    validate_environment_boundaries as _validated_boundaries,
)
from general_ludd.infra.azure_containerapp_environment_retention import (
    release_azure_containerapp_environment,
)
from general_ludd.infra.azure_containerapp_environment_types import (
    AzureContainerAppEnvironmentImportRuntime,
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


def _import_verified_environment(
    policy: AzureEnvironmentLifecyclePolicy,
    runtime: AzureContainerAppEnvironmentRuntime,
    trace_sink: _TraceSink,
) -> None:
    """Adopt state only after the independent reader proves Gludd ownership."""
    if not isinstance(runtime, AzureContainerAppEnvironmentImportRuntime):
        return
    _emit(trace_sink, EnvironmentLifecycleEvent.STATE_IMPORT_STARTED, policy)
    try:
        runtime.import_existing_environment(policy)
    except Exception:
        raise AzureEnvironmentLifecycleError("state-import") from None
    _emit(trace_sink, EnvironmentLifecycleEvent.STATE_IMPORT_SUCCEEDED, policy)


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
        try:
            existing_profiles = validated_environment_profiles(
                existing,
                policy,
                require_desired_profiles=False,
                require_current_tags=False,
            )
        except AzureEnvironmentLifecycleError as exc:
            _emit(
                trace_sink,
                EnvironmentLifecycleEvent.INSPECTION_FAILED,
                policy,
                failure_reason=exc.reason or "unknown",
            )
            raise
        effective_policy = _merged_policy(policy, existing_profiles)
        _emit(trace_sink, EnvironmentLifecycleEvent.OWNERSHIP_VERIFIED, effective_policy)
        _import_verified_environment(effective_policy, runtime, trace_sink)

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
