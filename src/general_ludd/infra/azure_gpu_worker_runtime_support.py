"""Contracts, parsing, and plan audit for the Azure GPU worker runtime."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import stat
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from general_ludd.azure.accelerator_credential_source import (
    AzureAcceleratorCredentialLease,
)
from general_ludd.azure.accelerator_credentials import AzureAcceleratorCredentials
from general_ludd.infra.azure_containerapp_terraform_executor import TerraformRuntimeState
from general_ludd.infra.azure_gpu_vm_strategy import AzureExecutionStrategy
from general_ludd.infra.azure_gpu_worker_materializer import AzureGpuWorkerProvisioningSpec
from general_ludd.infra.azure_gpu_worker_sdk import AzureGpuWorkerInstance
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
    "azurerm_subnet_network_security_group_association.worker": ("azurerm_subnet_network_security_group_association"),
    "azurerm_public_ip.egress": "azurerm_public_ip",
    "azurerm_nat_gateway.worker": "azurerm_nat_gateway",
    "azurerm_nat_gateway_public_ip_association.worker": ("azurerm_nat_gateway_public_ip_association"),
    "azurerm_subnet_nat_gateway_association.worker": ("azurerm_subnet_nat_gateway_association"),
    "azurerm_linux_virtual_machine_scale_set.worker": ("azurerm_linux_virtual_machine_scale_set"),
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

    def resolve_single_vm(
        self,
        *,
        credentials: AzureAcceleratorCredentials,
        spec: AzureGpuWorkerProvisioningSpec,
        vm_id: str,
    ) -> AzureGpuWorkerInstance: ...

    def remaining_owned_resource_ids(
        self,
        *,
        credentials: AzureAcceleratorCredentials,
        spec: AzureGpuWorkerProvisioningSpec,
        owned_resource_ids: tuple[str, ...],
    ) -> tuple[str, ...]: ...

    def delete_owned_resources(
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
        if not isinstance(self.operation_digest, str) or _HEX_DIGEST.fullmatch(self.operation_digest) is None:
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
        if not stat.S_ISREG(metadata.st_mode) or path.is_symlink() or not 0 < metadata.st_size <= _MAX_JSON_BYTES:
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
    if any(not isinstance(item, str) or not item.lower().startswith(prefix) or len(item) > 2_048 for item in value):
        raise ValueError
    result = tuple(value)
    if len({item.lower() for item in result}) != len(result):
        raise ValueError
    return result


def _parse_single_vm_outputs(
    outputs: Mapping[str, object],
    *,
    spec: AzureGpuWorkerProvisioningSpec,
    operation_digest: str,
) -> ProvisionedModelWorkerPool:
    vm_id = _output_value(outputs, "virtual_machine_id")
    address = _output_value(outputs, "ansible_host")
    user = _output_value(outputs, "ansible_user")
    endpoint = _output_value(outputs, "inference_endpoint")
    owned = _owned_ids(
        _output_value(outputs, "owned_resource_ids"),
        spec.resource_group_id,
    )
    if (
        not isinstance(vm_id, str)
        or vm_id not in owned
        or not isinstance(address, str)
        or not isinstance(user, str)
        or user != spec.admin_username
        or endpoint != f"http://{address}:{spec.inference_port}"
    ):
        raise ValueError
    parsed = ipaddress.ip_address(address)
    if parsed.version != 4 or parsed.is_unspecified or parsed.is_multicast:
        raise ValueError
    return ProvisionedModelWorkerPool(
        deployment_id=operation_digest,
        hosts=(
            ModelWorkerHost(
                host_id=vm_id,
                address=address,
                ansible_user=user,
                ssh_private_key_path=spec.ssh_private_key_path,
                endpoint_url=f"{endpoint}/v1",
            ),
        ),
        owned_resource_ids=owned,
    )


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
        expected = _SINGLE_VM_PLAN if strategy is AzureExecutionStrategy.SINGLE_VM else _VMSS_PLAN
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


__all__ = (
    "AzureGpuWorkerRuntimeError",
    "AzureGpuWorkerRuntimeTrace",
)
