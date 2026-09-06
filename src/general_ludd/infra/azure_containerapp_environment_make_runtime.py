"""Make-mediated Terraform runtime for one Gludd-owned Azure environment.

Terraform is the only mutation boundary.  Independent ARM readers are injected
for inspection, readiness, application inventory, and absence proof; credentials
are passed only in the child environment and never included in arguments or
events.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
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

_STACK_NAME = "azure-container-app-environment"
_MODULE_NAME = "azure-container-app-environment"
_STACK_SOURCE = '../../modules/azure-container-app-environment'
_RUNTIME_SOURCE = './modules/azure-container-app-environment'
_OWNERSHIP_PROTOCOL = "gludd-azure-containerapp-live-proof-v1"
_MAX_MARKER_BYTES = 4096


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


def _terraform_assets_root() -> Path:
    source_tree = Path(__file__).resolve().parents[3] / "infra" / "terraform"
    if source_tree.is_dir():
        return source_tree
    packaged = Path(__file__).resolve().parents[1] / "terraform"
    if packaged.is_dir():
        return packaged
    raise AzureContainerAppMakeRuntimeError("assets")


def _require_regular_source(path: Path) -> None:
    try:
        metadata = path.lstat()
    except OSError:
        raise AzureContainerAppMakeRuntimeError("assets") from None
    if not stat.S_ISREG(metadata.st_mode) or path.is_symlink():
        raise AzureContainerAppMakeRuntimeError("assets")


def _write_private_json(path: Path, value: object) -> None:
    payload = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    descriptor: int | None = None
    try:
        temporary.unlink(missing_ok=True)
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0),
            0o600,
        )
        os.write(descriptor, payload)
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        os.replace(temporary, path)
    except OSError:
        raise AzureContainerAppMakeRuntimeError("materialize") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


def _verify_existing_state_boundary(path: Path, state_digest: str) -> None:
    """Refuse to adopt a nonempty Terraform root without exact ownership."""
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return
    except OSError:
        raise AzureContainerAppMakeRuntimeError("state-ownership") from None
    if not stat.S_ISDIR(metadata.st_mode) or path.is_symlink():
        raise AzureContainerAppMakeRuntimeError("state-ownership")
    try:
        if next(path.iterdir(), None) is None:
            return
        marker_path = path / OWNERSHIP_MARKER
        marker_metadata = marker_path.lstat()
        if (
            not stat.S_ISREG(marker_metadata.st_mode)
            or marker_path.is_symlink()
            or marker_metadata.st_size <= 0
            or marker_metadata.st_size > _MAX_MARKER_BYTES
            or marker_metadata.st_mode & 0o077
        ):
            raise ValueError
        marker = read_bounded_json(marker_path, phase="state-ownership")
        if (
            not isinstance(marker, dict)
            or set(marker) != {"operation_digest", "protocol"}
            or marker.get("operation_digest") != state_digest
            or marker.get("protocol") != _OWNERSHIP_PROTOCOL
        ):
            raise ValueError
    except AzureContainerAppMakeRuntimeError:
        raise
    except (OSError, ValueError):
        raise AzureContainerAppMakeRuntimeError("state-ownership") from None


class AzureContainerAppEnvironmentTerraformMaterializer:
    """Materialize the reviewed environment stack without its remote backend."""

    def __init__(self, assets_root: str | os.PathLike[str] | None = None) -> None:
        """Bind an optional test asset root; production resolves packaged assets."""
        self._assets_root = (
            Path(assets_root).resolve() if assets_root is not None else None
        )

    def materialize(
        self,
        policy: AzureEnvironmentLifecyclePolicy,
        destination: str | os.PathLike[str],
    ) -> Path:
        """Copy exact reviewed HCL and atomically refresh public desired values."""
        if not isinstance(policy, AzureEnvironmentLifecyclePolicy):
            raise AzureContainerAppMakeRuntimeError("policy")
        destination_path = Path(destination).resolve()
        try:
            destination_path.mkdir(mode=0o700, parents=True, exist_ok=True)
            os.chmod(destination_path, 0o700)
        except OSError:
            raise AzureContainerAppMakeRuntimeError("materialize") from None
        assets = self._assets_root or _terraform_assets_root()
        stack = assets / "stacks" / _STACK_NAME
        module = assets / "modules" / _MODULE_NAME
        sources = [
            *(stack / name for name in ("main.tf", "variables.tf", "outputs.tf")),
            *(module / name for name in ("main.tf", "variables.tf", "outputs.tf")),
        ]
        for source in sources:
            _require_regular_source(source)
        try:
            stack_main = (stack / "main.tf").read_text(encoding="utf-8")
            if stack_main.count(_STACK_SOURCE) != 1:
                raise ValueError
            stack_main = stack_main.replace(_STACK_SOURCE, _RUNTIME_SOURCE)
            (destination_path / "main.tf").write_text(
                stack_main,
                encoding="utf-8",
            )
            for name in ("variables.tf", "outputs.tf"):
                shutil.copy2(stack / name, destination_path / name)
            module_destination = destination_path / "modules" / _MODULE_NAME
            module_destination.mkdir(mode=0o700, parents=True, exist_ok=True)
            for name in ("main.tf", "variables.tf", "outputs.tf"):
                shutil.copy2(module / name, module_destination / name)
        except (OSError, UnicodeError, ValueError):
            raise AzureContainerAppMakeRuntimeError("materialize") from None
        tfvars = {
            "environment_name": policy.environment_name,
            "resource_group_id": policy.resource_group_id,
            "region": policy.location,
            "workload_profiles": [
                {
                    "profile_name": profile.profile_name,
                    "workload_profile_type": profile.workload_profile_type,
                }
                for profile in policy.profiles
            ],
            "owner_digest": policy.owner_digest,
            "plan_digest": policy.plan_digest,
            "expires_at_utc": policy.expires_at_utc,
        }
        _write_private_json(destination_path / "terraform.tfvars.json", tfvars)
        return destination_path


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
        _verify_existing_state_boundary(tf_dir, policy.state_digest)
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
            {
                "CHECKPOINT_DISABLE": "1",
                "TF_IN_AUTOMATION": "1",
                "TF_INPUT": "0",
            }
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
        if not result.success:
            self._emit(
                phase,
                MakeRuntimeState.FAILED,
                elapsed_seconds=int(time.monotonic() - started),
            )
            raise AzureContainerAppMakeRuntimeError(phase)
        self._emit(
            phase,
            MakeRuntimeState.SUCCEEDED,
            elapsed_seconds=int(time.monotonic() - started),
        )

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
        self._invoke("init", timeout_seconds=300)
        self._invoke("validate", timeout_seconds=120)
        self._invoke("plan", timeout_seconds=600)
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
