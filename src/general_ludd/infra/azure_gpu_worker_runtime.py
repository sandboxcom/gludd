"""Owned OpenTofu infrastructure adapter for Azure GPU VM model workers."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import stat
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

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
from general_ludd.infra.azure_gpu_worker_sdk import (
    AzureGpuWorkerInstance,
    AzureGpuWorkerSdkReader,
)
from general_ludd.infra.model_worker_lifecycle import (
    ModelWorkerHost,
    ModelWorkerLifecyclePolicy,
    ProvisionedModelWorkerPool,
)

_MAX_JSON_BYTES = 16 * 1024 * 1024
_PHASE = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
_HEX_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_SINGLE_VM_PLAN = {
    "azurerm_virtual_network.worker": "azurerm_virtual_network",
    "azurerm_subnet.worker": "azurerm_subnet",
    "azurerm_public_ip.worker": "azurerm_public_ip",
    "azurerm_network_security_group.worker": "azurerm_network_security_group",
    "azurerm_network_interface.worker": "azurerm_network_interface",
    "azurerm_network_interface_security_group_association.worker": (
        "azurerm_network_interface_security_group_association"
    ),
    "azurerm_linux_virtual_machine.worker": "azurerm_linux_virtual_machine",
}
_VMSS_PLAN = {
    "azurerm_virtual_network.worker": "azurerm_virtual_network",
    "azurerm_subnet.worker": "azurerm_subnet",
    "azurerm_network_security_group.worker": "azurerm_network_security_group",
    "azurerm_subnet_network_security_group_association.worker": (
        "azurerm_subnet_network_security_group_association"
    ),
    "azurerm_public_ip.egress": "azurerm_public_ip",
    "azurerm_nat_gateway.worker": "azurerm_nat_gateway",
    "azurerm_nat_gateway_public_ip_association.worker": (
        "azurerm_nat_gateway_public_ip_association"
    ),
    "azurerm_subnet_nat_gateway_association.worker": (
        "azurerm_subnet_nat_gateway_association"
    ),
    "azurerm_linux_virtual_machine_scale_set.worker": (
        "azurerm_linux_virtual_machine_scale_set"
    ),
}


class _CredentialSource(Protocol):
    def acquire(self) -> AzureAcceleratorCredentialLease: ...

    def release(self, lease: AzureAcceleratorCredentialLease) -> None: ...


class _TerraformExecutor(Protocol):
    def run(
        self,
        *,
        phase: str,
        terraform_dir: str | os.PathLike[str],
        plan_file: str | os.PathLike[str],
        json_file: str | os.PathLike[str],
        allowed_root: str | os.PathLike[str],
        environment: Mapping[str, str],
        timeout_seconds: int,
        progress: Callable[[str, TerraformRuntimeState, int], None],
    ) -> None: ...


class _Materializer(Protocol):
    def materialize(
        self,
        spec: AzureGpuWorkerProvisioningSpec,
        destination: str | os.PathLike[str],
        *,
        operation_digest: str,
    ) -> Path: ...


class _SdkReader(Protocol):
    def resolve_vmss_instances(
        self,
        *,
        credentials: AzureAcceleratorCredentials,
        spec: AzureGpuWorkerProvisioningSpec,
        scale_set_id: str,
    ) -> tuple[AzureGpuWorkerInstance, ...]: ...

    def remaining_owned_resource_ids(
        self,
        *,
        credentials: AzureAcceleratorCredentials,
        spec: AzureGpuWorkerProvisioningSpec,
        owned_resource_ids: tuple[str, ...],
    ) -> tuple[str, ...]: ...


class AzureGpuWorkerRuntimeError(RuntimeError):
    """Censored infrastructure failure with compensation disposition."""

    def __init__(self, phase: str, *, cleanup_failed: bool = False) -> None:
        """Retain only one stable phase and whether owned cleanup failed."""
        self.phase = phase
        self.cleanup_failed = bool(cleanup_failed)
        suffix = "; cleanup failed" if self.cleanup_failed else ""
        super().__init__(f"Azure GPU worker runtime failed: {phase}{suffix}")


@dataclass(frozen=True, slots=True)
class AzureGpuWorkerRuntimeTrace:
    """Content-free OpenTofu progress for one immutable worker operation."""

    phase: str
    state: TerraformRuntimeState
    operation_digest: str
    elapsed_seconds: int

    def __post_init__(self) -> None:
        """Validate the bounded observable envelope."""
        if not isinstance(self.phase, str) or _PHASE.fullmatch(self.phase) is None:
            raise ValueError("phase is invalid")
        if not isinstance(self.state, TerraformRuntimeState):
            raise ValueError("state must be TerraformRuntimeState")
        if not isinstance(self.operation_digest, str) or _HEX_DIGEST.fullmatch(
            self.operation_digest
        ) is None:
            raise ValueError("operation_digest is invalid")
        if (
            isinstance(self.elapsed_seconds, bool)
            or not isinstance(self.elapsed_seconds, int)
            or self.elapsed_seconds < 0
        ):
            raise ValueError("elapsed_seconds must be non-negative")


def _discard_trace(_trace: AzureGpuWorkerRuntimeTrace) -> None:
    return None


def _read_json(path: Path, phase: str) -> object:
    try:
        metadata = path.lstat()
        if (
            not stat.S_ISREG(metadata.st_mode)
            or path.is_symlink()
            or not 0 < metadata.st_size <= _MAX_JSON_BYTES
        ):
            raise ValueError
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
        raise AzureGpuWorkerRuntimeError(phase) from None


def _mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError
    return value


def _output_value(outputs: Mapping[str, object], name: str) -> object:
    value = _mapping(outputs.get(name))
    if value.get("sensitive") is not False or "value" not in value:
        raise ValueError
    return value["value"]


def _owned_ids(value: object, resource_group_id: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise ValueError
    prefix = f"{resource_group_id.lower()}/providers/"
    if any(
        not isinstance(item, str)
        or not item.lower().startswith(prefix)
        or len(item) > 2_048
        for item in value
    ):
        raise ValueError
    result = tuple(value)
    if len({item.lower() for item in result}) != len(result):
        raise ValueError
    return result


def _operation_digest(
    spec: AzureGpuWorkerProvisioningSpec,
    policy: ModelWorkerLifecyclePolicy,
) -> str:
    payload = json.dumps(
        {
            "policy": policy.operation_digest,
            "private_key_path": spec.ssh_private_key_path,
            "terraform": spec.terraform_variables(),
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    return hashlib.sha256(payload).hexdigest()


def _audit_plan(document: object, strategy: AzureExecutionStrategy) -> None:
    try:
        root = _mapping(document)
        changes = root.get("resource_changes")
        if not isinstance(changes, list):
            raise ValueError
        expected = (
            _SINGLE_VM_PLAN
            if strategy is AzureExecutionStrategy.SINGLE_VM
            else _VMSS_PLAN
        )
        observed: dict[str, str] = {}
        for raw in changes:
            change = _mapping(raw)
            address = change.get("address")
            resource_type = change.get("type")
            actions = _mapping(change.get("change")).get("actions")
            if (
                not isinstance(address, str)
                or not isinstance(resource_type, str)
                or actions not in (["create"], ["no-op"])
                or address in observed
            ):
                raise ValueError
            observed[address] = resource_type
        if observed != expected:
            raise ValueError
    except (TypeError, ValueError):
        raise AzureGpuWorkerRuntimeError("plan-audit") from None


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
        vm_id = _output_value(outputs, "virtual_machine_id")
        address = _output_value(outputs, "ansible_host")
        user = _output_value(outputs, "ansible_user")
        endpoint = _output_value(outputs, "inference_endpoint")
        owned = _owned_ids(
            _output_value(outputs, "owned_resource_ids"),
            self._spec.resource_group_id,
        )
        if (
            not isinstance(vm_id, str)
            or vm_id not in owned
            or not isinstance(address, str)
            or not isinstance(user, str)
            or user != self._spec.admin_username
            or endpoint != f"http://{address}:{self._spec.inference_port}"
        ):
            raise ValueError
        parsed = ipaddress.ip_address(address)
        if parsed.version != 4 or parsed.is_unspecified or parsed.is_multicast:
            raise ValueError
        return ProvisionedModelWorkerPool(
            deployment_id=self.operation_digest,
            hosts=(
                ModelWorkerHost(
                    host_id=vm_id,
                    address=address,
                    ansible_user=user,
                    ssh_private_key_path=self._spec.ssh_private_key_path,
                    endpoint_url=f"{endpoint}/v1",
                ),
            ),
            owned_resource_ids=owned,
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
