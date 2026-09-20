"""Owned OpenTofu infrastructure adapter for Azure GPU VM model workers."""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path

from general_ludd.azure.accelerator_credential_source import (
    AzureAcceleratorCredentialLease,
)
from general_ludd.azure.accelerator_credentials import AzureAcceleratorCredentials
from general_ludd.infra.azure_containerapp_terraform_executor import (
    AzureContainerAppTerraformPhaseExecutor,
    TerraformRuntimeState,
    terraform_process_environment,
)
from general_ludd.infra.azure_gpu_vm_strategy import AzureExecutionStrategy
from general_ludd.infra.azure_gpu_worker_materializer import (
    AzureGpuWorkerProvisioningSpec,
    AzureGpuWorkerTerraformMaterializer,
)
from general_ludd.infra.azure_gpu_worker_runtime_support import (
    AzureGpuWorkerRuntimeError,
    AzureGpuWorkerRuntimeTrace,
    _audit_plan,
    _CredentialSource,
    _discard_trace,
    _mapping,
    _Materializer,
    _operation_digest,
    _output_value,
    _owned_ids,
    _parse_single_vm_outputs,
    _read_json,
    _SdkReader,
    _TerraformExecutor,
)
from general_ludd.infra.azure_gpu_worker_sdk import AzureGpuWorkerSdkReader
from general_ludd.infra.model_worker_lifecycle import (
    ModelWorkerHost,
    ModelWorkerLifecyclePolicy,
    ProvisionedModelWorkerPool,
)


