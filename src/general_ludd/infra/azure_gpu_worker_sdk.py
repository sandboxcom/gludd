"""Independent Azure SDK readback for owned GPU VM and VMSS workers."""

from __future__ import annotations

import ipaddress
from collections.abc import Callable, Iterator, Mapping
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from typing import Any, Protocol

from general_ludd.azure.accelerator_credentials import AzureAcceleratorCredentials
from general_ludd.infra.azure_gpu_vm_strategy import AzureExecutionStrategy
from general_ludd.infra.azure_gpu_worker_materializer import (
    AzureGpuWorkerProvisioningSpec,
)

_NETWORK_API_VERSION = "2024-05-01"


class _Closable(Protocol):
    def close(self) -> object: ...


CredentialFactory = Callable[[AzureAcceleratorCredentials], object]
ClientFactory = Callable[[object, str], object]


class AzureGpuWorkerSdkError(RuntimeError):
    """Censored official-SDK readback failure."""

    def __init__(self, phase: str) -> None:
        """Retain one stable failure phase without provider response content."""
        self.phase = phase
        super().__init__(f"Azure GPU worker SDK readback failed: {phase}")


@dataclass(frozen=True, slots=True)
class AzureGpuWorkerInstance:
    """One exact VMSS instance and its private controller-routed address."""

    host_id: str
    address: str

    def __post_init__(self) -> None:
        """Require a bounded ARM identifier and one private IPv4 address."""
        if (
            not isinstance(self.host_id, str)
            or not self.host_id.startswith("/subscriptions/")
            or len(self.host_id) > 2_048
            or any(character in self.host_id for character in "\x00\r\n")
        ):
            raise ValueError("host_id must be one bounded ARM identifier")
        try:
            parsed = ipaddress.ip_address(self.address)
        except ValueError:
            raise ValueError("address must be one private IPv4 address") from None
        if parsed.version != 4 or not parsed.is_private:
            raise ValueError("address must be one private IPv4 address")


def _default_credential_factory(credentials: AzureAcceleratorCredentials) -> object:
    from azure.identity import ClientSecretCredential

    return ClientSecretCredential(
        tenant_id=credentials.tenant_id,
        client_id=credentials.client_id,
        client_secret=credentials.client_secret,
    )


def _default_compute_client_factory(credential: object, subscription_id: str) -> object:
    from azure.mgmt.compute import ComputeManagementClient

    return ComputeManagementClient(credential, subscription_id)


def _default_resource_client_factory(credential: object, subscription_id: str) -> object:
    from azure.mgmt.resource import ResourceManagementClient

    return ResourceManagementClient(credential, subscription_id)


def _close(value: object) -> None:
    close = getattr(value, "close", None)
    if callable(close):
        close()


def _mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError
    return value


