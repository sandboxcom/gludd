"""Direct Terraform runtime for one Gludd-owned Azure environment.

Terraform is the only mutation boundary. Independent ARM readers are injected
for inspection, readiness, application inventory, and absence proof.  The
historical module name remains only for import compatibility.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import Protocol, cast

from general_ludd.azure.accelerator_credentials import AzureAcceleratorCredentials
from general_ludd.infra.azure_containerapp_environment_lifecycle import (
    AzureEnvironmentLifecyclePolicy,
)
from general_ludd.infra.azure_containerapp_environment_materializer import (
    AzureContainerAppEnvironmentTerraformMaterializer,
    verify_existing_state_boundary,
)
from general_ludd.infra.azure_containerapp_make_types import (
    AZURE_RESOURCE_MANAGER_TARGET,
    AzureContainerAppMakeRuntimeError,
    MakeRuntimeEvent,
    MakeRuntimeState,
    azure_provisioning_fact,
)
from general_ludd.infra.azure_containerapp_make_validation import (
    OWNERSHIP_MARKER,
    read_bounded_json,
    write_ownership_marker,
)
from general_ludd.infra.azure_containerapp_terraform_executor import (
    AzureContainerAppTerraformPhaseError,
    AzureContainerAppTerraformPhaseExecutor,
    TerraformRuntimeState,
    TerraformUIEvent,
    terraform_process_environment,
)


class _TerraformExecutor(Protocol):
    def run(
        self,
        *,
        phase: str,
        terraform_dir: str | os.PathLike[str],
        plan_file: str | os.PathLike[str],
        json_file: str | os.PathLike[str],
        allowed_root: str | os.PathLike[str],
        environment: dict[str, str],
        timeout_seconds: int,
        progress: Callable[[str, TerraformRuntimeState, int], None],
    ) -> None: ...


class _EnvironmentTerraformMaterializer(Protocol):
    def materialize(
        self,
        policy: AzureEnvironmentLifecyclePolicy,
        destination: str | os.PathLike[str],
    ) -> Path: ...


ReadEnvironment = Callable[[AzureEnvironmentLifecyclePolicy, bool], object | None]
ListEnvironmentApps = Callable[[AzureEnvironmentLifecyclePolicy], tuple[str, ...]]


def _discard_trace(_event: MakeRuntimeEvent) -> None:
    return None


class AzureContainerAppEnvironmentTerraformRuntime:
    """Operate one stable owner-bound state directly through Terraform/OpenTofu."""

    def __init__(
        self,
        *,
        work_root: str | os.PathLike[str],
        credentials: AzureAcceleratorCredentials,
        read_environment: ReadEnvironment,
        list_environment_apps: ListEnvironmentApps,
        terraform_executor: _TerraformExecutor | None = None,
        terraform_materializer: _EnvironmentTerraformMaterializer | None = None,
        trace_sink: Callable[[MakeRuntimeEvent], None] = _discard_trace,
        heartbeat_seconds: float = 15.0,
    ) -> None:
        """Bind secret-bearing execution and independent read boundaries."""
        if not isinstance(credentials, AzureAcceleratorCredentials):
            raise ValueError("credentials must be AzureAcceleratorCredentials")
        if not callable(read_environment) or not callable(list_environment_apps):
            raise ValueError("Azure read boundaries must be callable")
        if not callable(trace_sink):
            raise ValueError("trace_sink must be callable")
        if (
            isinstance(heartbeat_seconds, bool)
            or not isinstance(heartbeat_seconds, (int, float))
            or not 0.0 < float(heartbeat_seconds) <= 60.0
        ):
            raise ValueError("heartbeat_seconds must be in 0..60")
        self._work_root = Path(work_root).resolve()
        self._credentials = credentials
        self._read_environment = read_environment
        self._list_environment_apps = list_environment_apps
        self._executor = terraform_executor or AzureContainerAppTerraformPhaseExecutor(
            heartbeat_seconds=float(heartbeat_seconds),
            telemetry_sink=self._emit_terraform_ui,
        )
        self._materializer = (
            terraform_materializer
            or AzureContainerAppEnvironmentTerraformMaterializer()
        )
        self._trace_sink = trace_sink
        self._heartbeat_seconds = float(heartbeat_seconds)
        self._state_digest: str | None = None
        self._current_operation_digest: str | None = None
        self._planned_operation_digest: str | None = None
        self._materialized_operation_digest: str | None = None
        self._tf_dir: Path | None = None
        self._plan_file: Path | None = None
        self._plan_json: Path | None = None

    def _emit(
        self,
        phase: str,
        state: MakeRuntimeState,
        *,
        elapsed_seconds: int = 0,
        target: str = "terraform",
        event_source: str | None = None,
        resource_type: str | None = None,
        action: str | None = None,
        event_kind: str | None = None,
        provisioning_state: str | None = None,
    ) -> None:
        try:
            self._trace_sink(
                MakeRuntimeEvent(
                    phase=phase,
                    state=state,
                    operation_digest=self._current_operation_digest or "unbound",
                    elapsed_seconds=elapsed_seconds,
                    target=target,
                    event_source=event_source,
                    resource_type=resource_type,
                    action=action,
                    event_kind=event_kind,
                    provisioning_state=provisioning_state,
                )
            )
        except Exception:
            raise AzureContainerAppMakeRuntimeError("trace") from None

    def _emit_terraform_ui(self, event: TerraformUIEvent) -> None:
        if not isinstance(event, TerraformUIEvent):
            raise AzureContainerAppMakeRuntimeError("trace")
        self._emit(
            event.phase,
            MakeRuntimeState(event.state.value),
            elapsed_seconds=event.elapsed_seconds,
            event_source="opentofu_ui",
            resource_type=event.resource_type,
            action=event.action,
            event_kind=event.event_kind,
        )

    def _emit_azure_state(self, document: object) -> None:
        fact = azure_provisioning_fact(document)
        if fact is None:
            return
        provisioning_state, state = fact
        self._emit(
            "azure-resource-state",
            state,
            target=AZURE_RESOURCE_MANAGER_TARGET,
            event_source="azure_resource_manager",
            resource_type="microsoft.app/managedenvironments",
            action="read",
            provisioning_state=provisioning_state,
        )

    def _bind_state(self, policy: AzureEnvironmentLifecyclePolicy) -> None:
        if not isinstance(policy, AzureEnvironmentLifecyclePolicy):
            raise AzureContainerAppMakeRuntimeError("policy")
        if policy.subscription_id != self._credentials.subscription_id:
            raise AzureContainerAppMakeRuntimeError("credentials")
        if self._state_digest is not None and self._state_digest != policy.state_digest:
            raise AzureContainerAppMakeRuntimeError("state-identity")
        self._state_digest = policy.state_digest
        self._current_operation_digest = policy.operation_digest
        if self._tf_dir is not None:
            return
        try:
            self._work_root.mkdir(mode=0o700, parents=True, exist_ok=True)
            os.chmod(self._work_root, 0o700)
        except OSError:
            raise AzureContainerAppMakeRuntimeError("work-root") from None
        self._tf_dir = self._work_root / policy.state_digest[:24]
        self._plan_file = self._tf_dir / "gludd.tfplan"
        self._plan_json = self._tf_dir / "gludd.plan.json"

    def _paths(self) -> tuple[Path, Path, Path]:
        if None in (self._tf_dir, self._plan_file, self._plan_json):
            raise AzureContainerAppMakeRuntimeError("not-bound")
        return cast(
            tuple[Path, Path, Path],
            (self._tf_dir, self._plan_file, self._plan_json),
        )

    def _materialize_policy(self, policy: AzureEnvironmentLifecyclePolicy) -> None:
        if self._materialized_operation_digest == policy.operation_digest:
            return
        tf_dir, _plan_file, _plan_json = self._paths()
        verify_existing_state_boundary(tf_dir, policy.state_digest)
        try:
            materialized = self._materializer.materialize(policy, tf_dir)
            if Path(materialized).resolve() != tf_dir:
                raise ValueError
            write_ownership_marker(tf_dir / OWNERSHIP_MARKER, policy.state_digest)
            self._materialized_operation_digest = policy.operation_digest
        except AzureContainerAppMakeRuntimeError:
            raise
        except Exception:
            raise AzureContainerAppMakeRuntimeError("materialize") from None

    def _invoke(self, phase: str, *, timeout_seconds: int) -> None:
        tf_dir, plan_file, plan_json = self._paths()
        credential_environment = self._credentials.arm_environment()
        credential_environment.update(
            {"CHECKPOINT_DISABLE": "1", "TF_IN_AUTOMATION": "1", "TF_INPUT": "0"}
        )
        environment = terraform_process_environment(credential_environment)
        failed_emitted = False

        def progress(
            active_phase: str,
            state: TerraformRuntimeState,
            elapsed_seconds: int,
        ) -> None:
            nonlocal failed_emitted
            failed_emitted = failed_emitted or state is TerraformRuntimeState.FAILED
            self._emit(
                active_phase,
                MakeRuntimeState(state.value),
                elapsed_seconds=elapsed_seconds,
            )

        try:
            self._executor.run(
                phase=phase,
                terraform_dir=tf_dir,
                plan_file=plan_file,
                json_file=plan_json,
                allowed_root=self._work_root,
                environment=environment,
                timeout_seconds=timeout_seconds,
                progress=progress,
            )
        except AzureContainerAppMakeRuntimeError:
            raise
        except AzureContainerAppTerraformPhaseError:
            if not failed_emitted:
                self._emit(phase, MakeRuntimeState.FAILED)
            raise AzureContainerAppMakeRuntimeError(phase) from None
        except Exception:
            if not failed_emitted:
                self._emit(phase, MakeRuntimeState.FAILED)
            raise AzureContainerAppMakeRuntimeError(phase) from None

    def read_environment(
        self,
        policy: AzureEnvironmentLifecyclePolicy,
        *,
        expect_absent: bool,
    ) -> object | None:
        """Read or await the exact environment through the independent ARM reader."""
        self._bind_state(policy)
        if not isinstance(expect_absent, bool):
            raise AzureContainerAppMakeRuntimeError("environment-read")
        try:
            document = self._read_environment(policy, expect_absent)
        except Exception:
            raise AzureContainerAppMakeRuntimeError("environment-read") from None
        self._emit_azure_state(document)
        return document

    def list_environment_apps(
        self,
        policy: AzureEnvironmentLifecyclePolicy,
    ) -> tuple[str, ...]:
        """List exact app IDs through the independent ARM inventory reader."""
        self._bind_state(policy)
        try:
            return self._list_environment_apps(policy)
        except Exception:
            raise AzureContainerAppMakeRuntimeError("app-inventory") from None

    def plan(self, policy: AzureEnvironmentLifecyclePolicy) -> object:
        """Materialize, initialize, validate, save, and decode one plan."""
        self._bind_state(policy)
        self._materialize_policy(policy)
        _tf_dir, _plan_file, plan_json = self._paths()
        self._planned_operation_digest = None
        for phase, timeout_seconds in (
            ("init", 300),
            ("validate", 120),
            ("plan", 600),
        ):
            self._invoke(phase, timeout_seconds=timeout_seconds)
        plan_json.unlink(missing_ok=True)
        self._invoke("show-plan", timeout_seconds=120)
        result = read_bounded_json(plan_json, phase="show-plan")
        self._planned_operation_digest = policy.operation_digest
        return result

    def apply(self, policy: AzureEnvironmentLifecyclePolicy) -> None:
        """Apply only the saved plan bound to the exact current desired state."""
        self._bind_state(policy)
        if self._planned_operation_digest != policy.operation_digest:
            raise AzureContainerAppMakeRuntimeError("policy-drift")
        self._invoke("apply", timeout_seconds=3_600)

    def destroy(self, policy: AzureEnvironmentLifecyclePolicy) -> None:
        """Destroy only resources in this stable owner/resource state boundary."""
        self._bind_state(policy)
        self._materialize_policy(policy)
        self._invoke("destroy", timeout_seconds=900)


AzureContainerAppEnvironmentMakeRuntime = AzureContainerAppEnvironmentTerraformRuntime


__all__ = (
    "AzureContainerAppEnvironmentMakeRuntime",
    "AzureContainerAppEnvironmentTerraformMaterializer",
    "AzureContainerAppEnvironmentTerraformRuntime",
    "AzureContainerAppMakeRuntimeError",
    "MakeRuntimeEvent",
    "MakeRuntimeState",
)
