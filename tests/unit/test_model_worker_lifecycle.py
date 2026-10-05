"""Tests for provider-neutral owned model-worker lifecycle orchestration."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import cast

import pytest

from general_ludd.hardware.accelerator_topology import DistributionMode
from general_ludd.hardware.model_runner_launch_contracts import RunnerLaunchPlan
from general_ludd.infra.model_worker_lifecycle import (
    ModelWorkerEndpoint,
    ModelWorkerHost,
    ModelWorkerLifecycleError,
    ModelWorkerLifecycleEvent,
    ModelWorkerLifecycleManager,
    ModelWorkerLifecyclePolicy,
    ModelWorkerLifecycleTrace,
    ProvisionedModelWorkerPool,
)

_DIGEST_A = "a" * 64
_DIGEST_B = "b" * 64
_DIGEST_C = "c" * 64


def _policy() -> ModelWorkerLifecyclePolicy:
    plan = RunnerLaunchPlan(
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
        replica_count=1,
        devices_per_replica=2,
    )
    return ModelWorkerLifecyclePolicy(
        launch_plan=plan,
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
        deployment_id="deployment-01",
        hosts=(
            ModelWorkerHost(
                host_id="worker-01",
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
        host_id="worker-01",
        endpoint_url="http://10.42.1.4:8000/v1",
        service_name="gludd-model-worker-aaaaaaaaaaaa.service",
        attestation_digest=_DIGEST_C,
    )


@dataclass
class _Infrastructure:
    calls: list[str]
    remains: bool = False
    fail_provision: bool = False

    def provision(
        self,
        policy: ModelWorkerLifecyclePolicy,
    ) -> ProvisionedModelWorkerPool:
        self.calls.append("provision")
        if self.fail_provision:
            raise RuntimeError("provider detail must be censored")
        return _deployment()

    def destroy(self, deployment: ProvisionedModelWorkerPool) -> None:
        self.calls.append("destroy")

    def exists(self, deployment: ProvisionedModelWorkerPool) -> bool:
        self.calls.append("exists")
        return self.remains


@dataclass
class _Configuration:
    calls: list[str]
    fail_configure: bool = False
    fail_retire: bool = False

    def configure(
        self,
        deployment: ProvisionedModelWorkerPool,
        policy: ModelWorkerLifecyclePolicy,
    ) -> tuple[ModelWorkerEndpoint, ...]:
        self.calls.append("configure")
        if self.fail_configure:
            raise RuntimeError("guest detail must be censored")
        return (_endpoint(),)

    def retire(
        self,
        deployment: ProvisionedModelWorkerPool,
        endpoints: tuple[ModelWorkerEndpoint, ...],
    ) -> None:
        self.calls.append("retire")
        if self.fail_retire:
            raise RuntimeError("retire detail must be censored")


@dataclass
class _Dispatcher:
    calls: list[str]
    fail_publish: bool = False
    fail_withdraw: bool = False

    def publish(
        self,
        deployment: ProvisionedModelWorkerPool,
        endpoints: tuple[ModelWorkerEndpoint, ...],
    ) -> str:
        self.calls.append("publish")
        if self.fail_publish:
            raise RuntimeError("router detail must be censored")
        return "dispatch-lease-01"

    def withdraw(self, dispatch_lease: str) -> None:
        self.calls.append("withdraw")
        if self.fail_withdraw:
            raise RuntimeError("drain detail must be censored")


@dataclass
class _Harness:
    calls: list[str] = field(default_factory=list)
    traces: list[ModelWorkerLifecycleTrace] = field(default_factory=list)

    def manager(
        self,
        *,
        remains: bool = False,
        fail_provision: bool = False,
        fail_configure: bool = False,
        fail_retire: bool = False,
        fail_publish: bool = False,
        fail_withdraw: bool = False,
    ) -> ModelWorkerLifecycleManager:
        return ModelWorkerLifecycleManager(
            infrastructure=_Infrastructure(
                self.calls,
                remains=remains,
                fail_provision=fail_provision,
            ),
            configuration=_Configuration(
                self.calls,
                fail_configure=fail_configure,
                fail_retire=fail_retire,
            ),
            dispatcher=_Dispatcher(
                self.calls,
                fail_publish=fail_publish,
                fail_withdraw=fail_withdraw,
            ),
            trace_sink=self.traces.append,
        )


def test_success_publishes_only_ready_workers_and_closes_in_owned_order() -> None:
    harness = _Harness()
    pool = harness.manager().acquire(_policy())

    assert pool.active is True
    assert pool.endpoints == (_endpoint(),)
    assert harness.calls == ["provision", "configure", "publish"]

    pool.close()
    pool.close()

    assert pool.active is False
    assert harness.calls == [
        "provision",
        "configure",
        "publish",
        "withdraw",
        "retire",
        "destroy",
        "exists",
    ]
    assert [trace.event for trace in harness.traces] == [
        ModelWorkerLifecycleEvent.PROVISION_STARTED,
        ModelWorkerLifecycleEvent.PROVISIONED,
        ModelWorkerLifecycleEvent.CONFIGURE_STARTED,
        ModelWorkerLifecycleEvent.READY,
        ModelWorkerLifecycleEvent.PUBLISH_STARTED,
        ModelWorkerLifecycleEvent.PUBLISHED,
        ModelWorkerLifecycleEvent.DRAIN_STARTED,
        ModelWorkerLifecycleEvent.DRAINED,
        ModelWorkerLifecycleEvent.RETIRE_STARTED,
        ModelWorkerLifecycleEvent.RETIRED,
        ModelWorkerLifecycleEvent.DESTROY_STARTED,
        ModelWorkerLifecycleEvent.DESTROYED,
        ModelWorkerLifecycleEvent.ABSENCE_VERIFIED,
    ]


def test_configuration_failure_destroys_replacement_without_publication() -> None:
    harness = _Harness()

    with pytest.raises(ModelWorkerLifecycleError) as caught:
        harness.manager(fail_configure=True).acquire(_policy())

    assert caught.value.phase == "configure"
    assert caught.value.cleanup_failed is False
    assert "guest detail" not in str(caught.value)
    assert harness.calls == ["provision", "configure", "destroy", "exists"]


def test_publish_failure_retires_then_destroys_unrouted_replacement() -> None:
    harness = _Harness()

    with pytest.raises(ModelWorkerLifecycleError) as caught:
        harness.manager(fail_publish=True).acquire(_policy())

    assert caught.value.phase == "publish"
    assert caught.value.cleanup_failed is False
    assert harness.calls == [
        "provision",
        "configure",
        "publish",
        "retire",
        "destroy",
        "exists",
    ]


def test_cleanup_reports_retire_or_absence_failure_after_trying_every_owner() -> None:
    harness = _Harness()
    pool = harness.manager(remains=True, fail_retire=True).acquire(_policy())

    with pytest.raises(ModelWorkerLifecycleError) as caught:
        pool.close()

    assert caught.value.phase == "cleanup"
    assert caught.value.cleanup_failed is True
    assert harness.calls[-4:] == ["withdraw", "retire", "destroy", "exists"]


def test_drain_failure_preserves_active_service_and_infrastructure() -> None:
    harness = _Harness()
    pool = harness.manager(fail_withdraw=True).acquire(_policy())

    with pytest.raises(ModelWorkerLifecycleError) as caught:
        pool.close()

    assert caught.value.phase == "cleanup"
    assert caught.value.cleanup_failed is True
    assert pool.active is True
    assert harness.calls == ["provision", "configure", "publish", "withdraw"]


def test_traces_are_content_free_and_bind_only_policy_digest_and_counts() -> None:
    harness = _Harness()
    pool = harness.manager().acquire(_policy())
    pool.close()

    for trace in harness.traces:
        assert trace.operation_digest == _policy().operation_digest
        assert trace.worker_count in {0, 1}
        rendered = repr(trace)
        assert "org/model" not in rendered
        assert "10.42.1.4" not in rendered
        assert "resourceGroups" not in rendered
        assert "dispatch-lease-01" not in rendered


@pytest.mark.parametrize(
    ("factory", "message"),
    [
        (
            lambda: replace(_deployment().hosts[0], address=""),
            "bounded non-empty text",
        ),
        (
            lambda: replace(_deployment().hosts[0], address="host\nname"),
            "control delimiter",
        ),
        (
            lambda: replace(_endpoint(), attestation_digest="not-a-digest"),
            "lowercase sha256 digest",
        ),
        (
            lambda: replace(_deployment(), hosts=()),
            "bounded non-empty ModelWorkerHost tuple",
        ),
        (
            lambda: replace(
                _deployment(),
                hosts=(_deployment().hosts[0], _deployment().hosts[0]),
            ),
            "host identifiers must be unique",
        ),
        (
            lambda: replace(_deployment(), owned_resource_ids=()),
            "owned_resource_ids must be a bounded non-empty tuple",
        ),
        (
            lambda: replace(
                _deployment(),
                owned_resource_ids=("resource", "resource"),
            ),
            "owned resource identifiers must be unique",
        ),
        (
            lambda: replace(_policy(), launch_plan=cast(RunnerLaunchPlan, object())),
            "launch_plan must be RunnerLaunchPlan",
        ),
        (
            lambda: replace(_policy(), minimum_memory_mib=0),
            "minimum_memory_mib must be a positive integer",
        ),
        (
            lambda: replace(_policy(), runtime_probe=()),
            "runtime_probe must be a bounded non-empty tuple",
        ),
        (
            lambda: replace(
                _policy(),
                expected_runtime_version_digest="sha256:nope",
            ),
            "must be a sha256 version digest",
        ),
        (
            lambda: replace(_policy(), configure_timeout_seconds=0),
            "configure_timeout_seconds is outside",
        ),
    ],
)
def test_lifecycle_contracts_reject_unsafe_or_incomplete_values(
    factory: object,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        factory()  # type: ignore[operator]


def test_trace_and_manager_boundaries_fail_closed() -> None:
    with pytest.raises(ValueError, match="event must"):
        ModelWorkerLifecycleTrace("ready", _DIGEST_A, 1)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="worker_count"):
        ModelWorkerLifecycleTrace(
            ModelWorkerLifecycleEvent.READY,
            _DIGEST_A,
            -1,
        )
    with pytest.raises(ValueError, match="trace_sink"):
        ModelWorkerLifecycleManager(
            infrastructure=_Infrastructure([]),
            configuration=_Configuration([]),
            dispatcher=_Dispatcher([]),
            trace_sink=None,  # type: ignore[arg-type]
        )


def test_provision_failure_has_no_cleanup_target() -> None:
    harness = _Harness()

    with pytest.raises(ModelWorkerLifecycleError) as caught:
        harness.manager(fail_provision=True).acquire(_policy())

    assert caught.value.phase == "provision"
    assert caught.value.cleanup_failed is False
    assert harness.calls == ["provision"]


@pytest.mark.parametrize(
    "endpoints",
    [
        (),
        (_endpoint(), _endpoint()),
        (replace(_endpoint(), host_id="unknown"),),
        (replace(_endpoint(), endpoint_url="http://10.42.1.99:8000/v1"),),
    ],
)
def test_endpoint_admission_requires_exact_provisioned_host_coverage(
    endpoints: tuple[ModelWorkerEndpoint, ...],
) -> None:
    with pytest.raises(ValueError):
        ModelWorkerLifecycleManager._validated_endpoints(_deployment(), endpoints)
