"""Tests for the owned Azure GPU worker infrastructure runtime."""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import pytest

from general_ludd.azure.accelerator_credential_source import (
    AzureAcceleratorCredentialLease,
)
from general_ludd.azure.accelerator_credentials import AzureAcceleratorCredentials
from general_ludd.hardware.accelerator_topology import DistributionMode
from general_ludd.hardware.model_runner_launch_contracts import RunnerLaunchPlan
from general_ludd.infra.azure_containerapp_terraform_executor import (
    TerraformRuntimeState,
)
from general_ludd.infra.azure_gpu_vm_strategy import AzureExecutionStrategy
from general_ludd.infra.azure_gpu_worker_materializer import (
    AzureGpuWorkerProvisioningSpec,
)
from general_ludd.infra.azure_gpu_worker_runtime import (
    AzureGpuWorkerInfrastructureRuntime,
    AzureGpuWorkerRuntimeError,
    AzureGpuWorkerRuntimeTrace,
)
from general_ludd.infra.azure_gpu_worker_sdk import AzureGpuWorkerInstance
from general_ludd.infra.model_worker_lifecycle import ModelWorkerLifecyclePolicy

_SUBSCRIPTION = "00000000-0000-4000-8000-000000000001"
_RESOURCE_GROUP = "gludd-accelerators"
_RESOURCE_GROUP_ID = f"/subscriptions/{_SUBSCRIPTION}/resourceGroups/{_RESOURCE_GROUP}"
_VM_ID = (
    f"{_RESOURCE_GROUP_ID}/providers/Microsoft.Compute/"
    "virtualMachines/gludd-worker-001-vm"
)
_VMSS_ID = (
    f"{_RESOURCE_GROUP_ID}/providers/Microsoft.Compute/"
    "virtualMachineScaleSets/gludd-worker-001-vmss"
)


def _credentials() -> AzureAcceleratorCredentials:
    return AzureAcceleratorCredentials(
        client_id="00000000-0000-4000-8000-000000000002",
        client_secret="private-secret",  # pragma: allowlist secret
        subscription_id=_SUBSCRIPTION,
        tenant_id="00000000-0000-4000-8000-000000000003",
    )


def _spec(
    strategy: AzureExecutionStrategy = AzureExecutionStrategy.SINGLE_VM,
) -> AzureGpuWorkerProvisioningSpec:
    return AzureGpuWorkerProvisioningSpec(
        strategy=strategy,
        deployment_name="gludd-worker-001",
        subscription_id=_SUBSCRIPTION,
        resource_group_id=_RESOURCE_GROUP_ID,
        resource_group_name=_RESOURCE_GROUP,
        location="eastus",
        vm_size="Standard_NC24ads_A100_v4",
        instance_count=1 if strategy is AzureExecutionStrategy.SINGLE_VM else 2,
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
        availability_zone=(
            "1" if strategy is AzureExecutionStrategy.SINGLE_VM else None
        ),
        availability_zones=(
            () if strategy is AzureExecutionStrategy.SINGLE_VM else ("1",)
        ),
        user_assigned_identity_id=(
            None
            if strategy is AzureExecutionStrategy.SINGLE_VM
            else (
                f"{_RESOURCE_GROUP_ID}/providers/Microsoft.ManagedIdentity/"
                "userAssignedIdentities/gludd-worker"
            )
        ),
        rdma_enabled=strategy is AzureExecutionStrategy.VMSS,
    )


def _policy(replica_count: int = 1) -> ModelWorkerLifecyclePolicy:
    return ModelWorkerLifecyclePolicy(
        launch_plan=RunnerLaunchPlan(
            runner_id="vllm",
            adapter_id="vllm-openai-v1",
            source_revision="sha256:runner",
            variant_id="model-q4",
            model_id="org/model",
            quantization="q4",
            distribution_mode=DistributionMode.EXPLICIT_PARALLEL,
            command=("/opt/gludd/bin/vllm", "serve", "org/model"),
            environment=(("CUDA_VISIBLE_DEVICES", "0,1"),),
            request_options=(("max_tokens", 1024),),
            replica_count=replica_count,
            devices_per_replica=2,
        ),
        release_id="b" * 64,
        topology_digest="c" * 64,
        backend="cuda",
        minimum_memory_mib=48_000,
        required_interconnect="nvlink",
        runtime_probe=("/opt/gludd/bin/vllm", "--version"),
        expected_runtime_version_digest=f"sha256:{'d' * 64}",
        configure_timeout_seconds=600,
    )


