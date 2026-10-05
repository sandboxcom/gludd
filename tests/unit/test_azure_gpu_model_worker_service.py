"""Tests for the composed Azure GPU model-worker service boundary."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

import pytest

from general_ludd.hardware.accelerator_topology import DistributionMode
from general_ludd.hardware.model_runner_launch_contracts import RunnerLaunchPlan
from general_ludd.infra.azure_gpu_model_worker_service import (
    AzureGpuModelWorkerService,
    build_azure_gpu_model_worker_service,
)
from general_ludd.infra.model_worker_lifecycle import (
    ModelWorkerEndpoint,
    ModelWorkerHost,
    ModelWorkerLifecycleEvent,
    ModelWorkerLifecyclePolicy,
    ModelWorkerLifecycleTrace,
    ProvisionedModelWorkerPool,
)
from general_ludd.models.gateway import ModelGateway, ModelResponse

_DIGEST_A = "a" * 64
_DIGEST_B = "b" * 64
_DIGEST_C = "c" * 64


def _policy() -> ModelWorkerLifecyclePolicy:
    return ModelWorkerLifecyclePolicy(
        launch_plan=RunnerLaunchPlan(
            runner_id="vllm",
            adapter_id="vllm-openai-v1",
            source_revision="sha256:runner",
            variant_id="model-q4",
            model_id="org/model-q4",
            quantization="q4",
            distribution_mode=DistributionMode.EXPLICIT_PARALLEL,
            command=("/opt/gludd/bin/vllm", "serve", "org/model-q4"),
            environment=(("CUDA_VISIBLE_DEVICES", "0,1"),),
            request_options=(("max_tokens", 1_024),),
            replica_count=1,
            devices_per_replica=2,
        ),
        release_id=_DIGEST_A,
        topology_digest=_DIGEST_B,
        backend="cuda",
        minimum_memory_mib=48_000,
        required_interconnect="nvlink",
        runtime_probe=("/opt/gludd/bin/vllm", "--version"),
        expected_runtime_version_digest=f"sha256:{_DIGEST_C}",
        configure_timeout_seconds=600,
    )


def _deployment() -> ProvisionedModelWorkerPool:
    return ProvisionedModelWorkerPool(
        deployment_id="azure-owned-deployment",
        hosts=(
            ModelWorkerHost(
                host_id="azure-worker-01",
                address="10.42.1.4",
                ansible_user="gludd",
                ssh_private_key_path="/run/gludd/keys/worker",
                endpoint_url="http://10.42.1.4:8000/v1",
            ),
        ),
        owned_resource_ids=("/subscriptions/sub/resourceGroups/rg/providers/X/y",),
    )


def _endpoint() -> ModelWorkerEndpoint:
    return ModelWorkerEndpoint(
        host_id="azure-worker-01",
        endpoint_url="http://10.42.1.4:8000/v1",
        service_name="gludd-model-worker-aaaaaaaaaaaa.service",
        attestation_digest=_DIGEST_C,
    )


@dataclass
class _Infrastructure:
    calls: list[str]
    remains: bool = False

    def provision(self, policy: ModelWorkerLifecyclePolicy) -> ProvisionedModelWorkerPool:
        self.calls.append("provision")
        return _deployment()

    def destroy(self, deployment: ProvisionedModelWorkerPool) -> None:
        self.calls.append("destroy")

    def exists(self, deployment: ProvisionedModelWorkerPool) -> bool:
        self.calls.append("exists")
        return self.remains


@dataclass
class _Configuration:
    calls: list[str]

    def configure(
        self,
        deployment: ProvisionedModelWorkerPool,
        policy: ModelWorkerLifecyclePolicy,
    ) -> tuple[ModelWorkerEndpoint, ...]:
        self.calls.append("configure")
        return (_endpoint(),)

    def retire(
        self,
        deployment: ProvisionedModelWorkerPool,
        endpoints: tuple[ModelWorkerEndpoint, ...],
    ) -> None:
        self.calls.append("retire")


@dataclass
class _EndpointCaller:
    endpoint_url: str
    profile_id: str
    calls: list[str]
    closed: bool = False

    def call_model(
        self,
        profile_id: str,
        messages: list[dict[str, str]],
        **kwargs: Any,
    ) -> ModelResponse:
        self.calls.append("model")
        return ModelResponse(
            content='{"candidate":"polymer"}',
            model_name="org/model-q4",
            cost_estimate=0.25,
        )

    def call_model_stream(
        self,
        profile_id: str,
        messages: list[dict[str, str]],
        **kwargs: Any,
    ) -> Any:
        self.calls.append("stream")
        yield "chunk-one"
        yield "chunk-two"

    def close(self) -> None:
        self.calls.append("endpoint-close")
        self.closed = True


@dataclass
class _EndpointFactory:
    calls: list[str]
    callers: list[_EndpointCaller] = field(default_factory=list)

    def __call__(
        self,
        endpoint_url: str,
        endpoint_profile_id: str,
        model_name: str,
    ) -> _EndpointCaller:
        caller = _EndpointCaller(endpoint_url, endpoint_profile_id, self.calls)
        self.callers.append(caller)
        return caller


def test_composed_service_serves_buffered_and_streaming_universal_work() -> None:
    calls: list[str] = []
    traces: list[ModelWorkerLifecycleTrace] = []
    gateway = ModelGateway()
    factory = _EndpointFactory(calls)
    service = AzureGpuModelWorkerService.compose(
        gateway=gateway,
        infrastructure=_Infrastructure(calls),
        configuration=_Configuration(calls),
        profile_id="azure-owned-model",
        model_name="org/model-q4",
        endpoint_gateway_factory=factory,
        lifecycle_trace_sink=traces.append,
        profile_options={"role_names": ["chemistry", "firmware", "self_improvement"]},
    )

    pool = service.acquire(_policy())
    response = service.call_model(
        "azure-owned-model",
        [{"role": "user", "content": "design a plastic molecule"}],
        budget_remaining=1.0,
    )
    stream = list(
        service.call_model_stream(
            "azure-owned-model",
            [{"role": "user", "content": "write Arduino firmware"}],
        )
    )

    assert response.content == '{"candidate":"polymer"}'
    assert response.cost_estimate == 0.25
    assert stream == ["chunk-one", "chunk-two"]
    assert service.profile_id == "azure-owned-model"
    assert service.gateway is gateway
    assert gateway.get_profile("azure-owned-model") is not None

    pool.close()

    assert calls == [
        "provision",
        "configure",
        "model",
        "stream",
        "endpoint-close",
        "retire",
        "destroy",
        "exists",
    ]
    assert factory.callers[0].closed is True
    assert gateway.get_profile("azure-owned-model") is None
    assert traces[0].event is ModelWorkerLifecycleEvent.PROVISION_STARTED
    assert traces[-1].event is ModelWorkerLifecycleEvent.ABSENCE_VERIFIED


def test_service_rejects_calls_for_a_different_logical_profile() -> None:
    calls: list[str] = []
    service = AzureGpuModelWorkerService.compose(
        gateway=ModelGateway(),
        infrastructure=_Infrastructure(calls),
        configuration=_Configuration(calls),
        profile_id="azure-owned-model",
        model_name="org/model-q4",
        endpoint_gateway_factory=_EndpointFactory(calls),
        lifecycle_trace_sink=lambda _trace: None,
    )
    pool = service.acquire(_policy())

    with pytest.raises(ValueError, match="profile"):
        service.call_model("other-model", [])

    pool.close()


def test_service_rejects_invalid_policy_and_model_identity_drift() -> None:
    calls: list[str] = []
    service = AzureGpuModelWorkerService.compose(
        gateway=ModelGateway(),
        infrastructure=_Infrastructure(calls),
        configuration=_Configuration(calls),
        profile_id="azure-owned-model",
        model_name="org/model-q4",
        endpoint_gateway_factory=_EndpointFactory(calls),
        lifecycle_trace_sink=lambda _trace: None,
    )

    with pytest.raises(ValueError, match="policy"):
        service.acquire(object())  # type: ignore[arg-type]
    mismatched = replace(
        _policy(),
        launch_plan=replace(_policy().launch_plan, model_id="org/other-model"),
    )
    with pytest.raises(ValueError, match="model identity"):
        service.acquire(mismatched)

    assert calls == []


@pytest.mark.parametrize(
    "dependency",
    [
        {"gateway": object()},
        {"infrastructure": object()},
        {"configuration": object()},
        {"lifecycle_trace_sink": None},
    ],
)
def test_composition_rejects_invalid_lifecycle_dependencies(
    dependency: dict[str, object],
) -> None:
    values: dict[str, object] = {
        "gateway": ModelGateway(),
        "infrastructure": _Infrastructure([]),
        "configuration": _Configuration([]),
        "profile_id": "azure-owned-model",
        "model_name": "org/model-q4",
        "endpoint_gateway_factory": _EndpointFactory([]),
        "lifecycle_trace_sink": lambda _trace: None,
    }
    values.update(dependency)

    with pytest.raises(ValueError):
        AzureGpuModelWorkerService.compose(**values)  # type: ignore[arg-type]


def test_concrete_builder_validates_openbao_and_ansible_boundaries() -> None:
    class _CredentialSource:
        def acquire(self) -> object:
            raise AssertionError("not called during construction")

        def release(self, lease: object) -> None:
            raise AssertionError("not called during construction")

    class _Runner:
        def run_playbook(self, playbook_name: str, **kwargs: Any) -> dict[str, Any]:
            raise AssertionError("not called during construction")

    spec = object()
    with pytest.raises(ValueError, match="spec"):
        build_azure_gpu_model_worker_service(
            gateway=ModelGateway(),
            spec=spec,  # type: ignore[arg-type]
            work_root="/tmp/gludd-owned-worker-test",
            credential_source=_CredentialSource(),  # type: ignore[arg-type]
            ansible_runner=_Runner(),
            profile_id="azure-owned-model",
            model_name="org/model-q4",
            lifecycle_trace_sink=lambda _trace: None,
        )