class AzureGpuWorkerSdkReader:
    """Resolve VMSS addresses and independently enumerate exact owned resources."""

    def __init__(
        self,
        *,
        credential_factory: CredentialFactory = _default_credential_factory,
        compute_client_factory: ClientFactory = _default_compute_client_factory,
        resource_client_factory: ClientFactory = _default_resource_client_factory,
    ) -> None:
        """Bind maintained Azure SDK construction seams."""
        if not all(
            callable(factory)
            for factory in (
                credential_factory,
                compute_client_factory,
                resource_client_factory,
            )
        ):
            raise ValueError("Azure SDK factories must be callable")
        self._credential_factory = credential_factory
        self._compute_client_factory = compute_client_factory
        self._resource_client_factory = resource_client_factory

    @contextmanager
    def _clients(
        self,
        credentials: AzureAcceleratorCredentials,
    ) -> Iterator[tuple[object, object]]:
        with ExitStack() as stack:
            credential = self._credential_factory(credentials)
            stack.callback(_close, credential)
            compute = self._compute_client_factory(
                credential,
                credentials.subscription_id,
            )
            stack.callback(_close, compute)
            resource = self._resource_client_factory(
                credential,
                credentials.subscription_id,
            )
            stack.callback(_close, resource)
            yield compute, resource

    @staticmethod
    def _validate_contract(
        credentials: AzureAcceleratorCredentials,
        spec: AzureGpuWorkerProvisioningSpec,
    ) -> None:
        if not isinstance(credentials, AzureAcceleratorCredentials):
            raise ValueError
        if not isinstance(spec, AzureGpuWorkerProvisioningSpec):
            raise ValueError
        if credentials.subscription_id != spec.subscription_id:
            raise ValueError

    @staticmethod
    def _network_interface_id(vm: object, expected_vm_id: str) -> str:
        vm_id = getattr(vm, "id", None)
        name = getattr(vm, "name", None)
        if (
            not isinstance(vm_id, str)
            or not isinstance(name, str)
            or vm_id.lower() != expected_vm_id.lower()
        ):
            raise ValueError
        profile = getattr(vm, "network_profile", None)
        interfaces = getattr(profile, "network_interfaces", None)
        if not isinstance(interfaces, list) or len(interfaces) != 1:
            raise ValueError
        interface = interfaces[0]
        interface_id = getattr(interface, "id", None)
        if (
            not isinstance(interface_id, str)
            or getattr(interface, "primary", None) is not True
            or not interface_id.lower().startswith(f"{vm_id.lower()}/networkinterfaces/")
        ):
            raise ValueError
        return interface_id

    @staticmethod
    def _private_address(network_interface: object) -> str:
        properties = _mapping(getattr(network_interface, "properties", None))
        configurations = properties.get("ipConfigurations")
        if not isinstance(configurations, list):
            raise ValueError
        addresses: list[str] = []
        for configuration in configurations:
            values = _mapping(configuration)
            details = _mapping(values.get("properties"))
            address = details.get("privateIPAddress")
            if details.get("primary") is True and isinstance(address, str):
                parsed = ipaddress.ip_address(address)
                if parsed.version == 4 and parsed.is_private:
                    addresses.append(address)
        if len(addresses) != 1:
            raise ValueError
        return addresses[0]

    def resolve_vmss_instances(
        self,
        *,
        credentials: AzureAcceleratorCredentials,
        spec: AzureGpuWorkerProvisioningSpec,
        scale_set_id: str,
    ) -> tuple[AzureGpuWorkerInstance, ...]:
        """Resolve the exact private inventory of one owned Uniform VMSS."""
        try:
            self._validate_contract(credentials, spec)
            if spec.strategy is not AzureExecutionStrategy.VMSS:
                raise ValueError
            expected_scale_set_id = (
                f"{spec.resource_group_id}/providers/Microsoft.Compute/"
                f"virtualMachineScaleSets/{spec.deployment_name}-vmss"
            )
            if (
                not isinstance(scale_set_id, str)
                or scale_set_id.lower() != expected_scale_set_id.lower()
            ):
                raise ValueError
            with self._clients(credentials) as (compute, resource):
                operations: Any = getattr(
                    compute,
                    "virtual_machine_scale_set_vms",
                    None,
                )
                values = tuple(
                    operations.list(
                        spec.resource_group_name,
                        f"{spec.deployment_name}-vmss",
                    )
                )
                if len(values) != spec.instance_count:
                    raise ValueError
                resource_operations: Any = getattr(resource, "resources", None)
                instances: list[AzureGpuWorkerInstance] = []
                for vm in values:
                    name = getattr(vm, "name", None)
                    if not isinstance(name, str) or not name:
                        raise ValueError
                    vm_id = f"{scale_set_id}/virtualMachines/{name}"
                    interface_id = self._network_interface_id(vm, vm_id)
                    interface = resource_operations.get_by_id(
                        interface_id,
                        _NETWORK_API_VERSION,
                    )
                    instances.append(
                        AzureGpuWorkerInstance(
                            host_id=vm_id,
                            address=self._private_address(interface),
                        )
                    )
                if len({item.host_id.lower() for item in instances}) != len(instances):
                    raise ValueError
                if len({item.address for item in instances}) != len(instances):
                    raise ValueError
            return tuple(sorted(instances, key=lambda item: item.host_id.lower()))
        except AzureGpuWorkerSdkError:
            raise
        except Exception:
            raise AzureGpuWorkerSdkError("vmss-inventory") from None

    def remaining_owned_resource_ids(
        self,
        *,
        credentials: AzureAcceleratorCredentials,
        spec: AzureGpuWorkerProvisioningSpec,
        owned_resource_ids: tuple[str, ...],
    ) -> tuple[str, ...]:
        """Enumerate the delegated group and return exact owned IDs still present."""
        try:
            self._validate_contract(credentials, spec)
            if (
                not isinstance(owned_resource_ids, tuple)
                or not owned_resource_ids
                or any(
                    not isinstance(resource_id, str)
                    or not resource_id.lower().startswith(
                        f"{spec.resource_group_id.lower()}/providers/"
                    )
                    for resource_id in owned_resource_ids
                )
                or len({item.lower() for item in owned_resource_ids})
                != len(owned_resource_ids)
            ):
                raise ValueError
            with self._clients(credentials) as (_compute, resource):
                operations: Any = getattr(resource, "resources", None)
                observed = {
                    resource_id.lower()
                    for item in operations.list_by_resource_group(
                        spec.resource_group_name
                    )
                    if isinstance((resource_id := getattr(item, "id", None)), str)
                }
            return tuple(
                resource_id
                for resource_id in owned_resource_ids
                if resource_id.lower() in observed
            )
        except AzureGpuWorkerSdkError:
            raise
        except Exception:
            raise AzureGpuWorkerSdkError("resource-inventory") from None


__all__ = (
    "AzureGpuWorkerInstance",
    "AzureGpuWorkerSdkError",
    "AzureGpuWorkerSdkReader",
)
