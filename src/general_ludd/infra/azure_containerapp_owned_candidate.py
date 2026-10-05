"""On-demand Azure Container App candidate acquisition with owned cleanup."""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from threading import RLock
from typing import Protocol, runtime_checkable

from general_ludd.infra.azure_containerapp_environment_lifecycle import (
    AzureContainerAppEnvironmentRuntime,
    AzureEnvironmentLifecyclePolicy,
    EnvironmentLifecycleDisposition,
    ensure_azure_containerapp_environment,
    release_azure_containerapp_environment,
)
from general_ludd.infra.azure_containerapp_live_proof import (
    AzureContainerAppLiveProofPolicy,
    AzureContainerAppProofRuntime,
    audit_containerapp_plan,
)
from general_ludd.infra.azure_containerapp_owned_candidate_types import (
    OwnedCandidateLifecycleError,
    OwnedCandidateLifecycleEvent,
    OwnedCandidateLifecycleTrace,
)
from general_ludd.infra.azure_containerapp_owned_lifecycle import (
    validate_owned_azure_containerapp_authority,
)
from general_ludd.infra.azure_idle_retention import (
    AzureIdleRetentionPlan,
    AzureIdleRetentionPolicy,
    plan_container_apps_idle_retention,
)
from general_ludd.self_improve.azure_backend import (
    AzureApprovedPrompt,
    AzureCandidateResponse,
)
from general_ludd.self_improve.model_candidates import (
    AzureContainerAppCandidateIdentity,
    BackendInfrastructureError,
)

_PROTOCOL = "gludd-owned-azure-containerapp-candidate-v1"
@runtime_checkable
class _Backend(Protocol):
    @property
    def candidate_identity(self) -> AzureContainerAppCandidateIdentity: ...

    def generate(
        self,
        request: AzureApprovedPrompt,
        *,
        max_output_tokens: int,
        timeout_seconds: float,
    ) -> AzureCandidateResponse: ...

    def close(self) -> None: ...


_BackendFactory = Callable[[AzureContainerAppCandidateIdentity], _Backend]


def _discard_trace(_trace: OwnedCandidateLifecycleTrace) -> None:
    return None


