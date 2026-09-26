"""Hermetic full-chain proof for Azure GPU model-worker ownership."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
import yaml

from general_ludd.azure.accelerator_credential_source import (
    AzureAcceleratorCredentialLease,
)
from general_ludd.azure.accelerator_credentials import AzureAcceleratorCredentials
from general_ludd.hardware.accelerator_topology import DistributionMode
from general_ludd.hardware.model_runner_launch_contracts import RunnerLaunchPlan
from general_ludd.infra.ansible_model_worker_runtime import (
    AnsibleModelWorkerConfigurationRuntime,
)
from general_ludd.infra.azure_containerapp_terraform_executor import (
    TerraformRuntimeState,
)
from general_ludd.infra.azure_gpu_model_worker_service import (
    AzureGpuModelWorkerService,
)
from general_ludd.infra.azure_gpu_vm_strategy import AzureExecutionStrategy
from general_ludd.infra.azure_gpu_worker_materializer import (
    AzureGpuWorkerProvisioningSpec,
)
from general_ludd.infra.azure_gpu_worker_runtime import (
    AzureGpuWorkerInfrastructureRuntime,
)
from general_ludd.infra.azure_gpu_worker_sdk import AzureGpuWorkerInstance
from general_ludd.infra.model_worker_lifecycle import ModelWorkerLifecyclePolicy
from general_ludd.models.gateway import ModelGateway, ModelResponse

_SUBSCRIPTION = "00000000-0000-4000-8000-000000000001"
_RESOURCE_GROUP = "gludd-accelerators"
_RESOURCE_GROUP_ID = f"/subscriptions/{_SUBSCRIPTION}/resourceGroups/{_RESOURCE_GROUP}"
_DEPLOYMENT_NAME = "gludd-worker-001"
_VM_ID = f"{_RESOURCE_GROUP_ID}/providers/Microsoft.Compute/virtualMachines/{_DEPLOYMENT_NAME}-vm"
_VMSS_ID = f"{_RESOURCE_GROUP_ID}/providers/Microsoft.Compute/virtualMachineScaleSets/{_DEPLOYMENT_NAME}-vmss"
_RELEASE = "b" * 64
_TOPOLOGY = "c" * 64
_RUNTIME = f"sha256:{'d' * 64}"


def _spec(strategy: AzureExecutionStrategy) -> AzureGpuWorkerProvisioningSpec:
    return AzureGpuWorkerProvisioningSpec(
        strategy=strategy,
        deployment_name=_DEPLOYMENT_NAME,
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
        availability_zone="1" if strategy is AzureExecutionStrategy.SINGLE_VM else None,
        availability_zones=() if strategy is AzureExecutionStrategy.SINGLE_VM else ("1",),
        user_assigned_identity_id=(
            None
            if strategy is AzureExecutionStrategy.SINGLE_VM
            else (f"{_RESOURCE_GROUP_ID}/providers/Microsoft.ManagedIdentity/userAssignedIdentities/gludd-worker")
        ),
        rdma_enabled=strategy is AzureExecutionStrategy.VMSS,
    )


def _policy(
    *,
    runner_id: str,
    adapter_id: str,
    replica_count: int,
) -> ModelWorkerLifecyclePolicy:
    commands = {
        "vllm": ("/opt/gludd/bin/vllm", "serve", "org/model"),
        "ollama": ("/opt/gludd/bin/ollama", "serve"),
        "llama.cpp": ("/opt/gludd/bin/llama-server", "-m", "org/model"),
    }
    return ModelWorkerLifecyclePolicy(
        launch_plan=RunnerLaunchPlan(
            runner_id=runner_id,
            adapter_id=adapter_id,
            source_revision="sha256:runner",
            variant_id="model-q4",
            model_id="org/model",
            quantization="q4",
            distribution_mode=DistributionMode.EXPLICIT_PARALLEL,
            command=commands[runner_id],
            environment=(("CUDA_VISIBLE_DEVICES", "0,1"),),
            request_options=(("max_tokens", 1_024),),
            replica_count=replica_count,
            devices_per_replica=2,
        ),
        release_id=_RELEASE,
        topology_digest=_TOPOLOGY,
        backend="cuda",
        minimum_memory_mib=48_000,
        required_interconnect="nvlink",
        runtime_probe=(commands[runner_id][0], "--version"),
        expected_runtime_version_digest=_RUNTIME,
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
        resources = (
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
        resources = (
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
    return {"resource_changes": [_change(*resource) for resource in resources]}


def _output(value: object) -> dict[str, object]:
    return {"sensitive": False, "type": "dynamic", "value": value}


def _outputs(strategy: AzureExecutionStrategy) -> dict[str, object]:
    if strategy is AzureExecutionStrategy.SINGLE_VM:
        return {
            "virtual_machine_id": _output(_VM_ID),
            "ansible_host": _output("203.0.113.10"),
            "ansible_user": _output("gludd"),
            "inference_endpoint": _output("http://203.0.113.10:8000"),
            "owned_resource_ids": _output(
                [
                    _VM_ID,
                    f"{_RESOURCE_GROUP_ID}/providers/Microsoft.Network/networkInterfaces/worker",
                ]
            ),
        }
    return {
        "virtual_machine_scale_set_id": _output(_VMSS_ID),
        "ansible_user": _output("gludd"),
        "inference_port": _output(8000),
        "owned_resource_ids": _output(
            [
                _VMSS_ID,
                f"{_RESOURCE_GROUP_ID}/providers/Microsoft.Network/virtualNetworks/worker",
            ]
        ),
    }


def _credentials() -> AzureAcceleratorCredentials:
    return AzureAcceleratorCredentials(
        client_id="00000000-0000-4000-8000-000000000002",
        client_secret="private-secret",  # pragma: allowlist secret
        subscription_id=_SUBSCRIPTION,
        tenant_id="00000000-0000-4000-8000-000000000003",
    )


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
class _TerraformExecutor:
    strategy: AzureExecutionStrategy
    phases: list[str] = field(default_factory=list)

    def run(self, *, phase: str, **kwargs: Any) -> None:
        self.phases.append(phase)
        progress = kwargs["progress"]
        progress(phase, TerraformRuntimeState.STARTED, 0)
        path = Path(kwargs["json_file"])
        if phase == "show-plan":
            path.write_text(json.dumps(_plan(self.strategy)), encoding="utf-8")
        elif phase == "output":
            path.write_text(json.dumps(_outputs(self.strategy)), encoding="utf-8")
        progress(phase, TerraformRuntimeState.SUCCEEDED, 1)


@dataclass
class _SdkReader:
    strategy: AzureExecutionStrategy
    absence_checks: int = 0

    def resolve_vmss_instances(self, **kwargs: object) -> tuple[AzureGpuWorkerInstance, ...]:
        assert self.strategy is AzureExecutionStrategy.VMSS
        assert kwargs["scale_set_id"] == _VMSS_ID
        return (
            AzureGpuWorkerInstance(
                host_id=f"{_VMSS_ID}/virtualMachines/000000",
                address="10.43.1.4",
            ),
            AzureGpuWorkerInstance(
                host_id=f"{_VMSS_ID}/virtualMachines/000001",
                address="10.43.1.5",
            ),
        )

    def resolve_single_vm(
        self,
        **kwargs: object,
    ) -> AzureGpuWorkerInstance:
        assert self.strategy is AzureExecutionStrategy.SINGLE_VM
        assert kwargs["vm_id"] == _VM_ID
        return AzureGpuWorkerInstance(
            host_id=_VM_ID,
            address="203.0.113.10",
        )

    def delete_owned_resources(self, **kwargs: object) -> tuple[str, ...]:
        assert kwargs["owned_resource_ids"]
        return ()

    def remaining_owned_resource_ids(self, **kwargs: object) -> tuple[str, ...]:
        self.absence_checks += 1
        assert kwargs["owned_resource_ids"]
        return ()


@dataclass
class _AnsibleRunner:
    playbooks: list[str] = field(default_factory=list)
    launch_plans: list[dict[str, object]] = field(default_factory=list)

    def run_playbook(self, playbook_name: str, **kwargs: Any) -> dict[str, Any]:
        self.playbooks.append(playbook_name)
        if playbook_name == "model_worker_retire.yml":
            return {"status": "successful", "rc": 0, "events": []}
        extravars = kwargs["extravars"]
        plan = extravars["gludd_model_worker_plan"]
        self.launch_plans.append(plan)
        inventory = yaml.safe_load(Path(kwargs["inventory"][0]).read_text(encoding="utf-8"))
        aliases = inventory["all"]["children"]["gludd_model_workers"]["hosts"]
        service_name = f"gludd-model-worker-{_RELEASE[:12]}.service"
        candidate = {
            "adapter_id": plan["adapter_id"],
            "attestation_path": (f"/var/lib/gludd/model-workers/attestations/gludd-model-worker-{_RELEASE[:12]}.json"),
            "backend": "cuda",
            "devices_per_replica": plan["devices_per_replica"],
            "driver_version_digest": f"sha256:{'e' * 64}",
            "health_url": "http://127.0.0.1:8000/healthz",
            "interconnect": "nvlink",
            "observed_inventory_digest": "f" * 64,
            "ready": True,
            "release_id": _RELEASE,
            "runner_id": plan["runner_id"],
            "runtime_version_digest": _RUNTIME,
            "service_name": service_name,
            "source_revision": plan["source_revision"],
            "topology_digest": _TOPOLOGY,
        }
        return {
            "status": "successful",
            "rc": 0,
            "events": [
                {
                    "event": "runner_on_ok",
                    "host": alias,
                    "result": {
                        "ansible_facts": {
                            "gludd_model_worker_candidate": candidate,
                        }
                    },
                }
                for alias in aliases
            ],
        }


@dataclass
class _EndpointCaller:
    endpoint_url: str
    runner_id: str
    closed: bool = False

    def call_model(
        self,
        profile_id: str,
        messages: list[dict[str, str]],
        **kwargs: Any,
    ) -> ModelResponse:
        return ModelResponse(
            content=f"{self.runner_id}:{self.endpoint_url}",
            model_name="org/model",
            cost_estimate=0.0,
        )

    def call_model_stream(
        self,
        profile_id: str,
        messages: list[dict[str, str]],
        **kwargs: Any,
    ) -> Any:
        yield self.endpoint_url

    def close(self) -> None:
        self.closed = True


@pytest.mark.parametrize(
    ("runner_id", "adapter_id"),
    [
        ("vllm", "vllm-openai-v1"),
        ("ollama", "ollama-openai-v1"),
        ("llama.cpp", "llama-cpp-openai-v1"),
    ],
)
@pytest.mark.parametrize(
    "strategy",
    [AzureExecutionStrategy.SINGLE_VM, AzureExecutionStrategy.VMSS],
)
def test_full_owned_worker_chain_serves_and_cleans_every_runner(
    tmp_path: Path,
    strategy: AzureExecutionStrategy,
    runner_id: str,
    adapter_id: str,
) -> None:
    spec = _spec(strategy)
    leases = _LeaseSource()
    executor = _TerraformExecutor(strategy)
    sdk = _SdkReader(strategy)
    ansible = _AnsibleRunner()
    callers: list[_EndpointCaller] = []
    policy = _policy(
        runner_id=runner_id,
        adapter_id=adapter_id,
        replica_count=spec.instance_count,
    )

    def _endpoint_factory(
        endpoint_url: str,
        endpoint_profile_id: str,
        model_name: str,
    ) -> _EndpointCaller:
        assert endpoint_profile_id.startswith("local-owned-worker-")
        assert model_name == "org/model"
        caller = _EndpointCaller(endpoint_url, runner_id)
        callers.append(caller)
        return caller

    infrastructure = AzureGpuWorkerInfrastructureRuntime(
        spec=spec,
        work_root=tmp_path / "runtime",
        credential_source=leases,
        terraform_executor=executor,
        sdk_reader=sdk,
    )
    configuration = AnsibleModelWorkerConfigurationRuntime(ansible)
    service = AzureGpuModelWorkerService.compose(
        gateway=ModelGateway(),
        infrastructure=infrastructure,
        configuration=configuration,
        profile_id="azure-owned-model",
        model_name="org/model",
        endpoint_gateway_factory=_endpoint_factory,
        lifecycle_trace_sink=lambda _trace: None,
        profile_options={"role_names": ["chemistry", "firmware", "self_improvement"]},
    )

    pool = service.acquire(policy)
    responses = [
        service.call_model(
            "azure-owned-model",
            [{"role": "user", "content": "universal work"}],
        ).content
        for _host in pool.endpoints
    ]

    assert len(callers) == spec.instance_count
    assert {response.partition(":")[0] for response in responses} == {runner_id}
    assert {response.removeprefix(f"{runner_id}:") for response in responses} == {
        endpoint.endpoint_url for endpoint in pool.endpoints
    }
    assert ansible.launch_plans[0] == policy.launch_plan.to_dict()
    assert ansible.playbooks == ["model_worker_deploy.yml"]
    assert executor.phases == [
        "init",
        "validate",
        "plan",
        "show-plan",
        "apply",
        "output",
    ]

    pool.close()

    assert ansible.playbooks == [
        "model_worker_deploy.yml",
        "model_worker_retire.yml",
    ]
    assert executor.phases[-1] == "destroy"
    assert sdk.absence_checks == 1
    assert leases.released == leases.acquired
    assert all(caller.closed for caller in callers)
    assert service.gateway.get_profile("azure-owned-model") is None
    materialized = next((tmp_path / "runtime").iterdir())
    assert (materialized / "main.tf").is_file()
    assert (materialized / "terraform.tfvars.json").is_file()
