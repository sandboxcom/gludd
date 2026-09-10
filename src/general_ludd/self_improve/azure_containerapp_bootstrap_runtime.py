"""Owned Azure runtime construction and secret-free lifecycle tracing."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from typing import Any, Protocol, cast

from general_ludd.azure.accelerator_credentials import (
    AzureAcceleratorAuthentication,
)
from general_ludd.azure.resource_group_bootstrap import (
    AzureResourceGroupBootstrapPolicy,
)
from general_ludd.infra.azure_containerapp_environment_lifecycle import (
    AzureEnvironmentLifecyclePolicy,
)
from general_ludd.infra.azure_containerapp_gpu import ModelServingRequirement
from general_ludd.infra.azure_containerapp_live_proof import (
    AzureContainerAppLiveProofPolicy,
)
from general_ludd.infra.azure_containerapp_owned_candidate import (
    OwnedCandidateLifecycleTrace,
    owned_candidate_deployment_digest,
)
from general_ludd.infra.azure_idle_retention import AzureIdleRetentionPolicy
from general_ludd.self_improve.azure_containerapp_bootstrap_credentials import (
    AzureCredentialProvider,
    release_once,
)
from general_ludd.self_improve.live_candidate_wiring import (
    ContainerAppCandidateBackend,
)


class AzureBootstrapRuntimeResources(Protocol):
    """Minimum composite resource lifecycle constructed by the Azure builder."""

    runtime: object
    environment_runtime: object
    backend_factory: object

    def close(self) -> None:
        """Release the complete composite resource lifecycle."""
        ...


def runtime_trace(
    progress_sink: Callable[[str], None],
    component: str,
    event: object,
) -> None:
    """Emit one allowlisted, secret-free Azure lifecycle event."""
    phase = getattr(event, "phase", getattr(event, "event", "unknown"))
    state = getattr(event, "state", "observed")
    digest = getattr(
        event,
        "operation_digest",
        getattr(event, "candidate_identity_digest", None),
    )
    elapsed = getattr(event, "elapsed_seconds", 0)
    failure_class = getattr(event, "failure_class", None)
    retention_digest = getattr(event, "retention_plan_digest", None)
    retention_seconds = getattr(event, "retention_seconds", 0)
    retention_hourly_cost = getattr(
        event,
        "retention_hourly_cost_microusd",
        0,
    )
    event_source = getattr(event, "event_source", None)
    allowed_fields = {
        "event_source": frozenset({"opentofu_ui", "azure_resource_manager"}),
        "resource_type": frozenset(
            {
                "azapi_resource",
                "microsoft.app/containerapps",
                "microsoft.app/managedenvironments",
            }
        ),
        "action": frozenset(
            {"noop", "create", "read", "update", "replace", "delete", "move"}
        ),
        "event_kind": frozenset(
            {
                "planned_change",
                "resource_drift",
                "apply_start",
                "apply_progress",
                "apply_complete",
                "apply_errored",
            }
        ),
        "provisioning_state": frozenset(
            {
                "succeeded",
                "failed",
                "canceled",
                "waiting",
                "in-progress",
                "provisioning",
                "deleting",
                "initialization-in-progress",
                "infrastructure-setup-in-progress",
                "infrastructure-setup-complete",
                "scheduled-for-delete",
                "upgrade-requested",
                "upgrade-failed",
            }
        ),
    }
    structured = ""
    if event_source in allowed_fields["event_source"]:
        values = {
            name: getattr(event, name, None)
            for name in (
                "event_source",
                "resource_type",
                "action",
                "event_kind",
                "provisioning_state",
            )
        }
        structured = " " + " ".join(
            f"{name}={value if value in allowed_fields[name] else 'none'}"
            for name, value in values.items()
        )
    progress_sink(
        "SELF_IMPROVE_AZURE_BOOTSTRAP "
        f"component={component} phase={phase} state={state} "
        f"operation_digest={digest or 'unbound'} elapsed_seconds={elapsed} "
        f"failure_class={failure_class or 'none'} "
        f"retention_plan_digest={retention_digest or 'none'} "
        f"retention_seconds={retention_seconds} "
        f"retention_hourly_cost_microusd={retention_hourly_cost}{structured} "
        "secret_output=false"
    )


class ConfiguredAzureContainerAppBootstrapFactory:
    """Create a fresh complete owned lifecycle for every approved trial scope."""

    def __init__(
        self,
        *,
        app_policy: AzureContainerAppLiveProofPolicy,
        environment_policy: AzureEnvironmentLifecyclePolicy,
        requirement: ModelServingRequirement,
        work_root: Path,
        environment_work_root: Path,
        credential_provider: AzureCredentialProvider,
        resource_group_bootstrapper: Callable[..., object],
        resources_builder: Callable[..., AzureBootstrapRuntimeResources],
        owned_factory_type: type[Any],
        progress_sink: Callable[[str], None],
        idle_retention_policy: AzureIdleRetentionPolicy,
        expected_next_demand_seconds: int | None,
    ) -> None:
        """Initialize immutable lifecycle inputs for fresh candidate sessions."""
        self._app_policy = app_policy
        self._environment_policy = environment_policy
        self._requirement = requirement
        self._work_root = work_root
        self._environment_work_root = environment_work_root
        self._credential_provider = credential_provider
        if not callable(resource_group_bootstrapper):
            raise ValueError("resource_group_bootstrapper must be callable")
        self._resource_group_bootstrapper = resource_group_bootstrapper
        self._resources_builder = resources_builder
        self._owned_factory_type = owned_factory_type
        self._progress_sink = progress_sink
        self._idle_retention_policy = idle_retention_policy
        self._expected_next_demand_seconds = expected_next_demand_seconds
        self._deployment_digest = owned_candidate_deployment_digest(
            app_policy,
            environment_policy,
            idle_retention_policy,
            expected_next_demand_seconds,
        )

    @property
    def deployment_digest(self) -> str:
        """Return the immutable app-and-environment desired-state digest."""
        return self._deployment_digest

    def _ensure_resource_group(
        self,
        credentials: AzureAcceleratorAuthentication,
        release: Callable[[], None],
    ) -> None:
        """Acquire the owned resource group or release the credential lease."""
        try:
            self._resource_group_bootstrapper(
                AzureResourceGroupBootstrapPolicy(
                    subscription_id=self._app_policy.subscription_id,
                    resource_group=self._app_policy.resource_group,
                    location=self._app_policy.location,
                    owner_digest=self._environment_policy.owner_digest,
                ),
                credentials,
                trace_sink=lambda event: runtime_trace(
                    self._progress_sink,
                    "resource_group",
                    event,
                ),
            )
        except BaseException:
            with suppress(Exception):
                release()
            raise RuntimeError(
                "Azure bootstrap resource-group acquisition failed"
            ) from None

    def __call__(self) -> ContainerAppCandidateBackend:
        """Acquire credentials and return one fully owned Azure backend."""
        self._progress_sink(
            "SELF_IMPROVE_AZURE_BOOTSTRAP phase=credential_acquire_started "
            f"operation_digest={self._deployment_digest} secret_output=false"
        )
        acquisition = self._credential_provider.acquire()
        if acquisition.credentials.subscription_id != self._app_policy.subscription_id:
            with suppress(Exception):
                acquisition.release()
            raise RuntimeError("Azure bootstrap credential scope mismatch")
        release = release_once(acquisition.release)
        self._progress_sink(
            "SELF_IMPROVE_AZURE_BOOTSTRAP phase=credential_acquired "
            f"operation_digest={self._deployment_digest} secret_output=false"
        )
        self._ensure_resource_group(acquisition.credentials, release)
        try:
            resources = self._resources_builder(
                credentials=acquisition.credentials,
                policy=self._app_policy,
                requirement=self._requirement,
                work_root=self._work_root,
                environment_work_root=self._environment_work_root,
                credential_release=release,
                preflight_trace_sink=lambda event: runtime_trace(
                    self._progress_sink,
                    "preflight",
                    event,
                ),
                terraform_trace_sink=lambda event: runtime_trace(
                    self._progress_sink,
                    "app_terraform",
                    event,
                ),
                environment_terraform_trace_sink=lambda event: runtime_trace(
                    self._progress_sink,
                    "environment_terraform",
                    event,
                ),
                backend_trace_sink=lambda event: runtime_trace(
                    self._progress_sink,
                    "backend",
                    event,
                ),
                progress_sink=lambda message: self._progress_sink(
                    "SELF_IMPROVE_AZURE_BOOTSTRAP component=arm "
                    f"{message} secret_output=false"
                ),
            )
        except BaseException:
            with suppress(Exception):
                release()
            raise RuntimeError("Azure bootstrap resource construction failed") from None

        def owned_trace(event: OwnedCandidateLifecycleTrace) -> None:
            runtime_trace(self._progress_sink, "owned_lifecycle", event)

        try:
            owner = self._owned_factory_type(
                app_policy=self._app_policy,
                environment_policy=self._environment_policy,
                environment_runtime=resources.environment_runtime,
                app_runtime=resources.runtime,
                backend_factory=resources.backend_factory,
                resource_release=resources.close,
                trace_sink=owned_trace,
                idle_retention_policy=self._idle_retention_policy,
                expected_next_demand_seconds=self._expected_next_demand_seconds,
            )
            if owner.deployment_digest != self._deployment_digest:
                resources.close()
                raise RuntimeError("Azure bootstrap configuration drift")
            return cast(ContainerAppCandidateBackend, owner())
        except RuntimeError as exc:
            if str(exc) == "Azure bootstrap configuration drift":
                raise
            with suppress(Exception):
                resources.close()
            raise RuntimeError("Azure bootstrap candidate acquisition failed") from None
        except BaseException:
            with suppress(Exception):
                resources.close()
            raise RuntimeError("Azure bootstrap candidate acquisition failed") from None


__all__ = (
    "AzureBootstrapRuntimeResources",
    "ConfiguredAzureContainerAppBootstrapFactory",
    "runtime_trace",
)