def owned_candidate_deployment_digest(
    app_policy: AzureContainerAppLiveProofPolicy,
    environment_policy: AzureEnvironmentLifecyclePolicy,
    idle_retention_policy: AzureIdleRetentionPolicy | None = None,
    expected_next_demand_seconds: int | None = None,
) -> str:
    """Bind one app and environment desired state without acquiring resources."""
    if not isinstance(app_policy, AzureContainerAppLiveProofPolicy) or not isinstance(
        environment_policy,
        AzureEnvironmentLifecyclePolicy,
    ):
        raise ValueError("owned candidate policies have an invalid boundary")
    encoded = json.dumps(
        {
            "app_operation_digest": app_policy.operation_digest,
            "environment_operation_digest": environment_policy.operation_digest,
            "environment_state_digest": environment_policy.state_digest,
            "idle_retention": (
                None
                if idle_retention_policy is None
                else {
                    "preset": idle_retention_policy.preset.value,
                    "max_idle_hourly_cost_microusd": (
                        idle_retention_policy.max_idle_hourly_cost_microusd
                    ),
                    "max_idle_monthly_cost_microusd": (
                        idle_retention_policy.max_idle_monthly_cost_microusd
                    ),
                    "max_retention_cost_microusd": (
                        idle_retention_policy.max_retention_cost_microusd
                    ),
                    "max_retention_seconds": (
                        idle_retention_policy.max_retention_seconds
                    ),
                    "max_price_age_seconds": (
                        idle_retention_policy.max_price_age_seconds
                    ),
                    "max_latency_age_seconds": (
                        idle_retention_policy.max_latency_age_seconds
                    ),
                    "max_cost_per_saved_hour_microusd": (
                        idle_retention_policy.max_cost_per_saved_hour_microusd
                    ),
                    "expected_next_demand_seconds": expected_next_demand_seconds,
                }
            ),
            "protocol": _PROTOCOL,
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


class _OwnedCandidateBackend:
    """Delegate inference while retaining the infrastructure cleanup owner."""

    def __init__(
        self,
        owner: AzureContainerAppOwnedCandidateFactory,
        delegate: _Backend,
    ) -> None:
        self._owner = owner
        self._delegate = delegate
        self._closed = False

    @property
    def candidate_identity(self) -> AzureContainerAppCandidateIdentity:
        return self._delegate.candidate_identity

    def generate(
        self,
        request: AzureApprovedPrompt,
        *,
        max_output_tokens: int,
        timeout_seconds: float,
    ) -> AzureCandidateResponse:
        if self._closed:
            raise OwnedCandidateLifecycleError("closed")
        return self._delegate.generate(
            request,
            max_output_tokens=max_output_tokens,
            timeout_seconds=timeout_seconds,
        )

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._owner._release(self._delegate)


class AzureContainerAppOwnedCandidateFactory:
    """Bootstrap one exact candidate lazily and destroy it when its backend closes."""

    def __init__(
        self,
        *,
        app_policy: AzureContainerAppLiveProofPolicy,
        environment_policy: AzureEnvironmentLifecyclePolicy,
        environment_runtime: AzureContainerAppEnvironmentRuntime,
        app_runtime: AzureContainerAppProofRuntime,
        backend_factory: _BackendFactory,
        resource_release: Callable[[], None] | None = None,
        trace_sink: Callable[[OwnedCandidateLifecycleTrace], None] = _discard_trace,
        idle_retention_policy: AzureIdleRetentionPolicy | None = None,
        expected_next_demand_seconds: int | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        """Initialize the exact app, environment, backend, and release owners."""
        if not isinstance(app_policy, AzureContainerAppLiveProofPolicy):
            raise ValueError("app_policy must be AzureContainerAppLiveProofPolicy")
        if not app_policy.live:
            raise ValueError("owned candidate bootstrap requires live authority")
        if not isinstance(environment_policy, AzureEnvironmentLifecyclePolicy):
            raise ValueError("environment_policy must be AzureEnvironmentLifecyclePolicy")
        if not isinstance(environment_runtime, AzureContainerAppEnvironmentRuntime):
            raise ValueError("environment_runtime has an invalid boundary")
        if not isinstance(app_runtime, AzureContainerAppProofRuntime):
            raise ValueError("app_runtime has an invalid boundary")
        if not callable(backend_factory):
            raise ValueError("backend_factory must be callable")
        if resource_release is not None and not callable(resource_release):
            raise ValueError("resource_release must be callable")
        if not callable(trace_sink):
            raise ValueError("trace_sink must be callable")
        if idle_retention_policy is not None and not isinstance(
            idle_retention_policy,
            AzureIdleRetentionPolicy,
        ):
            raise ValueError("idle_retention_policy has an invalid boundary")
        if expected_next_demand_seconds is not None and (
            isinstance(expected_next_demand_seconds, bool)
            or not isinstance(expected_next_demand_seconds, int)
            or expected_next_demand_seconds <= 0
        ):
            raise ValueError("expected_next_demand_seconds must be a positive integer")
        if not callable(now) or not callable(monotonic):
            raise ValueError("retention clocks must be callable")
        validate_owned_azure_containerapp_authority(app_policy, environment_policy)
        self._app_policy = app_policy
        self._environment_policy = environment_policy
        self._environment_runtime = environment_runtime
        self._app_runtime = app_runtime
        self._backend_factory = backend_factory
        self._resource_release = resource_release
        self._trace_sink = trace_sink
        self._idle_retention_policy = idle_retention_policy
        self._expected_next_demand_seconds = expected_next_demand_seconds
        self._now = now
        self._monotonic = monotonic
        self._environment_latency_seconds: float | None = None
        self._app_latency_seconds: float | None = None
        self._deployment_digest = owned_candidate_deployment_digest(
            app_policy,
            environment_policy,
            idle_retention_policy,
            expected_next_demand_seconds,
        )
        self._lock = RLock()
        self._active = False
        self._used = False
        self._resources_released = False

    @property
    def deployment_digest(self) -> str:
        """Return the immutable app/environment configuration identity."""
        return self._deployment_digest

    @property
    def active(self) -> bool:
        """Return whether a provisioned candidate still owns live resources."""
        with self._lock:
            return self._active

    def _emit(
        self,
        event: OwnedCandidateLifecycleEvent,
        identity: AzureContainerAppCandidateIdentity | None = None,
        retention_plan: AzureIdleRetentionPlan | None = None,
    ) -> None:
        try:
            self._trace_sink(
                OwnedCandidateLifecycleTrace(
                    event=event,
                    operation_digest=self._deployment_digest,
                    candidate_identity_digest=(
                        None if identity is None else identity.identity_digest
                    ),
                    retention_plan_digest=(
                        None if retention_plan is None else retention_plan.plan_digest
                    ),
                    retention_seconds=(
                        0 if retention_plan is None else retention_plan.retention_seconds
                    ),
                    retention_hourly_cost_microusd=(
                        0
                        if retention_plan is None
                        else retention_plan.hourly_cost_microusd
                    ),
                )
            )
        except Exception:
            raise OwnedCandidateLifecycleError("trace") from None

    def _cleanup_emit(
        self,
        event: OwnedCandidateLifecycleEvent,
        identity: AzureContainerAppCandidateIdentity | None = None,
        retention_plan: AzureIdleRetentionPlan | None = None,
    ) -> bool:
        try:
            self._emit(event, identity, retention_plan)
        except OwnedCandidateLifecycleError:
            return False
        return True

    def _release_resources(self) -> bool:
        with self._lock:
            if self._resources_released:
                return True
            self._resources_released = True
        if self._resource_release is not None:
            try:
                self._resource_release()
            except Exception:
                return False
        return self._cleanup_emit(OwnedCandidateLifecycleEvent.RESOURCES_RELEASED)

    def _release_environment(self) -> bool:
        emitted = self._cleanup_emit(
            OwnedCandidateLifecycleEvent.ENVIRONMENT_RELEASE_STARTED
        )
        retention_plan = self._build_retention_plan()
        if retention_plan is not None:
            emitted = (
                self._cleanup_emit(
                    OwnedCandidateLifecycleEvent.ENVIRONMENT_RETENTION_PLANNED,
                    retention_plan=retention_plan,
                )
                and emitted
            )
        try:
            result = release_azure_containerapp_environment(
                self._environment_policy,
                runtime=self._environment_runtime,
                retention_plan=retention_plan,
                now=(
                    retention_plan.reconcile_at
                    - timedelta(seconds=retention_plan.retention_seconds)
                    if retention_plan is not None
                    else None
                ),
            )
        except Exception:
            return False
        released = result.disposition in {
            EnvironmentLifecycleDisposition.ABSENT,
            EnvironmentLifecycleDisposition.DESTROYED,
        } or (
            result.disposition is EnvironmentLifecycleDisposition.RETAINED
            and retention_plan is not None
            and result.active_app_count == 0
        )
        if released:
            emitted = (
                self._cleanup_emit(OwnedCandidateLifecycleEvent.ENVIRONMENT_RELEASED)
                and emitted
            )
        return emitted and released

    def _build_retention_plan(self) -> AzureIdleRetentionPlan | None:
        if (
            self._idle_retention_policy is None
            or self._environment_latency_seconds is None
        ):
            return None
        try:
            current = self._now()
            return plan_container_apps_idle_retention(
                policy=self._idle_retention_policy,
                scope_digest=self._environment_policy.operation_digest,
                now=current,
                environment_latency_seconds=self._environment_latency_seconds,
                app_latency_seconds=self._app_latency_seconds,
                min_replicas=self._app_policy.min_replicas,
                activation_blocked_when_idle=False,
                has_dedicated_profiles=False,
                has_private_endpoint=False,
                has_planned_maintenance=False,
                has_paid_logging=False,
                runnable_todo_count=0,
                expected_next_demand_seconds=self._expected_next_demand_seconds,
            )
        except Exception:
            return None

    def _cleanup(
        self,
        delegate: _Backend | None,
        *,
        environment_acquired: bool,
        app_apply_started: bool,
        identity: AzureContainerAppCandidateIdentity | None,
    ) -> bool:
        clean = True
        if delegate is not None:
            clean = (
                self._cleanup_emit(
                    OwnedCandidateLifecycleEvent.BACKEND_CLOSE_STARTED,
                    identity,
                )
                and clean
            )
            try:
                delegate.close()
            except Exception:
                clean = False
            clean = (
                self._cleanup_emit(
                    OwnedCandidateLifecycleEvent.BACKEND_CLOSED,
                    identity,
                )
                and clean
            )
        app_absent = not app_apply_started
        if app_apply_started:
            clean = (
                self._cleanup_emit(
                    OwnedCandidateLifecycleEvent.APP_DESTROY_STARTED,
                    identity,
                )
                and clean
            )
            try:
                self._app_runtime.destroy(self._app_policy)
                clean = (
                    self._cleanup_emit(
                        OwnedCandidateLifecycleEvent.APP_DESTROYED,
                        identity,
                    )
                    and clean
                )
                app_absent = not self._app_runtime.exists(self._app_policy)
                if app_absent:
                    clean = (
                        self._cleanup_emit(
                            OwnedCandidateLifecycleEvent.APP_ABSENCE_VERIFIED,
                            identity,
                        )
                        and clean
                    )
            except Exception:
                app_absent = False
                clean = False
        environment_released = (
            self._release_environment() if environment_acquired else True
        )
        resources_released = self._release_resources()
        with self._lock:
            self._active = False
        return clean and app_absent and environment_released and resources_released

    def _release(self, delegate: _Backend) -> None:
        identity = delegate.candidate_identity
        if not self._cleanup(
            delegate,
            environment_acquired=True,
            app_apply_started=True,
            identity=identity,
        ):
            raise OwnedCandidateLifecycleError("cleanup")

    def __call__(self) -> _Backend:
        """Provision and bind one candidate only when candidate assembly needs it."""
        with self._lock:
            if self._active:
                raise OwnedCandidateLifecycleError("active")
            if self._used:
                raise OwnedCandidateLifecycleError("closed")
            self._used = True
            self._active = True
        phase = "environment"
        environment_acquired = False
        app_apply_started = False
        identity: AzureContainerAppCandidateIdentity | None = None
        delegate: _Backend | None = None
        try:
            self._emit(OwnedCandidateLifecycleEvent.ENVIRONMENT_ACQUIRE_STARTED)
            environment_started = self._monotonic()
            ensure_azure_containerapp_environment(
                self._environment_policy,
                runtime=self._environment_runtime,
            )
            environment_acquired = True
            self._environment_latency_seconds = max(
                self._monotonic() - environment_started,
                0.000_001,
            )
            self._emit(OwnedCandidateLifecycleEvent.ENVIRONMENT_ACQUIRED)
            phase = "plan"
            app_started = self._monotonic()
            self._emit(OwnedCandidateLifecycleEvent.APP_PLAN_STARTED)
            plan = self._app_runtime.plan(self._app_policy)
            audit_containerapp_plan(plan, self._app_policy)
            self._emit(OwnedCandidateLifecycleEvent.APP_PLAN_AUDITED)
            phase = "preflight"
            self._emit(OwnedCandidateLifecycleEvent.APP_PREFLIGHT_STARTED)
            self._app_runtime.preflight(self._app_policy)
            self._emit(OwnedCandidateLifecycleEvent.APP_PREFLIGHT_SUCCEEDED)
            phase = "apply"
            self._emit(OwnedCandidateLifecycleEvent.APP_APPLY_STARTED)
            app_apply_started = True
            evidence = self._app_runtime.apply(self._app_policy)
            self._app_latency_seconds = max(
                self._monotonic() - app_started,
                0.000_001,
            )
            identity = evidence.candidate_identity(self._app_policy)
            self._emit(OwnedCandidateLifecycleEvent.APP_APPLIED, identity)
            phase = "backend"
            delegate = self._backend_factory(identity)
            if (
                not isinstance(delegate, _Backend)
                or delegate.candidate_identity.identity_digest != identity.identity_digest
            ):
                raise ValueError
            self._emit(OwnedCandidateLifecycleEvent.BACKEND_ACQUIRED, identity)
            return _OwnedCandidateBackend(self, delegate)
        except BaseException as cause:
            cleaned = self._cleanup(
                delegate,
                environment_acquired=environment_acquired,
                app_apply_started=app_apply_started,
                identity=identity,
            )
            self._cleanup_emit(OwnedCandidateLifecycleEvent.FAILED, identity)
            if not cleaned:
                raise OwnedCandidateLifecycleError("cleanup") from None
            failure = (
                cause.failure if isinstance(cause, BackendInfrastructureError) else None
            )
            raise OwnedCandidateLifecycleError(phase, failure=failure) from None


__all__ = (
    "AzureContainerAppOwnedCandidateFactory",
    "OwnedCandidateLifecycleError",
    "OwnedCandidateLifecycleEvent",
    "OwnedCandidateLifecycleTrace",
    "owned_candidate_deployment_digest",
)
