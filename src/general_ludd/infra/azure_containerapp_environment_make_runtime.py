"""Make-mediated Terraform runtime for one Gludd-owned Azure environment.

Terraform is the only mutation boundary. Independent ARM readers are injected
for inspection, readiness, application inventory, and absence proof.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from pathlib import Path
from typing import Protocol, cast

from general_ludd.azure.accelerator_credentials import AzureAcceleratorCredentials
from general_ludd.commands.make import MakeResult, MakeRunner
from general_ludd.infra.azure_containerapp_environment_lifecycle import (
    AzureEnvironmentLifecyclePolicy,
)
from general_ludd.infra.azure_containerapp_environment_materializer import (
    AzureContainerAppEnvironmentTerraformMaterializer,
    verify_existing_state_boundary,
)
from general_ludd.infra.azure_containerapp_make_types import (
    MAKE_TARGET,
    AzureContainerAppMakeRuntimeError,
    MakeRuntimeEvent,
    MakeRuntimeState,
)
from general_ludd.infra.azure_containerapp_make_validation import (
    OWNERSHIP_MARKER,
    read_bounded_json,
    write_ownership_marker,
)


class _MakeRunner(Protocol):
    def run(
        self,
        target: str,
        *,
        extra_args: list[str] | None = None,
        timeout_s: int | None = None,
        env_extra: dict[str, str] | None = None,
        stream: bool = False,
        stream_callback: Callable[[str], None] | None = None,
    ) -> MakeResult: ...


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


class AzureContainerAppEnvironmentMakeRuntime:
    """Operate one stable, owner-bound environment Terraform state via Make."""

    def __init__(
        self,
        *,
        repo_root: str | os.PathLike[str],
        work_root: str | os.PathLike[str],
        credentials: AzureAcceleratorCredentials,
        read_environment: ReadEnvironment,
        list_environment_apps: ListEnvironmentApps,
        make_runner: _MakeRunner | None = None,
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
        self._repo_root = Path(repo_root).resolve()
        self._work_root = Path(work_root).resolve()
        self._credentials = credentials
        self._read_environment = read_environment
        self._list_environment_apps = list_environment_apps
        self._runner = make_runner or MakeRunner(self._repo_root)
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
    ) -> None:
        try:
            self._trace_sink(
                MakeRuntimeEvent(
                    phase=phase,
                    state=state,
                    operation_digest=self._current_operation_digest or "unbound",
                    elapsed_seconds=elapsed_seconds,
                )
            )
        except Exception:
            raise AzureContainerAppMakeRuntimeError("trace") from None

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
        arguments = [
            f"AZURE_CONTAINERAPP_TF_PHASE={phase}",
            f"AZURE_CONTAINERAPP_TF_DIR={tf_dir}",
            f"AZURE_CONTAINERAPP_TF_PLAN_FILE={plan_file}",
            f"AZURE_CONTAINERAPP_TF_JSON_FILE={plan_json}",
            "AZURE_CONTAINERAPP_TF_VALIDATE_ONLY=0",
        ]
        environment = self._credentials.arm_environment()
        environment.update(
            {"CHECKPOINT_DISABLE": "1", "TF_IN_AUTOMATION": "1", "TF_INPUT": "0"}
        )
        started = time.monotonic()
        self._emit(phase, MakeRuntimeState.STARTED)
        try:
            with ThreadPoolExecutor(
                max_workers=1,
                thread_name_prefix="gludd-azure-containerapp-environment",
            ) as executor:
                future = executor.submit(
                    self._runner.run,
                    MAKE_TARGET,
                    extra_args=arguments,
                    timeout_s=timeout_seconds,
                    env_extra=environment,
                )
                while True:
                    try:
                        result = future.result(timeout=self._heartbeat_seconds)
                        break
                    except FutureTimeoutError:
                        self._emit(
                            phase,
                            MakeRuntimeState.HEARTBEAT,
                            elapsed_seconds=int(time.monotonic() - started),
                        )
        except AzureContainerAppMakeRuntimeError:
            raise
        except Exception:
            self._emit(phase, MakeRuntimeState.FAILED)
            raise AzureContainerAppMakeRuntimeError(phase) from None
        elapsed = int(time.monotonic() - started)
        if not result.success:
            self._emit(phase, MakeRuntimeState.FAILED, elapsed_seconds=elapsed)
            raise AzureContainerAppMakeRuntimeError(phase)
        self._emit(phase, MakeRuntimeState.SUCCEEDED, elapsed_seconds=elapsed)

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
            return self._read_environment(policy, expect_absent)
        except Exception:
            raise AzureContainerAppMakeRuntimeError("environment-read") from None

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


__all__ = (
    "AzureContainerAppEnvironmentMakeRuntime",
    "AzureContainerAppEnvironmentTerraformMaterializer",
    "AzureContainerAppMakeRuntimeError",
    "MakeRuntimeEvent",
    "MakeRuntimeState",
)
