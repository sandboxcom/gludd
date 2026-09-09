"""Direct Terraform runtime for one bounded Azure Container App proof.

The historical module name is retained as an import compatibility boundary.  The
runtime itself never invokes Make or a shell.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import Protocol, cast

from general_ludd.azure.accelerator_credentials import AzureAcceleratorCredentials
from general_ludd.infra.azure_containerapp_compute_config import (
    build_containerapp_compute_config,
)
from general_ludd.infra.azure_containerapp_gpu import (
    ModelServingRequirement,
)
from general_ludd.infra.azure_containerapp_live_proof import (
    AzureContainerAppDeploymentEvidence,
    AzureContainerAppLiveProofPolicy,
)
from general_ludd.infra.azure_containerapp_make_types import (
    AZURE_RESOURCE_MANAGER_TARGET,
    AzureContainerAppMakeRuntimeError,
    MakeRuntimeEvent,
    MakeRuntimeState,
    azure_provisioning_fact,
)
from general_ludd.infra.azure_containerapp_make_validation import (
    OWNERSHIP_MARKER as _OWNERSHIP_MARKER,
)
from general_ludd.infra.azure_containerapp_make_validation import (
    deployment_outputs as _deployment_outputs,
)
from general_ludd.infra.azure_containerapp_make_validation import (
    mapping,
    member,
    output_value,
    required_argument,
)
from general_ludd.infra.azure_containerapp_make_validation import (
    read_bounded_json as _read_bounded_json,
)
from general_ludd.infra.azure_containerapp_make_validation import (
    validate_app_document as _validate_app_document,
)
from general_ludd.infra.azure_containerapp_make_validation import (
    write_ownership_marker as _write_ownership_marker,
)
from general_ludd.infra.azure_containerapp_terraform_executor import (
    AzureContainerAppTerraformPhaseError,
    AzureContainerAppTerraformPhaseExecutor,
    TerraformRuntimeState,
    TerraformUIEvent,
    terraform_process_environment,
)
from general_ludd.infra.compute import ComputeConfig
from general_ludd.infra.terraform import TerraformGenerator

_mapping = mapping
_member = member
_output_value = output_value
_required_argument = required_argument

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


class _TerraformGenerator(Protocol):
    def materialize(
        self,
        config: ComputeConfig,
        destination: str | os.PathLike[str],
        *,
        deployment_name: str,
    ) -> Path: ...


PreflightCheck = Callable[
    [AzureContainerAppLiveProofPolicy, ModelServingRequirement],
    None,
]
ReadApp = Callable[[AzureContainerAppLiveProofPolicy, bool], object | None]


def _discard_trace(_event: MakeRuntimeEvent) -> None:
    return None


class AzureContainerAppTerraformRuntime:
    """Materialize and operate one app through direct Terraform/OpenTofu calls."""

    def __init__(
        self,
        *,
        work_root: str | os.PathLike[str],
        credentials: AzureAcceleratorCredentials,
        requirement: ModelServingRequirement,
        preflight_check: PreflightCheck,
        read_app: ReadApp,
        terraform_executor: _TerraformExecutor | None = None,
        terraform_generator: _TerraformGenerator | None = None,
        trace_sink: Callable[[MakeRuntimeEvent], None] = _discard_trace,
        heartbeat_seconds: float = 15.0,
    ) -> None:
        """Bind credentials, policy inputs, runners, and observable boundaries."""
        if not isinstance(credentials, AzureAcceleratorCredentials):
            raise ValueError("credentials must be AzureAcceleratorCredentials")
        if not isinstance(requirement, ModelServingRequirement):
            raise ValueError("requirement must be ModelServingRequirement")
        if not callable(preflight_check) or not callable(read_app):
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
        self._requirement = requirement
        self._preflight_check = preflight_check
        self._read_app = read_app
        self._executor = terraform_executor or AzureContainerAppTerraformPhaseExecutor(
            heartbeat_seconds=float(heartbeat_seconds),
            telemetry_sink=self._emit_terraform_ui,
        )
        self._generator = terraform_generator or TerraformGenerator()
        self._trace_sink = trace_sink
        self._heartbeat_seconds = float(heartbeat_seconds)
        self._policy_digest: str | None = None
        self._tf_dir: Path | None = None
        self._plan_file: Path | None = None
        self._plan_json: Path | None = None
        self._output_json: Path | None = None

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
        digest = self._policy_digest or "unbound"
        try:
            self._trace_sink(
                MakeRuntimeEvent(
                    phase=phase,
                    state=state,
                    operation_digest=digest,
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
            resource_type="microsoft.app/containerapps",
            action="read",
            provisioning_state=provisioning_state,
        )

    def _bind(self, policy: AzureContainerAppLiveProofPolicy) -> None:
        if not isinstance(policy, AzureContainerAppLiveProofPolicy):
            raise AzureContainerAppMakeRuntimeError("policy")
        if policy.subscription_id != self._credentials.subscription_id:
            raise AzureContainerAppMakeRuntimeError("credentials")
        if self._policy_digest is not None:
            if self._policy_digest != policy.operation_digest:
                raise AzureContainerAppMakeRuntimeError("policy-drift")
            return
        self._policy_digest = policy.operation_digest
        self._work_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self._work_root, 0o700)
        self._tf_dir = self._work_root / policy.operation_digest[:24]
        self._plan_file = self._tf_dir / "gludd.tfplan"
        self._plan_json = self._tf_dir / "gludd.plan.json"
        self._output_json = self._tf_dir / "gludd.output.json"

    def _paths(self) -> tuple[Path, Path, Path, Path]:
        if None in (self._tf_dir, self._plan_file, self._plan_json, self._output_json):
            raise AzureContainerAppMakeRuntimeError("not-planned")
        return cast(
            tuple[Path, Path, Path, Path],
            (self._tf_dir, self._plan_file, self._plan_json, self._output_json),
        )

    def _invoke(self, phase: str, *, json_file: Path, timeout_seconds: int) -> None:
        tf_dir, plan_file, _plan_json, _output_json = self._paths()
        credential_environment = self._credentials.arm_environment()
        credential_environment.update(
            {
                "CHECKPOINT_DISABLE": "1",
                "TF_IN_AUTOMATION": "1",
                "TF_INPUT": "0",
            }
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
                json_file=json_file,
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

    def plan(self, policy: AzureContainerAppLiveProofPolicy) -> object:
        """Materialize, initialize, validate, plan, and return bounded JSON."""
        self._bind(policy)
        tf_dir, _plan_file, plan_json, _output_json = self._paths()
        if not policy.app_name.startswith("gludd-vllm-"):
            raise AzureContainerAppMakeRuntimeError("app-name")
        deployment_name = policy.app_name.removeprefix("gludd-vllm-")
        try:
            materialized = self._generator.materialize(
                build_containerapp_compute_config(
                    policy,
                    self._requirement,
                    config_factory=ComputeConfig,
                ),
                tf_dir,
                deployment_name=deployment_name,
            )
            if Path(materialized).resolve() != tf_dir:
                raise ValueError
            _write_ownership_marker(
                tf_dir / _OWNERSHIP_MARKER,
                policy.operation_digest,
            )
        except AzureContainerAppMakeRuntimeError:
            raise
        except Exception:
            raise AzureContainerAppMakeRuntimeError("materialize") from None
        self._invoke("init", json_file=plan_json, timeout_seconds=300)
        self._invoke("validate", json_file=plan_json, timeout_seconds=120)
        self._invoke("plan", json_file=plan_json, timeout_seconds=600)
        plan_json.unlink(missing_ok=True)
        self._invoke("show-plan", json_file=plan_json, timeout_seconds=120)
        return _read_bounded_json(plan_json, phase="show-plan")

    def preflight(self, policy: AzureContainerAppLiveProofPolicy) -> None:
        """Run the exact named-environment sizing and quota proof."""
        self._bind(policy)
        try:
            self._preflight_check(policy, self._requirement)
        except Exception:
            raise AzureContainerAppMakeRuntimeError("preflight") from None

    def apply(
        self,
        policy: AzureContainerAppLiveProofPolicy,
    ) -> AzureContainerAppDeploymentEvidence:
        """Apply the audited saved plan and bind Terraform outputs to ARM truth."""
        self._bind(policy)
        _tf_dir, _plan_file, _plan_json, output_json = self._paths()
        self._invoke("apply", json_file=output_json, timeout_seconds=3_600)
        output_json.unlink(missing_ok=True)
        self._invoke("output", json_file=output_json, timeout_seconds=120)
        evidence = _deployment_outputs(
            _read_bounded_json(output_json, phase="output"),
            policy,
        )
        try:
            document = self._read_app(policy, False)
        except Exception:
            raise AzureContainerAppMakeRuntimeError("deployment-evidence") from None
        _validate_app_document(document, policy, evidence)
        self._emit_azure_state(document)
        return evidence

    def destroy(self, policy: AzureContainerAppLiveProofPolicy) -> None:
        """Destroy only resources recorded in this one app-owned state file."""
        self._bind(policy)
        _tf_dir, _plan_file, _plan_json, output_json = self._paths()
        self._invoke("destroy", json_file=output_json, timeout_seconds=900)

    def exists(self, policy: AzureContainerAppLiveProofPolicy) -> bool:
        """Return whether the exact app remains visible after destroy."""
        self._bind(policy)
        try:
            return self._read_app(policy, True) is not None
        except Exception:
            raise AzureContainerAppMakeRuntimeError("absence") from None


AzureContainerAppMakeRuntime = AzureContainerAppTerraformRuntime


__all__ = (
    "AzureContainerAppMakeRuntime",
    "AzureContainerAppMakeRuntimeError",
    "AzureContainerAppTerraformRuntime",
    "MakeRuntimeEvent",
    "MakeRuntimeState",
)