def _change(address: str, resource_type: str) -> dict[str, object]:
    return {
        "address": address,
        "type": resource_type,
        "change": {"actions": ["create"]},
    }


def _plan(strategy: AzureExecutionStrategy) -> dict[str, object]:
    if strategy is AzureExecutionStrategy.SINGLE_VM:
        values = (
            ("azurerm_virtual_network.worker", "azurerm_virtual_network"),
            ("azurerm_subnet.worker", "azurerm_subnet"),
            ("azurerm_public_ip.worker", "azurerm_public_ip"),
            ("azurerm_network_security_group.worker", "azurerm_network_security_group"),
            ("azurerm_network_interface.worker", "azurerm_network_interface"),
            (
                "azurerm_network_interface_security_group_association.worker",
                "azurerm_network_interface_security_group_association",
            ),
            ("azurerm_linux_virtual_machine.worker", "azurerm_linux_virtual_machine"),
        )
    else:
        values = (
            ("azurerm_virtual_network.worker", "azurerm_virtual_network"),
            ("azurerm_subnet.worker", "azurerm_subnet"),
            ("azurerm_network_security_group.worker", "azurerm_network_security_group"),
            (
                "azurerm_subnet_network_security_group_association.worker",
                "azurerm_subnet_network_security_group_association",
            ),
            ("azurerm_public_ip.egress", "azurerm_public_ip"),
            ("azurerm_nat_gateway.worker", "azurerm_nat_gateway"),
            (
                "azurerm_nat_gateway_public_ip_association.worker",
                "azurerm_nat_gateway_public_ip_association",
            ),
            (
                "azurerm_subnet_nat_gateway_association.worker",
                "azurerm_subnet_nat_gateway_association",
            ),
            (
                "azurerm_linux_virtual_machine_scale_set.worker",
                "azurerm_linux_virtual_machine_scale_set",
            ),
        )
    return {"resource_changes": [_change(*value) for value in values]}


def _output(value: object) -> dict[str, object]:
    return {"sensitive": False, "type": "dynamic", "value": value}


def _single_outputs() -> dict[str, object]:
    owned = (
        _VM_ID,
        f"{_RESOURCE_GROUP_ID}/providers/Microsoft.Network/networkInterfaces/worker",
        f"{_RESOURCE_GROUP_ID}/providers/Microsoft.Network/publicIPAddresses/worker",
    )
    return {
        "virtual_machine_id": _output(_VM_ID),
        "ansible_host": _output("203.0.113.10"),
        "ansible_user": _output("gludd"),
        "inference_endpoint": _output("http://203.0.113.10:8000"),
        "owned_resource_ids": _output(list(owned)),
    }


def _vmss_outputs() -> dict[str, object]:
    owned = (
        _VMSS_ID,
        f"{_RESOURCE_GROUP_ID}/providers/Microsoft.Network/virtualNetworks/worker",
    )
    return {
        "virtual_machine_scale_set_id": _output(_VMSS_ID),
        "ansible_user": _output("gludd"),
        "inference_port": _output(8000),
        "owned_resource_ids": _output(list(owned)),
    }


@dataclass
class _LeaseSource:
    acquired: list[AzureAcceleratorCredentialLease] = field(default_factory=list)
    released: list[AzureAcceleratorCredentialLease] = field(default_factory=list)

    def acquire(self) -> AzureAcceleratorCredentialLease:
        lease = AzureAcceleratorCredentialLease(
            credentials=_credentials(),
            lease_duration_seconds=900,
            renewable=False,
            _lease_id=f"azure/creds/gludd/{len(self.acquired)}",
        )
        self.acquired.append(lease)
        return lease

    def release(self, lease: AzureAcceleratorCredentialLease) -> None:
        self.released.append(lease)


@dataclass
class _Executor:
    plan: object
    outputs: object
    fail_phase: str | None = None
    calls: list[dict[str, Any]] = field(default_factory=list)

    def run(self, *, phase: str, **kwargs: Any) -> None:
        self.calls.append({"phase": phase, **kwargs})
        progress = kwargs["progress"]
        progress(phase, TerraformRuntimeState.STARTED, 0)
        if phase == self.fail_phase:
            raise RuntimeError("private provider failure")
        if phase == "show-plan":
            Path(kwargs["json_file"]).write_text(json.dumps(self.plan), encoding="utf-8")
        if phase == "output":
            Path(kwargs["json_file"]).write_text(
                json.dumps(self.outputs),
                encoding="utf-8",
            )
        progress(phase, TerraformRuntimeState.SUCCEEDED, 1)


