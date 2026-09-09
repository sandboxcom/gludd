"""Compose owned environment lifecycle around one bounded Container App proof."""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import UTC, datetime

from general_ludd.infra.azure_containerapp_environment_lifecycle import (
    AzureContainerAppEnvironmentRuntime,
    AzureEnvironmentLifecyclePolicy,
    AzureEnvironmentProfile,
    EnvironmentLifecycleDisposition,
    EnvironmentLifecycleTrace,
    ensure_azure_containerapp_environment,
    release_azure_containerapp_environment,
)
from general_ludd.infra.azure_containerapp_live_proof import (
    AzureContainerAppLiveProofError,
    AzureContainerAppLiveProofFailure,
    AzureContainerAppLiveProofPolicy,
    AzureContainerAppLiveProofResult,
    AzureContainerAppProofRuntime,
    LiveProofTrace,
    run_azure_containerapp_live_proof,
)
from general_ludd.infra.azure_idle_retention import (
    AzureIdleRetentionPlan,
    AzureIdleRetentionPolicy,
    AzureRetentionTrace,
    plan_container_apps_idle_retention,
)
from general_ludd.self_improve.azure_backend import (
    AzureApprovedPrompt,
    AzureCandidateResponse,
)
from general_ludd.self_improve.model_candidates import (
    AzureContainerAppCandidateIdentity,
    CandidateBackend,
)

_Backend = CandidateBackend[AzureApprovedPrompt, AzureCandidateResponse]
_BackendFactory = Callable[[AzureContainerAppCandidateIdentity], _Backend]


def _discard_environment_trace(_trace: EnvironmentLifecycleTrace) -> None:
    return None


def _discard_app_trace(_trace: LiveProofTrace) -> None:
    return None


def _discard_retention_trace(_trace: AzureRetentionTrace) -> None:
    return None


def _validate_boundaries(
    app_policy: AzureContainerAppLiveProofPolicy,
    environment_policy: AzureEnvironmentLifecyclePolicy,
    environment_runtime: AzureContainerAppEnvironmentRuntime,
    app_runtime: AzureContainerAppProofRuntime,
    environment_trace_sink: Callable[[EnvironmentLifecycleTrace], None],
    app_trace_sink: Callable[[LiveProofTrace], None],
) -> None:
    if not isinstance(app_policy, AzureContainerAppLiveProofPolicy):
        raise ValueError("app_policy must be AzureContainerAppLiveProofPolicy")
    if not isinstance(environment_policy, AzureEnvironmentLifecyclePolicy):
        raise ValueError(
            "environment_policy must be AzureEnvironmentLifecyclePolicy"
        )
    if not isinstance(environment_runtime, AzureContainerAppEnvironmentRuntime):
        raise ValueError("environment_runtime must implement its runtime protocol")
    if not isinstance(app_runtime, AzureContainerAppProofRuntime):
        raise ValueError("app_runtime must implement its runtime protocol")
    if not callable(environment_trace_sink) or not callable(app_trace_sink):
        raise ValueError("trace sinks must be callable")


def _validate_retention_boundaries(
    policy: AzureIdleRetentionPolicy | None,
    expected_next_demand_seconds: int | None,
    now: Callable[[], datetime],
    monotonic: Callable[[], float],
    trace_sink: Callable[[AzureRetentionTrace], None],
) -> None:
    if policy is not None and not isinstance(policy, AzureIdleRetentionPolicy):
        raise ValueError("idle_retention_policy has an invalid boundary")
    if expected_next_demand_seconds is not None and (
        isinstance(expected_next_demand_seconds, bool)
        or not isinstance(expected_next_demand_seconds, int)
        or expected_next_demand_seconds <= 0
    ):
        raise ValueError("expected_next_demand_seconds must be a positive integer")
    if not callable(now) or not callable(monotonic) or not callable(trace_sink):
        raise ValueError("retention clocks and trace sink must be callable")


def validate_owned_azure_containerapp_authority(
    app_policy: AzureContainerAppLiveProofPolicy,
    environment_policy: AzureEnvironmentLifecyclePolicy,
) -> None:
    """Require app and environment policies to share one teardown authority."""
    try:
        required_profile = AzureEnvironmentProfile(
            app_policy.workload_profile_name,
            app_policy.workload_profile_type,
        )
        if (
            app_policy.subscription_id != environment_policy.subscription_id
            or app_policy.resource_group != environment_policy.resource_group
            or app_policy.environment_name != environment_policy.environment_name
            or app_policy.location != environment_policy.location
            or required_profile not in environment_policy.profiles
        ):
            raise ValueError
    except Exception:
        raise AzureContainerAppLiveProofError(
            AzureContainerAppLiveProofFailure.POLICY
        ) from None


def _release_owned_environment(
    environment_policy: AzureEnvironmentLifecyclePolicy,
    environment_runtime: AzureContainerAppEnvironmentRuntime,
    environment_trace_sink: Callable[[EnvironmentLifecycleTrace], None],
    retention_plan: AzureIdleRetentionPlan | None,
    retention_now: datetime | None,
) -> bool:
    try:
        release = release_azure_containerapp_environment(
            environment_policy,
            runtime=environment_runtime,
            trace_sink=environment_trace_sink,
            retention_plan=retention_plan,
            now=retention_now,
        )
        return release.disposition in {
            EnvironmentLifecycleDisposition.ABSENT,
            EnvironmentLifecycleDisposition.DESTROYED,
        } or (
            release.disposition is EnvironmentLifecycleDisposition.RETAINED
            and retention_plan is not None
            and release.active_app_count == 0
        )
    except Exception:
        return False


