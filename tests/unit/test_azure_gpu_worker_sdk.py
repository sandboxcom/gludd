"""Tests for independent official-SDK Azure GPU worker readback."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from types import SimpleNamespace
from typing import Any

import pytest

from general_ludd.azure.accelerator_credentials import AzureAcceleratorCredentials
from general_ludd.infra.azure_gpu_vm_strategy import AzureExecutionStrategy
from general_ludd.infra.azure_gpu_worker_materializer import (
    AzureGpuWorkerProvisioningSpec,
)
from general_ludd.infra.azure_gpu_worker_sdk import (
    AzureGpuWorkerInstance,
    AzureGpuWorkerSdkError,
    AzureGpuWorkerSdkReader,
)

_SUBSCRIPTION = "00000000-0000-4000-8000-000000000001"
_RESOURCE_GROUP = "gludd-accelerators"
_RESOURCE_GROUP_ID = f"/subscriptions/{_SUBSCRIPTION}/resourceGroups/{_RESOURCE_GROUP}"
_SCALE_SET_ID = (
    f"{_RESOURCE_GROUP_ID}/providers/Microsoft.Compute/"
    "virtualMachineScaleSets/gludd-worker-001-vmss"
)


def _credentials() -> AzureAcceleratorCredentials:
    return AzureAcceleratorCredentials(
        client_id="00000000-0000-4000-8000-000000000002",
        client_secret="private-value",  # pragma: allowlist secret
        subscription_id=_SUBSCRIPTION,
        tenant_id="00000000-0000-4000-8000-000000000003",
    )


def _spec() -> AzureGpuWorkerProvisioningSpec:
    return AzureGpuWorkerProvisioningSpec(
        strategy=AzureExecutionStrategy.VMSS,
        deployment_name="gludd-worker-001",
        subscription_id=_SUBSCRIPTION,
        resource_group_id=_RESOURCE_GROUP_ID,
        resource_group_name=_RESOURCE_GROUP,
        location="eastus",
        vm_size="Standard_NC24ads_A100_v4",
        instance_count=2,
        accelerated_networking_enabled=True,
        admin_username="gludd",
        ssh_public_key="ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITest gludd@test",
        ssh_private_key_path="/run/gludd/keys/azure-worker",
        controller_cidr="203.0.113.4/32",
        inference_port=8000,
        image_publisher="Canonical",
        image_offer="0001-com-ubuntu-server-jammy",
        image_sku="22_04-lts-gen2",
        image_version="22.04.202409020",
        owner_token="gludd-owner-001",
        trace_id="a" * 32,
        expires_at_utc="2026-09-20T12:00:00Z",
        availability_zones=("1",),
        user_assigned_identity_id=(
            f"{_RESOURCE_GROUP_ID}/providers/Microsoft.ManagedIdentity/"
            "userAssignedIdentities/gludd-worker"
        ),
        rdma_enabled=True,
    )


def _single_spec() -> AzureGpuWorkerProvisioningSpec:
    return replace(
        _spec(),
        strategy=AzureExecutionStrategy.SINGLE_VM,
        instance_count=1,
        availability_zone="1",
        availability_zones=(),
        user_assigned_identity_id=None,
        rdma_enabled=False,
    )


@dataclass
class _Closable:
    closed: bool = False

    def close(self) -> None:
        self.closed = True


@dataclass
class _VmOperations:
    values: list[object]
    calls: list[tuple[str, str]] = field(default_factory=list)

    def list(self, resource_group: str, scale_set: str) -> list[object]:
        self.calls.append((resource_group, scale_set))
        return self.values


@dataclass
class _ComputeClient(_Closable):
    virtual_machine_scale_set_vms: _VmOperations = field(
        default_factory=lambda: _VmOperations([])
    )


@dataclass
class _ResourceOperations:
    nics: dict[str, object]
    resources: list[object]
    get_calls: list[tuple[str, str]] = field(default_factory=list)
    list_calls: list[str] = field(default_factory=list)

    def get_by_id(self, resource_id: str, api_version: str) -> object:
        self.get_calls.append((resource_id, api_version))
        return self.nics[resource_id]

    def list_by_resource_group(self, resource_group: str) -> list[object]:
        self.list_calls.append(resource_group)
        return self.resources


@dataclass
class _ResourceClient(_Closable):
    resources: _ResourceOperations = field(
        default_factory=lambda: _ResourceOperations({}, [])
    )


def _vm(instance: str) -> object:
    vm_id = f"{_SCALE_SET_ID}/virtualMachines/{instance}"
    nic_id = f"{vm_id}/networkInterfaces/primary"
    return SimpleNamespace(
        id=vm_id,
        name=instance,
        network_profile=SimpleNamespace(
            network_interfaces=[SimpleNamespace(id=nic_id, primary=True)]
        ),
    )


def _nic(address: str) -> object:
    return SimpleNamespace(
        properties={
            "ipConfigurations": [
                {
                    "name": "primary",
                    "properties": {
                        "primary": True,
                        "privateIPAddress": address,
                    },
                }
            ]
        }
    )


def _reader(
    compute: _ComputeClient,
    resource: _ResourceClient,
    credential: _Closable,
) -> AzureGpuWorkerSdkReader:
    return AzureGpuWorkerSdkReader(
        credential_factory=lambda _credentials: credential,
        compute_client_factory=lambda _credential, _subscription: compute,
        resource_client_factory=lambda _credential, _subscription: resource,
    )


def test_resolves_exact_vmss_private_inventory_and_closes_sdk_clients() -> None:
    first = _vm("000001")
    second = _vm("000000")
    compute = _ComputeClient(
        virtual_machine_scale_set_vms=_VmOperations([first, second])
    )
    first_nic = first.network_profile.network_interfaces[0].id
    second_nic = second.network_profile.network_interfaces[0].id
    resource = _ResourceClient(
        resources=_ResourceOperations(
            {first_nic: _nic("10.43.1.5"), second_nic: _nic("10.43.1.4")},
            [],
        )
    )
    credential = _Closable()

    instances = _reader(compute, resource, credential).resolve_vmss_instances(
        credentials=_credentials(),
        spec=_spec(),
        scale_set_id=_SCALE_SET_ID,
    )

    assert [(item.host_id, item.address) for item in instances] == [
        (f"{_SCALE_SET_ID}/virtualMachines/000000", "10.43.1.4"),
        (f"{_SCALE_SET_ID}/virtualMachines/000001", "10.43.1.5"),
    ]
    assert compute.virtual_machine_scale_set_vms.calls == [
        (_RESOURCE_GROUP, "gludd-worker-001-vmss")
    ]
    assert all(call[1] == "2024-05-01" for call in resource.resources.get_calls)
    assert compute.closed is True
    assert resource.closed is True
    assert credential.closed is True


def test_reads_remaining_owned_ids_case_insensitively() -> None:
    owned = (
        _SCALE_SET_ID,
        f"{_RESOURCE_GROUP_ID}/providers/Microsoft.Network/virtualNetworks/worker",
    )
    compute = _ComputeClient()
    resource = _ResourceClient(
        resources=_ResourceOperations(
            {},
            [SimpleNamespace(id=_SCALE_SET_ID.upper()), SimpleNamespace(id="/foreign")],
        )
    )
    credential = _Closable()

    remaining = _reader(compute, resource, credential).remaining_owned_resource_ids(
        credentials=_credentials(),
        spec=_spec(),
        owned_resource_ids=owned,
    )

    assert remaining == (_SCALE_SET_ID,)
    assert resource.resources.list_calls == [_RESOURCE_GROUP]
    assert compute.closed is True
    assert resource.closed is True
    assert credential.closed is True


@pytest.mark.parametrize(
    "mutator",
    [
        lambda vm: setattr(vm, "id", "/foreign"),
        lambda vm: setattr(vm, "network_profile", None),
        lambda vm: setattr(vm.network_profile.network_interfaces[0], "id", None),
    ],
)
def test_rejects_malformed_vmss_inventory_and_censors_details(
    mutator: Any,
) -> None:
    vm = _vm("000000")
    mutator(vm)
    compute = _ComputeClient(virtual_machine_scale_set_vms=_VmOperations([vm]))
    resource = _ResourceClient()
    credential = _Closable()

    with pytest.raises(AzureGpuWorkerSdkError) as caught:
        _reader(compute, resource, credential).resolve_vmss_instances(
            credentials=_credentials(),
            spec=_spec(),
            scale_set_id=_SCALE_SET_ID,
        )

    assert caught.value.phase == "vmss-inventory"
    assert "private-value" not in str(caught.value)
    assert compute.closed is True
    assert resource.closed is True
    assert credential.closed is True


def test_rejects_wrong_instance_count() -> None:
    vm = _vm("000000")
    compute = _ComputeClient(virtual_machine_scale_set_vms=_VmOperations([vm]))
    resource = _ResourceClient()

    with pytest.raises(AzureGpuWorkerSdkError) as caught:
        _reader(compute, resource, _Closable()).resolve_vmss_instances(
            credentials=_credentials(),
            spec=_spec(),
            scale_set_id=_SCALE_SET_ID,
        )

    assert caught.value.phase == "vmss-inventory"


def test_rejects_malformed_private_address() -> None:
    first = _vm("000000")
    second = _vm("000001")
    first_nic = first.network_profile.network_interfaces[0].id
    second_nic = second.network_profile.network_interfaces[0].id
    compute = _ComputeClient(
        virtual_machine_scale_set_vms=_VmOperations([first, second])
    )
    resource = _ResourceClient(
        resources=_ResourceOperations(
            {first_nic: _nic("not-an-address"), second_nic: _nic("10.43.1.5")},
            [],
        )
    )

    with pytest.raises(AzureGpuWorkerSdkError) as caught:
        _reader(compute, resource, _Closable()).resolve_vmss_instances(
            credentials=_credentials(),
            spec=_spec(),
            scale_set_id=_SCALE_SET_ID,
        )

    assert caught.value.phase == "vmss-inventory"


@pytest.mark.parametrize(
    ("host_id", "address"),
    [
        ("foreign", "10.43.1.4"),
        (f"{_SCALE_SET_ID}\n/virtualMachines/0", "10.43.1.4"),
        (f"{_SCALE_SET_ID}/virtualMachines/0", "not-an-address"),
        (f"{_SCALE_SET_ID}/virtualMachines/0", "2001:db8::1"),
        (f"{_SCALE_SET_ID}/virtualMachines/0", "8.8.8.8"),
    ],
)
def test_instance_contract_rejects_non_private_or_unbounded_values(
    host_id: str,
    address: str,
) -> None:
    with pytest.raises(ValueError):
        AzureGpuWorkerInstance(host_id=host_id, address=address)


def test_reader_requires_callable_factories() -> None:
    with pytest.raises(ValueError, match="factories"):
        AzureGpuWorkerSdkReader(credential_factory=None)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("credentials", "spec", "scale_set_id"),
    [
        (object(), _spec(), _SCALE_SET_ID),
        (_credentials(), object(), _SCALE_SET_ID),
        (
            replace(_credentials(), subscription_id="00000000-0000-4000-8000-000000000004"),
            _spec(),
            _SCALE_SET_ID,
        ),
        (_credentials(), _single_spec(), _SCALE_SET_ID),
        (_credentials(), _spec(), "/foreign"),
    ],
)
def test_resolver_rejects_cross_scope_contracts(
    credentials: object,
    spec: object,
    scale_set_id: str,
) -> None:
    with pytest.raises(AzureGpuWorkerSdkError) as caught:
        AzureGpuWorkerSdkReader().resolve_vmss_instances(
            credentials=credentials,  # type: ignore[arg-type]
            spec=spec,  # type: ignore[arg-type]
            scale_set_id=scale_set_id,
        )

    assert caught.value.phase == "vmss-inventory"


@pytest.mark.parametrize(
    "change",
    [
        "empty-name",
        "interfaces-not-list",
        "primary-false",
        "foreign-nic",
    ],
)
def test_resolver_rejects_ambiguous_network_interface(change: str) -> None:
    first = _vm("000000")
    second = _vm("000001")
    if change == "empty-name":
        first.name = ""
    elif change == "interfaces-not-list":
        first.network_profile.network_interfaces = ()
    elif change == "primary-false":
        first.network_profile.network_interfaces[0].primary = False
    else:
        first.network_profile.network_interfaces[0].id = "/foreign"
    compute = _ComputeClient(
        virtual_machine_scale_set_vms=_VmOperations([first, second])
    )
    resource = _ResourceClient()

    with pytest.raises(AzureGpuWorkerSdkError) as caught:
        _reader(compute, resource, _Closable()).resolve_vmss_instances(
            credentials=_credentials(),
            spec=_spec(),
            scale_set_id=_SCALE_SET_ID,
        )

    assert caught.value.phase == "vmss-inventory"


@pytest.mark.parametrize(
    "network_interface",
    [
        SimpleNamespace(properties=None),
        SimpleNamespace(properties={"ipConfigurations": None}),
        SimpleNamespace(properties={"ipConfigurations": [None]}),
        SimpleNamespace(properties={"ipConfigurations": [{"properties": None}]}),
        SimpleNamespace(
            properties={
                "ipConfigurations": [
                    {"properties": {"primary": False, "privateIPAddress": "10.0.0.4"}}
                ]
            }
        ),
        SimpleNamespace(
            properties={
                "ipConfigurations": [
                    {"properties": {"primary": True, "privateIPAddress": "8.8.8.8"}}
                ]
            }
        ),
    ],
)
def test_resolver_rejects_ambiguous_private_ip_configuration(
    network_interface: object,
) -> None:
    first = _vm("000000")
    second = _vm("000001")
    first_nic = first.network_profile.network_interfaces[0].id
    second_nic = second.network_profile.network_interfaces[0].id
    compute = _ComputeClient(
        virtual_machine_scale_set_vms=_VmOperations([first, second])
    )
    resource = _ResourceClient(
        resources=_ResourceOperations(
            {first_nic: network_interface, second_nic: _nic("10.43.1.5")},
            [],
        )
    )

    with pytest.raises(AzureGpuWorkerSdkError) as caught:
        _reader(compute, resource, _Closable()).resolve_vmss_instances(
            credentials=_credentials(),
            spec=_spec(),
            scale_set_id=_SCALE_SET_ID,
        )

    assert caught.value.phase == "vmss-inventory"


@pytest.mark.parametrize("duplicate", ["host", "address"])
def test_resolver_rejects_duplicate_inventory(duplicate: str) -> None:
    first = _vm("000000")
    second = _vm("000000" if duplicate == "host" else "000001")
    first_nic = first.network_profile.network_interfaces[0].id
    second_nic = second.network_profile.network_interfaces[0].id
    address = "10.43.1.4"
    resource = _ResourceClient(
        resources=_ResourceOperations(
            {
                first_nic: _nic(address),
                second_nic: _nic(address if duplicate == "address" else "10.43.1.5"),
            },
            [],
        )
    )
    compute = _ComputeClient(
        virtual_machine_scale_set_vms=_VmOperations([first, second])
    )

    with pytest.raises(AzureGpuWorkerSdkError) as caught:
        _reader(compute, resource, _Closable()).resolve_vmss_instances(
            credentials=_credentials(),
            spec=_spec(),
            scale_set_id=_SCALE_SET_ID,
        )

    assert caught.value.phase == "vmss-inventory"


@pytest.mark.parametrize(
    "owned",
    [
        [],
        (),
        ("/foreign",),
        (_SCALE_SET_ID, _SCALE_SET_ID.upper()),
    ],
)
def test_remaining_inventory_rejects_unowned_contract(owned: object) -> None:
    with pytest.raises(AzureGpuWorkerSdkError) as caught:
        AzureGpuWorkerSdkReader().remaining_owned_resource_ids(
            credentials=_credentials(),
            spec=_spec(),
            owned_resource_ids=owned,  # type: ignore[arg-type]
        )

    assert caught.value.phase == "resource-inventory"


def test_sdk_error_from_factory_is_preserved() -> None:
    expected = AzureGpuWorkerSdkError("factory")

    def fail(_credentials: AzureAcceleratorCredentials) -> object:
        raise expected

    reader = AzureGpuWorkerSdkReader(
        credential_factory=fail,
        compute_client_factory=lambda _credential, _subscription: object(),
        resource_client_factory=lambda _credential, _subscription: object(),
    )

    with pytest.raises(AzureGpuWorkerSdkError) as resolve:
        reader.resolve_vmss_instances(
            credentials=_credentials(),
            spec=_spec(),
            scale_set_id=_SCALE_SET_ID,
        )
    with pytest.raises(AzureGpuWorkerSdkError) as inventory:
        reader.remaining_owned_resource_ids(
            credentials=_credentials(),
            spec=_spec(),
            owned_resource_ids=(_SCALE_SET_ID,),
        )

    assert resolve.value is expected
    assert inventory.value is expected


def test_nonclosable_sdk_boundary_is_supported() -> None:
    resource = _ResourceClient(resources=_ResourceOperations({}, []))
    reader = AzureGpuWorkerSdkReader(
        credential_factory=lambda _credentials: object(),
        compute_client_factory=lambda _credential, _subscription: object(),
        resource_client_factory=lambda _credential, _subscription: resource,
    )

    remaining = reader.remaining_owned_resource_ids(
        credentials=_credentials(),
        spec=_spec(),
        owned_resource_ids=(_SCALE_SET_ID,),
    )

    assert remaining == ()
    assert resource.closed is True