@dataclass
class _SdkReader:
    instances: tuple[AzureGpuWorkerInstance, ...] = ()
    remaining: tuple[str, ...] = ()
    resolve_calls: list[dict[str, object]] = field(default_factory=list)
    remaining_calls: list[dict[str, object]] = field(default_factory=list)

    def resolve_vmss_instances(self, **kwargs: object) -> tuple[AzureGpuWorkerInstance, ...]:
        self.resolve_calls.append(kwargs)
        return self.instances

    def remaining_owned_resource_ids(self, **kwargs: object) -> tuple[str, ...]:
        self.remaining_calls.append(kwargs)
        return self.remaining


class _FailingSdkReader(_SdkReader):
    def remaining_owned_resource_ids(self, **kwargs: object) -> tuple[str, ...]:
        raise RuntimeError("private SDK failure")


class _ApplyAndDestroyFailExecutor(_Executor):
    def run(self, *, phase: str, **kwargs: Any) -> None:
        if phase in {"apply", "destroy"}:
            self.calls.append({"phase": phase, **kwargs})
            raise RuntimeError("private provider failure")
        super().run(phase=phase, **kwargs)


@dataclass
class _Materializer:
    result: Path | None = None
    failure: Exception | None = None

    def materialize(self, _spec: object, destination: object, **_kwargs: object) -> Path:
        if self.failure is not None:
            raise self.failure
        return self.result or Path(destination)  # type: ignore[arg-type]


def _runtime(
    tmp_path: Path,
    *,
    spec: AzureGpuWorkerProvisioningSpec,
    executor: _Executor,
    leases: _LeaseSource | None = None,
    sdk: _SdkReader | None = None,
    traces: list[AzureGpuWorkerRuntimeTrace] | None = None,
) -> AzureGpuWorkerInfrastructureRuntime:
    return AzureGpuWorkerInfrastructureRuntime(
        spec=spec,
        work_root=tmp_path / "runtime",
        credential_source=leases or _LeaseSource(),
        terraform_executor=executor,
        sdk_reader=sdk or _SdkReader(),
        trace_sink=(traces if traces is not None else []).append,
    )


def test_single_vm_provision_destroy_and_independent_absence(
    tmp_path: Path,
) -> None:
    leases = _LeaseSource()
    sdk = _SdkReader()
    traces: list[AzureGpuWorkerRuntimeTrace] = []
    executor = _Executor(_plan(AzureExecutionStrategy.SINGLE_VM), _single_outputs())
    runtime = _runtime(
        tmp_path,
        spec=_spec(),
        executor=executor,
        leases=leases,
        sdk=sdk,
        traces=traces,
    )

    deployment = runtime.provision(_policy())

    assert deployment.deployment_id == runtime.operation_digest
    assert len(deployment.hosts) == 1
    assert deployment.hosts[0].host_id == _VM_ID
    assert deployment.hosts[0].address == "203.0.113.10"
    assert deployment.hosts[0].endpoint_url == "http://203.0.113.10:8000/v1"
    assert deployment.hosts[0].ssh_private_key_path == "/run/gludd/keys/azure-worker"
    assert [call["phase"] for call in executor.calls] == [
        "init",
        "validate",
        "plan",
        "show-plan",
        "apply",
        "output",
    ]
    assert len(leases.acquired) == 6
    assert leases.released == leases.acquired
    assert traces[0].state is TerraformRuntimeState.STARTED
    assert traces[-1].state is TerraformRuntimeState.SUCCEEDED

    runtime.destroy(deployment)
    assert runtime.exists(deployment) is False

    assert [call["phase"] for call in executor.calls][-1] == "destroy"
    assert len(leases.acquired) == 8
    assert leases.released == leases.acquired
    assert sdk.remaining_calls[0]["owned_resource_ids"] == deployment.owned_resource_ids


