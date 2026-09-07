"""On-demand Azure Container App candidate acquisition with owned cleanup."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
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
from general_ludd.infra.azure_containerapp_owned_lifecycle import (
    validate_owned_azure_containerapp_authority,
)
from general_ludd.self_improve.azure_backend import (
    AzureApprovedPrompt,
    AzureCandidateResponse,
)
from general_ludd.self_improve.model_candidates import AzureContainerAppCandidateIdentity

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


class OwnedCandidateLifecycleError(RuntimeError):
    """Fixed-context failure that never retains provider or prompt content."""

    def __init__(self, operation: str) -> None:
        """Initialize a censored failure for one lifecycle operation."""
        super().__init__(f"owned Azure candidate lifecycle failed: {operation}")
        self.operation = operation


class OwnedCandidateLifecycleEvent(StrEnum):
    """Observable states for candidate acquisition and release."""

    ENVIRONMENT_ACQUIRE_STARTED = "environment_acquire_started"
    ENVIRONMENT_ACQUIRED = "environment_acquired"
    APP_PLAN_STARTED = "app_plan_started"
    APP_PLAN_AUDITED = "app_plan_audited"
    APP_PREFLIGHT_STARTED = "app_preflight_started"
    APP_PREFLIGHT_SUCCEEDED = "app_preflight_succeeded"
    APP_APPLY_STARTED = "app_apply_started"
    APP_APPLIED = "app_applied"
    BACKEND_ACQUIRED = "backend_acquired"
    BACKEND_CLOSE_STARTED = "backend_close_started"
    BACKEND_CLOSED = "backend_closed"
    APP_DESTROY_STARTED = "app_destroy_started"
    APP_DESTROYED = "app_destroyed"
    APP_ABSENCE_VERIFIED = "app_absence_verified"
    ENVIRONMENT_RELEASE_STARTED = "environment_release_started"
    ENVIRONMENT_RELEASED = "environment_released"
    RESOURCES_RELEASED = "resources_released"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class OwnedCandidateLifecycleTrace:
    """Content-free lifecycle evidence safe for logs and durable event stores."""

    event: OwnedCandidateLifecycleEvent
    operation_digest: str
    candidate_identity_digest: str | None = None


def _discard_trace(_trace: OwnedCandidateLifecycleTrace) -> None:
    return None


def owned_candidate_deployment_digest(
    app_policy: AzureContainerAppLiveProofPolicy,
    environment_policy: AzureEnvironmentLifecyclePolicy,
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
        validate_owned_azure_containerapp_authority(app_policy, environment_policy)
        self._app_policy = app_policy
        self._environment_policy = environment_policy
        self._environment_runtime = environment_runtime
        self._app_runtime = app_runtime
        self._backend_factory = backend_factory
        self._resource_release = resource_release
        self._trace_sink = trace_sink
        self._deployment_digest = owned_candidate_deployment_digest(
            app_policy,
            environment_policy,
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
    ) -> None:
        try:
            self._trace_sink(
                OwnedCandidateLifecycleTrace(
                    event=event,
                    operation_digest=self._deployment_digest,
                    candidate_identity_digest=(
                        None if identity is None else identity.identity_digest
                    ),
                )
            )
        except Exception:
            raise OwnedCandidateLifecycleError("trace") from None

    def _cleanup_emit(
        self,
        event: OwnedCandidateLifecycleEvent,
        identity: AzureContainerAppCandidateIdentity | None = None,
    ) -> bool:
        try:
            self._emit(event, identity)
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
        try:
            result = release_azure_containerapp_environment(
                self._environment_policy,
                runtime=self._environment_runtime,
            )
        except Exception:
            return False
        released = result.disposition in {
            EnvironmentLifecycleDisposition.ABSENT,
            EnvironmentLifecycleDisposition.DESTROYED,
        }
        if released:
            emitted = (
                self._cleanup_emit(OwnedCandidateLifecycleEvent.ENVIRONMENT_RELEASED)
                and emitted
            )
        return emitted and released

    def _cleanup(
        self,
        delegate: _Backend | None,
        *,
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
        environment_released = self._release_environment()
        resources_released = self._release_resources()
        with self._lock:
            self._active = False
        return clean and app_absent and environment_released and resources_released

    def _release(self, delegate: _Backend) -> None:
        identity = delegate.candidate_identity
        if not self._cleanup(
            delegate,
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
        app_apply_started = False
        identity: AzureContainerAppCandidateIdentity | None = None
        delegate: _Backend | None = None
        try:
            self._emit(OwnedCandidateLifecycleEvent.ENVIRONMENT_ACQUIRE_STARTED)
            ensure_azure_containerapp_environment(
                self._environment_policy,
                runtime=self._environment_runtime,
            )
            self._emit(OwnedCandidateLifecycleEvent.ENVIRONMENT_ACQUIRED)
            phase = "plan"
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
        except BaseException:
            cleaned = self._cleanup(
                delegate,
                app_apply_started=app_apply_started,
                identity=identity,
            )
            self._cleanup_emit(OwnedCandidateLifecycleEvent.FAILED, identity)
            if not cleaned:
                raise OwnedCandidateLifecycleError("cleanup") from None
            raise OwnedCandidateLifecycleError(phase) from None


__all__ = (
    "AzureContainerAppOwnedCandidateFactory",
    "OwnedCandidateLifecycleError",
    "OwnedCandidateLifecycleEvent",
    "OwnedCandidateLifecycleTrace",
    "owned_candidate_deployment_digest",
)