def _retention_plan(
    app_policy: AzureContainerAppLiveProofPolicy,
    environment_policy: AzureEnvironmentLifecyclePolicy,
    policy: AzureIdleRetentionPolicy | None,
    *,
    environment_latency_seconds: float | None,
    expected_next_demand_seconds: int | None,
    now: Callable[[], datetime],
    trace_sink: Callable[[AzureRetentionTrace], None],
) -> tuple[AzureIdleRetentionPlan | None, datetime | None]:
    if policy is None or environment_latency_seconds is None:
        return None, None
    try:
        current = now()
        plan = plan_container_apps_idle_retention(
            policy=policy,
            scope_digest=environment_policy.operation_digest,
            now=current,
            environment_latency_seconds=environment_latency_seconds,
            app_latency_seconds=None,
            min_replicas=app_policy.min_replicas,
            activation_blocked_when_idle=False,
            has_dedicated_profiles=False,
            has_private_endpoint=False,
            has_planned_maintenance=False,
            has_paid_logging=False,
            runnable_todo_count=0,
            expected_next_demand_seconds=expected_next_demand_seconds,
            trace_sink=trace_sink,
        )
        return plan, current
    except Exception:
        return None, None


def run_owned_azure_containerapp_live_proof(
    app_policy: AzureContainerAppLiveProofPolicy,
    *,
    environment_policy: AzureEnvironmentLifecyclePolicy,
    environment_runtime: AzureContainerAppEnvironmentRuntime,
    app_runtime: AzureContainerAppProofRuntime,
    approved_prompt: AzureApprovedPrompt,
    backend_factory: _BackendFactory,
    environment_trace_sink: Callable[
        [EnvironmentLifecycleTrace], None
    ] = _discard_environment_trace,
    app_trace_sink: Callable[[LiveProofTrace], None] = _discard_app_trace,
    idle_retention_policy: AzureIdleRetentionPolicy | None = None,
    expected_next_demand_seconds: int | None = None,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
    monotonic: Callable[[], float] = time.monotonic,
    retention_trace_sink: Callable[
        [AzureRetentionTrace], None
    ] = _discard_retention_trace,
) -> AzureContainerAppLiveProofResult:
    """Create/reconcile, use, and release one owned environment safely."""
    _validate_boundaries(
        app_policy,
        environment_policy,
        environment_runtime,
        app_runtime,
        environment_trace_sink,
        app_trace_sink,
    )
    _validate_retention_boundaries(
        idle_retention_policy,
        expected_next_demand_seconds,
        now,
        monotonic,
        retention_trace_sink,
    )
    validate_owned_azure_containerapp_authority(app_policy, environment_policy)
    if not app_policy.live:
        return run_azure_containerapp_live_proof(
            app_policy,
            runtime=app_runtime,
            approved_prompt=approved_prompt,
            backend_factory=backend_factory,
            trace_sink=app_trace_sink,
        )

    environment_started: float | None = None
    if idle_retention_policy is not None:
        try:
            environment_started = monotonic()
        except Exception:
            environment_started = None
    try:
        ensure_azure_containerapp_environment(
            environment_policy,
            runtime=environment_runtime,
            trace_sink=environment_trace_sink,
        )
    except Exception:
        raise AzureContainerAppLiveProofError(
            AzureContainerAppLiveProofFailure.ENVIRONMENT
        ) from None
    environment_latency_seconds: float | None = None
    if environment_started is not None:
        try:
            environment_latency_seconds = max(
                monotonic() - environment_started,
                0.000_001,
            )
        except Exception:
            environment_latency_seconds = None

    proof: AzureContainerAppLiveProofResult | None = None
    proof_failure: AzureContainerAppLiveProofError | None = None
    try:
        proof = run_azure_containerapp_live_proof(
            app_policy,
            runtime=app_runtime,
            approved_prompt=approved_prompt,
            backend_factory=backend_factory,
            trace_sink=app_trace_sink,
        )
    except AzureContainerAppLiveProofError as error:
        proof_failure = error
    except Exception:
        proof_failure = AzureContainerAppLiveProofError(
            AzureContainerAppLiveProofFailure.POLICY
        )

    retention_plan, retention_now = _retention_plan(
        app_policy,
        environment_policy,
        idle_retention_policy,
        environment_latency_seconds=environment_latency_seconds,
        expected_next_demand_seconds=expected_next_demand_seconds,
        now=now,
        trace_sink=retention_trace_sink,
    )
    if not _release_owned_environment(
        environment_policy,
        environment_runtime,
        environment_trace_sink,
        retention_plan,
        retention_now,
    ):
        raise AzureContainerAppLiveProofError(
            AzureContainerAppLiveProofFailure.CLEANUP
        ) from None
    if proof_failure is not None:
        raise proof_failure from None
    if proof is None:
        raise AzureContainerAppLiveProofError(
            AzureContainerAppLiveProofFailure.POLICY
        )
    return proof


__all__ = (
    "run_owned_azure_containerapp_live_proof",
    "validate_owned_azure_containerapp_authority",
)