def test_vmss_provision_uses_sdk_resolved_private_instances(tmp_path: Path) -> None:
    spec = _spec(AzureExecutionStrategy.VMSS)
    instances = (
        AzureGpuWorkerInstance(
            host_id=f"{_VMSS_ID}/virtualMachines/000000",
            address="10.43.1.4",
        ),
        AzureGpuWorkerInstance(
            host_id=f"{_VMSS_ID}/virtualMachines/000001",
            address="10.43.1.5",
        ),
    )
    sdk = _SdkReader(instances=instances)
    runtime = _runtime(
        tmp_path,
        spec=spec,
        executor=_Executor(_plan(AzureExecutionStrategy.VMSS), _vmss_outputs()),
        sdk=sdk,
    )

    deployment = runtime.provision(_policy(replica_count=2))

    assert [host.host_id for host in deployment.hosts] == [item.host_id for item in instances]
    assert [host.endpoint_url for host in deployment.hosts] == [
        "http://10.43.1.4:8000/v1",
        "http://10.43.1.5:8000/v1",
    ]
    assert sdk.resolve_calls[0]["scale_set_id"] == _VMSS_ID


def test_plan_audit_rejects_unreviewed_resource_before_apply(tmp_path: Path) -> None:
    plan = _plan(AzureExecutionStrategy.SINGLE_VM)
    plan["resource_changes"].append(
        _change("azurerm_role_assignment.wide", "azurerm_role_assignment")
    )
    executor = _Executor(plan, _single_outputs())
    runtime = _runtime(tmp_path, spec=_spec(), executor=executor)

    with pytest.raises(AzureGpuWorkerRuntimeError) as caught:
        runtime.provision(_policy())

    assert caught.value.phase == "plan-audit"
    assert "apply" not in [call["phase"] for call in executor.calls]


def test_partial_apply_failure_runs_owned_destroy_compensation(tmp_path: Path) -> None:
    executor = _Executor(
        _plan(AzureExecutionStrategy.SINGLE_VM),
        _single_outputs(),
        fail_phase="apply",
    )
    runtime = _runtime(tmp_path, spec=_spec(), executor=executor)

    with pytest.raises(AzureGpuWorkerRuntimeError) as caught:
        runtime.provision(_policy())

    assert caught.value.phase == "apply"
    assert [call["phase"] for call in executor.calls][-1] == "destroy"
    assert "private provider failure" not in str(caught.value)


def test_output_failure_after_apply_is_compensated(tmp_path: Path) -> None:
    outputs = _single_outputs()
    outputs["ansible_host"] = _output("wrong.example")
    executor = _Executor(_plan(AzureExecutionStrategy.SINGLE_VM), outputs)
    runtime = _runtime(tmp_path, spec=_spec(), executor=executor)

    with pytest.raises(AzureGpuWorkerRuntimeError) as caught:
        runtime.provision(_policy())

    assert caught.value.phase == "outputs"
    assert [call["phase"] for call in executor.calls][-1] == "destroy"


def test_refuses_policy_replica_drift_before_materializing(tmp_path: Path) -> None:
    executor = _Executor(_plan(AzureExecutionStrategy.VMSS), _vmss_outputs())
    runtime = _runtime(
        tmp_path,
        spec=_spec(AzureExecutionStrategy.VMSS),
        executor=executor,
    )

    with pytest.raises(AzureGpuWorkerRuntimeError) as caught:
        runtime.provision(_policy(replica_count=1))

    assert caught.value.phase == "policy"
    assert executor.calls == []


def test_destroy_and_exists_require_exact_owned_deployment(tmp_path: Path) -> None:
    runtime = _runtime(
        tmp_path,
        spec=_spec(),
        executor=_Executor(_plan(AzureExecutionStrategy.SINGLE_VM), _single_outputs()),
    )
    deployment = runtime.provision(_policy())
    foreign = replace(deployment, deployment_id="foreign")

    with pytest.raises(AzureGpuWorkerRuntimeError) as destroy:
        runtime.destroy(foreign)
    with pytest.raises(AzureGpuWorkerRuntimeError) as exists:
        runtime.exists(foreign)

    assert destroy.value.phase == "ownership"
    assert exists.value.phase == "ownership"


@pytest.mark.parametrize(
    "changes",
    [
        {"phase": "BAD"},
        {"state": "started"},
        {"operation_digest": "bad"},
        {"elapsed_seconds": -1},
    ],
)
def test_trace_contract_rejects_unbounded_values(changes: dict[str, object]) -> None:
    values: dict[str, object] = {
        "phase": "plan",
        "state": TerraformRuntimeState.STARTED,
        "operation_digest": "a" * 64,
        "elapsed_seconds": 0,
    }
    values.update(changes)

    with pytest.raises(ValueError):
        AzureGpuWorkerRuntimeTrace(**values)  # type: ignore[arg-type]