class AzureGpuWorkerInfrastructureRuntime:
    """Provision, read back, destroy, and prove absence of one Azure worker pool."""

    def __init__(
        self,
        *,
        spec: AzureGpuWorkerProvisioningSpec,
        work_root: str | os.PathLike[str],
        credential_source: _CredentialSource,
        terraform_executor: _TerraformExecutor | None = None,
        materializer: _Materializer | None = None,
        sdk_reader: _SdkReader | None = None,
        trace_sink: Callable[[AzureGpuWorkerRuntimeTrace], None] = _discard_trace,
    ) -> None:
        """Bind one immutable desired state and exact ownership boundaries."""
        if not isinstance(spec, AzureGpuWorkerProvisioningSpec):
            raise ValueError("spec must be AzureGpuWorkerProvisioningSpec")
        if not callable(getattr(credential_source, "acquire", None)) or not callable(
            getattr(credential_source, "release", None)
        ):
            raise ValueError("credential_source must own acquire and release")
        if not callable(trace_sink):
            raise ValueError("trace_sink must be callable")
        self._spec = spec
        self._work_root = Path(os.path.abspath(os.fspath(work_root)))
        self._credential_source = credential_source
        self._executor = terraform_executor or AzureContainerAppTerraformPhaseExecutor(
            telemetry_sink=lambda _event: None
        )
        self._materializer = materializer or AzureGpuWorkerTerraformMaterializer()
        self._sdk = sdk_reader or AzureGpuWorkerSdkReader()
        self._trace_sink = trace_sink
        self._operation_digest: str | None = None
        self._terraform_dir: Path | None = None
        self._deployment: ProvisionedModelWorkerPool | None = None

    @property
    def operation_digest(self) -> str:
        """Return the bound policy-and-infrastructure operation digest."""
        if self._operation_digest is None:
            raise AzureGpuWorkerRuntimeError("unbound")
        return self._operation_digest

    def _bind(self, policy: ModelWorkerLifecyclePolicy) -> None:
        if not isinstance(policy, ModelWorkerLifecyclePolicy):
            raise AzureGpuWorkerRuntimeError("policy")
        if policy.launch_plan.replica_count != self._spec.instance_count:
            raise AzureGpuWorkerRuntimeError("policy")
        digest = _operation_digest(self._spec, policy)
        if self._operation_digest is not None:
            if self._operation_digest != digest:
                raise AzureGpuWorkerRuntimeError("policy-drift")
            return
        if self._work_root.is_symlink():
            raise AzureGpuWorkerRuntimeError("state-ownership")
        try:
            self._work_root.mkdir(mode=0o700, parents=True, exist_ok=True)
            os.chmod(self._work_root, 0o700)
        except OSError:
            raise AzureGpuWorkerRuntimeError("state-ownership") from None
        self._operation_digest = digest
        self._terraform_dir = self._work_root / digest[:24]

    def _paths(self) -> tuple[Path, Path, Path, Path]:
        if self._terraform_dir is None:
            raise AzureGpuWorkerRuntimeError("unbound")
        return (
            self._terraform_dir,
            self._terraform_dir / "gludd.tfplan",
            self._terraform_dir / "gludd.plan.json",
            self._terraform_dir / "gludd.output.json",
        )

    def _emit(
        self,
        phase: str,
        state: TerraformRuntimeState,
        elapsed_seconds: int,
    ) -> None:
        try:
            self._trace_sink(
                AzureGpuWorkerRuntimeTrace(
                    phase=phase,
                    state=state,
                    operation_digest=self.operation_digest,
                    elapsed_seconds=elapsed_seconds,
                )
            )
        except AzureGpuWorkerRuntimeError:
            raise
        except Exception:
            raise AzureGpuWorkerRuntimeError("trace") from None

    @contextmanager
    def _credentials(self) -> Iterator[AzureAcceleratorCredentials]:
        try:
            lease = self._credential_source.acquire()
        except Exception:
            raise AzureGpuWorkerRuntimeError("credentials") from None
        if not isinstance(lease, AzureAcceleratorCredentialLease):
            raise AzureGpuWorkerRuntimeError("credentials")
        if lease.credentials.subscription_id != self._spec.subscription_id:
            try:
                self._credential_source.release(lease)
            except Exception:
                raise AzureGpuWorkerRuntimeError("credential-release") from None
            raise AzureGpuWorkerRuntimeError("credentials")
        try:
            yield lease.credentials
        finally:
            try:
                self._credential_source.release(lease)
            except Exception:
                raise AzureGpuWorkerRuntimeError("credential-release") from None

    def _invoke(self, phase: str, *, json_file: Path, timeout_seconds: int) -> None:
        terraform_dir, plan_file, _plan_json, _output_json = self._paths()
        try:
            with self._credentials() as credentials:
                environment = credentials.arm_environment()
                environment.update(
                    {
                        "CHECKPOINT_DISABLE": "1",
                        "TF_IN_AUTOMATION": "1",
                        "TF_INPUT": "0",
                    }
                )
                self._executor.run(
                    phase=phase,
                    terraform_dir=terraform_dir,
                    plan_file=plan_file,
                    json_file=json_file,
                    allowed_root=self._work_root,
                    environment=terraform_process_environment(environment),
                    timeout_seconds=timeout_seconds,
                    progress=self._emit,
                )
        except AzureGpuWorkerRuntimeError:
            raise
        except Exception:
            raise AzureGpuWorkerRuntimeError(phase) from None

    def _parse_single_vm(
        self,
        outputs: Mapping[str, object],
    ) -> ProvisionedModelWorkerPool:
        return _parse_single_vm_outputs(
            outputs,
            spec=self._spec,
            operation_digest=self.operation_digest,
        )

    def _parse_vmss(
        self,
        outputs: Mapping[str, object],
    ) -> ProvisionedModelWorkerPool:
        scale_set_id = _output_value(outputs, "virtual_machine_scale_set_id")
        user = _output_value(outputs, "ansible_user")
        port = _output_value(outputs, "inference_port")
        owned = _owned_ids(
            _output_value(outputs, "owned_resource_ids"),
            self._spec.resource_group_id,
        )
        if (
            not isinstance(scale_set_id, str)
            or scale_set_id not in owned
            or user != self._spec.admin_username
            or isinstance(port, bool)
            or port != self._spec.inference_port
        ):
            raise ValueError
        with self._credentials() as credentials:
            instances = self._sdk.resolve_vmss_instances(
                credentials=credentials,
                spec=self._spec,
                scale_set_id=scale_set_id,
            )
        if len(instances) != self._spec.instance_count:
            raise ValueError
        hosts = tuple(
            ModelWorkerHost(
                host_id=instance.host_id,
                address=instance.address,
                ansible_user=str(user),
                ssh_private_key_path=self._spec.ssh_private_key_path,
                endpoint_url=f"http://{instance.address}:{port}/v1",
            )
            for instance in instances
        )
        return ProvisionedModelWorkerPool(
            deployment_id=self.operation_digest,
            hosts=hosts,
            owned_resource_ids=owned,
        )

    def _outputs(self, path: Path) -> ProvisionedModelWorkerPool:
        try:
            outputs = _mapping(_read_json(path, "outputs"))
            if self._spec.strategy is AzureExecutionStrategy.SINGLE_VM:
                return self._parse_single_vm(outputs)
            return self._parse_vmss(outputs)
        except AzureGpuWorkerRuntimeError:
            raise
        except Exception:
            raise AzureGpuWorkerRuntimeError("outputs") from None

    def _compensate(self, output_json: Path) -> bool:
        try:
            self._invoke("destroy", json_file=output_json, timeout_seconds=7_200)
        except AzureGpuWorkerRuntimeError:
            return False
        return True

    def provision(
        self,
        policy: ModelWorkerLifecyclePolicy,
    ) -> ProvisionedModelWorkerPool:
        """Apply one audited root and return independently resolved exact hosts."""
        self._bind(policy)
        if self._deployment is not None:
            return self._deployment
        terraform_dir, _plan_file, plan_json, output_json = self._paths()
        apply_started = False
        try:
            materialized = self._materializer.materialize(
                self._spec,
                terraform_dir,
                operation_digest=self.operation_digest,
            )
            if Path(materialized).resolve() != terraform_dir:
                raise AzureGpuWorkerRuntimeError("materialize")
            self._invoke("init", json_file=plan_json, timeout_seconds=300)
            self._invoke("validate", json_file=plan_json, timeout_seconds=120)
            self._invoke("plan", json_file=plan_json, timeout_seconds=900)
            plan_json.unlink(missing_ok=True)
            self._invoke("show-plan", json_file=plan_json, timeout_seconds=120)
            _audit_plan(_read_json(plan_json, "plan-audit"), self._spec.strategy)
            apply_started = True
            self._invoke("apply", json_file=output_json, timeout_seconds=7_200)
            output_json.unlink(missing_ok=True)
            self._invoke("output", json_file=output_json, timeout_seconds=120)
            deployment = self._outputs(output_json)
            self._deployment = deployment
            return deployment
        except AzureGpuWorkerRuntimeError as exc:
            cleanup_failed = apply_started and not self._compensate(output_json)
            raise AzureGpuWorkerRuntimeError(
                exc.phase,
                cleanup_failed=cleanup_failed,
            ) from None
        except Exception:
            cleanup_failed = apply_started and not self._compensate(output_json)
            raise AzureGpuWorkerRuntimeError(
                "provision",
                cleanup_failed=cleanup_failed,
            ) from None

    def _require_owned(self, deployment: ProvisionedModelWorkerPool) -> None:
        if (
            not isinstance(deployment, ProvisionedModelWorkerPool)
            or self._deployment is None
            or deployment != self._deployment
        ):
            raise AzureGpuWorkerRuntimeError("ownership")

    def destroy(self, deployment: ProvisionedModelWorkerPool) -> None:
        """Destroy only the exact deployment returned by this runtime."""
        self._require_owned(deployment)
        _terraform_dir, _plan_file, _plan_json, output_json = self._paths()
        self._invoke("destroy", json_file=output_json, timeout_seconds=7_200)

    def exists(self, deployment: ProvisionedModelWorkerPool) -> bool:
        """Independently enumerate whether any exact owned ARM ID remains."""
        self._require_owned(deployment)
        try:
            with self._credentials() as credentials:
                remaining = self._sdk.remaining_owned_resource_ids(
                    credentials=credentials,
                    spec=self._spec,
                    owned_resource_ids=deployment.owned_resource_ids,
                )
            return bool(remaining)
        except AzureGpuWorkerRuntimeError:
            raise
        except Exception:
            raise AzureGpuWorkerRuntimeError("absence") from None


__all__ = (
    "AzureGpuWorkerInfrastructureRuntime",
    "AzureGpuWorkerRuntimeError",
    "AzureGpuWorkerRuntimeTrace",
)
