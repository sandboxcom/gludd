"""Bounded Azure Container Apps readiness readers for an owned deployment."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, cast

from general_ludd.infra.azure_containerapp_environment_lifecycle import (
    AzureEnvironmentLifecyclePolicy,
)
from general_ludd.infra.azure_containerapp_gpu import ModelServingRequirement
from general_ludd.infra.azure_containerapp_live_proof import (
    AzureContainerAppLiveProofPolicy,
)
from general_ludd.infra.azure_containerapp_preflight import (
    ARM_SCOPE,
    AzureContainerAppReadOnlyPreflight,
    PreflightTrace,
)
from general_ludd.infra.azure_containerapp_runtime_state import (
    _app_provisioning_state,
    _environment_ready,
    _observed_revision_name,
    _ready,
    _ready_revision_name,
    _replica_progress,
    _replica_status,
    _revision_state,
    _RevisionState,
    _with_ready_revision,
)
from general_ludd.infra.azure_containerapp_sdk import AzureContainerAppsSDKReadError
from general_ludd.self_improve.model_candidates import (
    BackendFailure,
    BackendInfrastructureError,
)


def _discard_preflight(_trace: PreflightTrace) -> None:
    return None


def _discard_progress(_message: str) -> None:
    return None


@dataclass(slots=True)
class AzureContainerAppRuntimeReaders:
    """Poll only the exact policy-bound app and environment resources."""

    credential: Any
    environment_transport: Any
    lifecycle_transport: Any
    app_transport: Any
    policy: AzureContainerAppLiveProofPolicy
    preflight_factory: Callable[..., Any] = AzureContainerAppReadOnlyPreflight
    preflight_trace_sink: Callable[[PreflightTrace], None] = _discard_preflight
    progress_sink: Callable[[str], None] = _discard_progress
    monotonic: Callable[[], float] = time.monotonic
    sleep: Callable[[float], None] = time.sleep

    def preflight(
        self,
        active_policy: AzureContainerAppLiveProofPolicy,
        active_requirement: ModelServingRequirement,
    ) -> None:
        """Run the read-only capacity proof against the bound environment."""
        self.preflight_factory(
            self.credential,
            self.environment_transport,
            trace_sink=self.preflight_trace_sink,
        ).check(
            subscription_id=active_policy.subscription_id,
            resource_group=active_policy.resource_group,
            environment_name=active_policy.environment_name,
            workload_profile_name=active_policy.workload_profile_name,
            location=active_policy.location,
            requirement=active_requirement,
        )

    def _revision_readiness(
        self,
        token: str,
        document: object,
        active_policy: AzureContainerAppLiveProofPolicy,
        replica_diagnostics_available: bool,
    ) -> tuple[bool, str | None, bool]:
        revision_reader = getattr(self.app_transport, "get_revision_json", None)
        active_reader = getattr(self.app_transport, "get_active_revision_json", None)
        replica_reader = getattr(self.app_transport, "get_replica_status_json", None)
        revision_name = _ready_revision_name(document) if _ready(document) else None
        if revision_name is not None and callable(revision_reader):
            revision = revision_reader(token, revision_name)
        elif callable(active_reader):
            revision = active_reader(token)
        else:
            revision = None
        state = _revision_state(revision)
        if state.terminal:
            raise BackendInfrastructureError(BackendFailure.UNAVAILABLE)
        ready = state.ready(active_policy.min_replicas)
        observed_name = _observed_revision_name(revision, active_policy.app_name)
        if observed_name is not None and callable(replica_reader) and replica_diagnostics_available:
            ready, replica_diagnostics_available = self._replica_readiness(
                token, observed_name, state, replica_reader, active_policy.min_replicas
            )
        if not ready:
            self.progress_sink(
                "azure_containerapp_revision_poll phase=readiness state=heartbeat "
                f"provisioning_state={state.provisioning_state} "
                f"health_state={state.health_state} "
                f"running_state={state.running_state} replicas={state.replicas}"
            )
        return ready, observed_name, replica_diagnostics_available

    def _replica_readiness(
        self,
        token: str,
        revision_name: str,
        state: _RevisionState,
        reader: Callable[[str, str], object],
        minimum_replicas: int,
    ) -> tuple[bool, bool]:
        try:
            status = _replica_status(reader(token, revision_name))
        except AzureContainerAppsSDKReadError:
            self.progress_sink(
                "azure_containerapp_replica_poll phase=readiness "
                "state=supplementary_unavailable reason=sdk_read_failed"
            )
            return state.ready(minimum_replicas), False
        if status.replicas == 0 and state.ready(minimum_replicas):
            self.progress_sink(
                "azure_containerapp_replica_poll phase=readiness "
                "state=supplementary_unavailable reason=empty_inventory"
            )
            return True, False
        self.progress_sink(_replica_progress(status, minimum_replicas))
        if status.terminal:
            raise BackendInfrastructureError(BackendFailure.UNAVAILABLE)
        ready = bool(
            status.ready(minimum_replicas)
            and state.active
            and state.replicas >= minimum_replicas
            and state.health_state == "Healthy"
            and state.provisioning_state == "Provisioned"
        )
        return ready, True

    def read_app(
        self,
        active_policy: AzureContainerAppLiveProofPolicy,
        expect_absent: bool,
    ) -> object | None:
        """Poll one app until it is absent, ready, terminal, or timed out."""
        deadline = self.monotonic() + (600.0 if expect_absent else 900.0)
        document: object | None = None
        replica_diagnostics_available = True
        while True:
            token = self.credential.get_token(ARM_SCOPE).token
            document = self.app_transport.get_json(token)
            revision_ready = _ready(document)
            if not expect_absent and document is not None and active_policy.min_replicas > 0:
                revision_ready, revision_name, replica_diagnostics_available = (
                    self._revision_readiness(
                        token,
                        document,
                        active_policy,
                        replica_diagnostics_available,
                    )
                )
                if revision_ready and revision_name is not None:
                    document = _with_ready_revision(document, revision_name)
            if (expect_absent and document is None) or (not expect_absent and revision_ready):
                return document
            if self.monotonic() >= deadline:
                if not expect_absent:
                    raise BackendInfrastructureError(BackendFailure.TIMEOUT)
                return document
            app_state, has_ready_revision = _app_provisioning_state(document)
            phase = "absence" if expect_absent else "readiness"
            self.progress_sink(
                f"azure_containerapp_poll phase={phase} state=heartbeat "
                f"provisioning_state={app_state} "
                f"latest_ready_revision={str(has_ready_revision).lower()}"
            )
            self.sleep(10.0)

    def _validate_environment_policy(
        self, active_policy: AzureEnvironmentLifecyclePolicy
    ) -> None:
        if active_policy.environment_id.casefold() != self.policy.environment_id.casefold():
            raise RuntimeError("environment policy escaped the bound resource")

    def read_environment(
        self,
        active_policy: AzureEnvironmentLifecyclePolicy,
        expect_absent: bool,
    ) -> object | None:
        """Poll the exact environment without exposing provider-controlled fields."""
        self._validate_environment_policy(active_policy)
        deadline = self.monotonic() + (900.0 if expect_absent else 1_200.0)
        document: object | None = None
        while True:
            token = self.credential.get_token(ARM_SCOPE).token
            document = self.lifecycle_transport.get_environment(token)
            if (expect_absent and document is None) or (
                not expect_absent and (document is None or _environment_ready(document))
            ):
                return document
            if self.monotonic() >= deadline:
                return document
            phase = "absence" if expect_absent else "readiness"
            self.progress_sink(
                f"azure_containerapp_environment_poll phase={phase} state=heartbeat"
            )
            self.sleep(10.0)

    def list_environment_apps(
        self,
        active_policy: AzureEnvironmentLifecyclePolicy,
    ) -> tuple[str, ...]:
        """Wait until an owned app drains, returning unexpected inventory."""
        self._validate_environment_policy(active_policy)
        expected_app_id = self.policy.expected_resource_id.casefold()
        deadline = self.monotonic() + 300.0
        while True:
            token = self.credential.get_token(ARM_SCOPE).token
            app_ids = self.lifecycle_transport.list_environment_app_ids(token)
            if not app_ids or any(
                app_id.casefold() != expected_app_id for app_id in app_ids
            ):
                return cast(tuple[str, ...], app_ids)
            if self.monotonic() >= deadline:
                return cast(tuple[str, ...], app_ids)
            self.progress_sink(
                "azure_containerapp_environment_poll phase=inventory state=heartbeat"
            )
            self.sleep(10.0)


__all__ = ("AzureContainerAppRuntimeReaders",)