def test_constructor_and_unbound_contracts_fail_closed(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="spec"):
        AzureGpuWorkerInfrastructureRuntime(
            spec=object(),  # type: ignore[arg-type]
            work_root=tmp_path,
            credential_source=_LeaseSource(),
        )
    with pytest.raises(ValueError, match="credential_source"):
        AzureGpuWorkerInfrastructureRuntime(
            spec=_spec(),
            work_root=tmp_path,
            credential_source=object(),  # type: ignore[arg-type]
        )
    with pytest.raises(ValueError, match="trace_sink"):
        AzureGpuWorkerInfrastructureRuntime(
            spec=_spec(),
            work_root=tmp_path,
            credential_source=_LeaseSource(),
            trace_sink=None,  # type: ignore[arg-type]
        )
    runtime = _runtime(
        tmp_path,
        spec=_spec(),
        executor=_Executor(_plan(AzureExecutionStrategy.SINGLE_VM), _single_outputs()),
    )
    with pytest.raises(AzureGpuWorkerRuntimeError) as unbound:
        _ = runtime.operation_digest
    assert unbound.value.phase == "unbound"


def test_provision_is_idempotent_but_rejects_policy_drift(tmp_path: Path) -> None:
    executor = _Executor(_plan(AzureExecutionStrategy.SINGLE_VM), _single_outputs())
    runtime = _runtime(tmp_path, spec=_spec(), executor=executor)
    policy = _policy()

    first = runtime.provision(policy)
    second = runtime.provision(policy)

    assert second is first
    assert len(executor.calls) == 6
    with pytest.raises(AzureGpuWorkerRuntimeError) as drift:
        runtime.provision(replace(policy, release_id="e" * 64))
    assert drift.value.phase == "policy-drift"


def test_trace_sink_failure_is_censored_and_releases_credential(tmp_path: Path) -> None:
    leases = _LeaseSource()

    def fail_trace(_trace: AzureGpuWorkerRuntimeTrace) -> None:
        raise RuntimeError("private trace failure")

    runtime = AzureGpuWorkerInfrastructureRuntime(
        spec=_spec(),
        work_root=tmp_path / "runtime",
        credential_source=leases,
        terraform_executor=_Executor(
            _plan(AzureExecutionStrategy.SINGLE_VM),
            _single_outputs(),
        ),
        sdk_reader=_SdkReader(),
        trace_sink=fail_trace,
    )

    with pytest.raises(AzureGpuWorkerRuntimeError) as caught:
        runtime.provision(_policy())

    assert caught.value.phase == "trace"
    assert leases.released == leases.acquired
    assert "private trace failure" not in str(caught.value)


@pytest.mark.parametrize("failure", ["invalid", "mismatch", "release"])
def test_credential_lease_contract_fails_closed(tmp_path: Path, failure: str) -> None:
    class Source(_LeaseSource):
        def acquire(self) -> Any:
            if failure == "invalid":
                return object()
            lease = super().acquire()
            if failure == "mismatch":
                return replace(
                    lease,
                    credentials=replace(
                        lease.credentials,
                        subscription_id="00000000-0000-4000-8000-000000000009",
                    ),
                )
            return lease

        def release(self, lease: AzureAcceleratorCredentialLease) -> None:
            super().release(lease)
            if failure == "release":
                raise RuntimeError("private release failure")

    runtime = _runtime(
        tmp_path,
        spec=_spec(),
        executor=_Executor(_plan(AzureExecutionStrategy.SINGLE_VM), _single_outputs()),
        leases=Source(),
    )

    with pytest.raises(AzureGpuWorkerRuntimeError) as caught:
        runtime.provision(_policy())

    assert caught.value.phase in {"credentials", "credential-release"}


def test_symlink_work_root_is_rejected(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    work_root = tmp_path / "runtime"
    work_root.symlink_to(target, target_is_directory=True)
    runtime = AzureGpuWorkerInfrastructureRuntime(
        spec=_spec(),
        work_root=work_root,
        credential_source=_LeaseSource(),
        terraform_executor=_Executor(
            _plan(AzureExecutionStrategy.SINGLE_VM),
            _single_outputs(),
        ),
        sdk_reader=_SdkReader(),
    )

    with pytest.raises(AzureGpuWorkerRuntimeError) as caught:
        runtime.provision(_policy())

    assert caught.value.phase == "state-ownership"


@pytest.mark.parametrize(
    "plan",
    [
        None,
        {},
        {"resource_changes": [None]},
        {
            "resource_changes": [
                {
                    "address": "azurerm_virtual_network.worker",
                    "type": "azurerm_virtual_network",
                    "change": {"actions": ["delete"]},
                }
            ]
        },
    ],
)
def test_plan_audit_rejects_malformed_documents(tmp_path: Path, plan: object) -> None:
    runtime = _runtime(
        tmp_path,
        spec=_spec(),
        executor=_Executor(plan, _single_outputs()),
    )

    with pytest.raises(AzureGpuWorkerRuntimeError) as caught:
        runtime.provision(_policy())

    assert caught.value.phase == "plan-audit"


@pytest.mark.parametrize(
    "outputs",
    [
        None,
        {},
        {**_single_outputs(), "virtual_machine_id": {"sensitive": True, "value": _VM_ID}},
        {**_single_outputs(), "owned_resource_ids": _output([])},
        {
            **_single_outputs(),
            "owned_resource_ids": _output([_VM_ID, _VM_ID.upper()]),
        },
        {**_single_outputs(), "ansible_host": _output("0.0.0.0")},
    ],
)
def test_output_parser_rejects_malformed_or_unowned_values(
    tmp_path: Path,
    outputs: object,
) -> None:
    executor = _Executor(_plan(AzureExecutionStrategy.SINGLE_VM), outputs)
    runtime = _runtime(tmp_path, spec=_spec(), executor=executor)

    with pytest.raises(AzureGpuWorkerRuntimeError) as caught:
        runtime.provision(_policy())

    assert caught.value.phase == "outputs"
    assert [call["phase"] for call in executor.calls][-1] == "destroy"


def test_vmss_rejects_incomplete_sdk_inventory(tmp_path: Path) -> None:
    runtime = _runtime(
        tmp_path,
        spec=_spec(AzureExecutionStrategy.VMSS),
        executor=_Executor(_plan(AzureExecutionStrategy.VMSS), _vmss_outputs()),
        sdk=_SdkReader(
            instances=(
                AzureGpuWorkerInstance(
                    host_id=f"{_VMSS_ID}/virtualMachines/000000",
                    address="10.43.1.4",
                ),
            )
        ),
    )

    with pytest.raises(AzureGpuWorkerRuntimeError) as caught:
        runtime.provision(_policy(replica_count=2))

    assert caught.value.phase == "outputs"


def test_compensation_failure_is_reported_without_provider_detail(tmp_path: Path) -> None:
    executor = _ApplyAndDestroyFailExecutor(
        _plan(AzureExecutionStrategy.SINGLE_VM),
        _single_outputs(),
    )
    runtime = _runtime(tmp_path, spec=_spec(), executor=executor)

    with pytest.raises(AzureGpuWorkerRuntimeError) as caught:
        runtime.provision(_policy())

    assert caught.value.phase == "apply"
    assert caught.value.cleanup_failed is True
    assert "private provider failure" not in str(caught.value)


@pytest.mark.parametrize("mode", ["wrong-path", "exception"])
def test_materializer_failure_is_censored(tmp_path: Path, mode: str) -> None:
    materializer = (
        _Materializer(result=tmp_path / "foreign")
        if mode == "wrong-path"
        else _Materializer(failure=RuntimeError("private materializer failure"))
    )
    runtime = AzureGpuWorkerInfrastructureRuntime(
        spec=_spec(),
        work_root=tmp_path / "runtime",
        credential_source=_LeaseSource(),
        terraform_executor=_Executor(
            _plan(AzureExecutionStrategy.SINGLE_VM),
            _single_outputs(),
        ),
        materializer=materializer,
        sdk_reader=_SdkReader(),
    )

    with pytest.raises(AzureGpuWorkerRuntimeError) as caught:
        runtime.provision(_policy())

    assert caught.value.phase in {"materialize", "provision"}
    assert "private materializer failure" not in str(caught.value)


def test_absence_sdk_failure_is_censored(tmp_path: Path) -> None:
    runtime = _runtime(
        tmp_path,
        spec=_spec(),
        executor=_Executor(_plan(AzureExecutionStrategy.SINGLE_VM), _single_outputs()),
        sdk=_FailingSdkReader(),
    )
    deployment = runtime.provision(_policy())

    with pytest.raises(AzureGpuWorkerRuntimeError) as caught:
        runtime.exists(deployment)

    assert caught.value.phase == "absence"
    assert "private SDK failure" not in str(caught.value)
