"""Tests for the remote Ansible model-worker configuration adapter."""

from __future__ import annotations

import stat
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import pytest
import yaml

from general_ludd.hardware.accelerator_topology import DistributionMode
from general_ludd.hardware.model_runner_launch_contracts import RunnerLaunchPlan
from general_ludd.infra.ansible_model_worker_runtime import (
    AnsibleModelWorkerConfigurationRuntime,
    ModelWorkerConfigurationError,
)
from general_ludd.infra.model_worker_lifecycle import (
    ModelWorkerEndpoint,
    ModelWorkerHost,
    ModelWorkerLifecyclePolicy,
    ProvisionedModelWorkerPool,
)

_RELEASE = "a" * 64
_TOPOLOGY = "b" * 64
_INVENTORY = "c" * 64
_DRIVER = f"sha256:{'d' * 64}"
_RUNTIME = f"sha256:{'e' * 64}"


def _policy() -> ModelWorkerLifecyclePolicy:
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
            replica_count=1,
            devices_per_replica=2,
        ),
        release_id=_RELEASE,
        topology_digest=_TOPOLOGY,
        backend="cuda",
        minimum_memory_mib=48_000,
        required_interconnect="nvlink",
        runtime_probe=("/opt/gludd/bin/vllm", "--version"),
        expected_runtime_version_digest=_RUNTIME,
        configure_timeout_seconds=600,
    )


def _deployment(*, host_id: str = "azure-vm-01") -> ProvisionedModelWorkerPool:
    return ProvisionedModelWorkerPool(
        deployment_id="deployment-01",
        hosts=(
            ModelWorkerHost(
                host_id=host_id,
                address="10.42.1.4",
                ansible_user="gludd",
                ssh_private_key_path="/run/gludd/keys/worker",
                endpoint_url="http://10.42.1.4:8000/v1",
            ),
        ),
        owned_resource_ids=("/subscriptions/sub/resourceGroups/rg/providers/X/y",),
    )


def _candidate(**overrides: object) -> dict[str, object]:
    candidate: dict[str, object] = {
        "ready": True,
        "release_id": _RELEASE,
        "runner_id": "vllm",
        "adapter_id": "vllm-openai-v1",
        "source_revision": "sha256:runner",
        "topology_digest": _TOPOLOGY,
        "observed_inventory_digest": _INVENTORY,
        "driver_version_digest": _DRIVER,
        "runtime_version_digest": _RUNTIME,
        "backend": "cuda",
        "interconnect": "nvlink",
        "devices_per_replica": 2,
        "service_name": "gludd-model-worker-aaaaaaaaaaaa.service",
        "health_url": "http://127.0.0.1:8000/healthz",
        "attestation_path": (
            "/var/lib/gludd/model-workers/attestations/"
            "gludd-model-worker-aaaaaaaaaaaa.json"
        ),
    }
    candidate.update(overrides)
    return candidate


def _success_result(candidate: dict[str, object] | None = None) -> dict[str, object]:
    events: list[dict[str, object]] = []
    if candidate is not None:
        events.append(
            {
                "event": "runner_on_ok",
                "host": "gludd_worker_0000",
                "result": {
                    "ansible_facts": {"gludd_model_worker_candidate": candidate}
                },
            }
        )
    return {"status": "successful", "rc": 0, "events": events}


@dataclass
class _Runner:
    result: object
    calls: list[dict[str, Any]] = field(default_factory=list)
    inventory_documents: list[dict[str, object]] = field(default_factory=list)
    inventory_modes: list[int] = field(default_factory=list)
    inventory_paths: list[Path] = field(default_factory=list)

    def run_playbook(self, playbook_name: str, **kwargs: Any) -> object:
        path = Path(kwargs["inventory"][0])
        self.inventory_paths.append(path)
        self.inventory_modes.append(stat.S_IMODE(path.stat().st_mode))
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        self.inventory_documents.append(document)
        self.calls.append({"playbook_name": playbook_name, **kwargs})
        return self.result


def _endpoint(**overrides: object) -> ModelWorkerEndpoint:
    values: dict[str, object] = {
        "host_id": "azure-vm-01",
        "endpoint_url": "http://10.42.1.4:8000/v1",
        "service_name": "gludd-model-worker-aaaaaaaaaaaa.service",
        "attestation_digest": "f" * 64,
    }
    values.update(overrides)
    return ModelWorkerEndpoint(**values)  # type: ignore[arg-type]


def test_configure_uses_ephemeral_exact_inventory_and_returns_attested_endpoint() -> None:
    runner = _Runner(_success_result(_candidate()))
    runtime = AnsibleModelWorkerConfigurationRuntime(runner)

    endpoints = runtime.configure(_deployment(), _policy())

    assert len(endpoints) == 1
    endpoint = endpoints[0]
    assert endpoint.host_id == "azure-vm-01"
    assert endpoint.endpoint_url == "http://10.42.1.4:8000/v1"
    assert endpoint.service_name == "gludd-model-worker-aaaaaaaaaaaa.service"
    assert len(endpoint.attestation_digest) == 64
    call = runner.calls[0]
    assert call["playbook_name"] == "model_worker_deploy.yml"
    assert call["connection"] == "ssh"
    assert call["become"] is True
    assert call["timeout"] == 600
    assert call["extravars"]["gludd_model_worker_plan"] == _policy().launch_plan.to_dict()
    assert call["extravars"]["gludd_model_worker_attestation_backend"] == "cuda"
    assert call["extravars"]["gludd_model_worker_release_id"] == _RELEASE
    assert call["extravars"]["gludd_model_worker_expected_topology_digest"] == _TOPOLOGY
    inventory = runner.inventory_documents[0]
    hosts = inventory["all"]["children"]["gludd_model_workers"]["hosts"]
    assert hosts == {
        "gludd_worker_0000": {
            "ansible_host": "10.42.1.4",
            "ansible_ssh_private_key_file": "/run/gludd/keys/worker",
            "ansible_user": "gludd",
        }
    }
    assert runner.inventory_modes == [0o600]
    assert all(not path.exists() for path in runner.inventory_paths)


def test_configure_censors_runner_failure_details() -> None:
    runner = _Runner(
        {
            "status": "failed",
            "rc": 2,
            "error": "secret provider failure at 10.42.1.4",
            "events": [],
        }
    )

    with pytest.raises(ModelWorkerConfigurationError) as caught:
        AnsibleModelWorkerConfigurationRuntime(runner).configure(
            _deployment(),
            _policy(),
        )

    assert caught.value.phase == "configure"
    assert "secret" not in str(caught.value)
    assert "10.42.1.4" not in str(caught.value)


@pytest.mark.parametrize(
    "candidate",
    [
        None,
        _candidate(ready=False),
        _candidate(release_id="f" * 64),
        _candidate(runner_id="ollama"),
        _candidate(adapter_id="ollama-native"),
        _candidate(source_revision="sha256:other"),
        _candidate(topology_digest="f" * 64),
        _candidate(runtime_version_digest=f"sha256:{'f' * 64}"),
        _candidate(backend="rocm"),
        _candidate(interconnect="pcie"),
        _candidate(devices_per_replica=1),
        _candidate(devices_per_replica=True),
        _candidate(service_name="unowned.service"),
        _candidate(observed_inventory_digest=1),
        _candidate(observed_inventory_digest="invalid"),
        _candidate(driver_version_digest=1),
        _candidate(driver_version_digest="invalid"),
        _candidate(health_url=1),
        _candidate(health_url="http://10.42.1.4:8000/healthz"),
        _candidate(attestation_path=1),
        _candidate(attestation_path="/tmp/attestation.json"),
        _candidate(unexpected="field"),
    ],
)
def test_configure_rejects_missing_or_mismatched_attestation(
    candidate: dict[str, object] | None,
) -> None:
    runner = _Runner(_success_result(candidate))

    with pytest.raises(ModelWorkerConfigurationError) as caught:
        AnsibleModelWorkerConfigurationRuntime(runner).configure(
            _deployment(),
            _policy(),
        )

    assert caught.value.phase == "attestation"


def test_retire_passes_only_explicit_service_name_over_same_inventory() -> None:
    runner = _Runner(_success_result())
    runtime = AnsibleModelWorkerConfigurationRuntime(runner)
    endpoint = _endpoint()

    runtime.retire(_deployment(), (endpoint,))

    call = runner.calls[0]
    assert call["playbook_name"] == "model_worker_retire.yml"
    assert call["extravars"] == {
        "gludd_model_worker_retire_service": (
            "gludd-model-worker-aaaaaaaaaaaa.service"
        )
    }
    assert call["connection"] == "ssh"
    assert call["become"] is True
    assert all(not path.exists() for path in runner.inventory_paths)


def test_retire_rejects_mixed_service_generations_before_running_ansible() -> None:
    runner = _Runner(_success_result())
    runtime = AnsibleModelWorkerConfigurationRuntime(runner)
    endpoint = _endpoint()
    other = replace(
        endpoint,
        host_id="azure-vm-02",
        service_name="gludd-model-worker-bbbbbbbbbbbb.service",
    )
    deployment = replace(
        _deployment(),
        hosts=(
            _deployment().hosts[0],
            replace(
                _deployment().hosts[0],
                host_id="azure-vm-02",
                address="10.42.1.5",
                endpoint_url="http://10.42.1.5:8000/v1",
            ),
        ),
    )

    with pytest.raises(ModelWorkerConfigurationError) as caught:
        runtime.retire(deployment, (endpoint, other))

    assert caught.value.phase == "retire_contract"
    assert runner.calls == []


def test_constructor_rejects_runner_without_playbook_boundary() -> None:
    with pytest.raises(ValueError, match="runner"):
        AnsibleModelWorkerConfigurationRuntime(object())


@pytest.mark.parametrize("timeout", [True, 0, 3_601])
def test_constructor_rejects_unbounded_retire_timeout(timeout: object) -> None:
    with pytest.raises(ValueError, match="retire_timeout_seconds"):
        AnsibleModelWorkerConfigurationRuntime(
            _Runner(_success_result()),
            retire_timeout_seconds=timeout,  # type: ignore[arg-type]
        )


def test_configure_rejects_non_mapping_runner_result() -> None:
    runtime = AnsibleModelWorkerConfigurationRuntime(_Runner("not-a-result"))

    with pytest.raises(ModelWorkerConfigurationError) as caught:
        runtime.configure(_deployment(), _policy())

    assert caught.value.phase == "runner_result"


@pytest.mark.parametrize(
    "events",
    [
        None,
        [1],
        [{"event": "runner_on_failed"}],
        [{"event": "runner_on_ok", "host": "unknown", "result": {}}],
        [{"event": "runner_on_ok", "host": "gludd_worker_0000", "result": None}],
        [
            {
                "event": "runner_on_ok",
                "host": "gludd_worker_0000",
                "result": {"ansible_facts": None},
            }
        ],
        [
            {
                "event": "runner_on_ok",
                "host": "gludd_worker_0000",
                "result": {"ansible_facts": {"gludd_model_worker_candidate": []}},
            }
        ],
    ],
)
def test_configure_rejects_malformed_runner_events(events: object) -> None:
    result = {"status": "successful", "rc": 0, "events": events}
    runtime = AnsibleModelWorkerConfigurationRuntime(_Runner(result))

    with pytest.raises(ModelWorkerConfigurationError) as caught:
        runtime.configure(_deployment(), _policy())

    assert caught.value.phase == "attestation"


def test_configure_requires_exact_contract_types() -> None:
    runtime = AnsibleModelWorkerConfigurationRuntime(_Runner(_success_result()))

    with pytest.raises(ValueError, match="deployment and lifecycle policy"):
        runtime.configure(object(), _policy())  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "endpoints",
    [
        [],
        (),
        (object(),),
        (_endpoint(host_id="another-host"),),
    ],
)
def test_retire_requires_exact_endpoint_contract(endpoints: object) -> None:
    runner = _Runner(_success_result())
    runtime = AnsibleModelWorkerConfigurationRuntime(runner)

    with pytest.raises(ModelWorkerConfigurationError) as caught:
        runtime.retire(_deployment(), endpoints)  # type: ignore[arg-type]

    assert caught.value.phase == "retire_contract"
    assert runner.calls == []


def test_retire_requires_a_deployment_contract() -> None:
    runtime = AnsibleModelWorkerConfigurationRuntime(_Runner(_success_result()))

    with pytest.raises(ValueError, match="ProvisionedModelWorkerPool"):
        runtime.retire(object(), (_endpoint(),))  # type: ignore[arg-type]


def test_retire_rejects_unowned_service_name() -> None:
    runner = _Runner(_success_result())
    runtime = AnsibleModelWorkerConfigurationRuntime(runner)

    with pytest.raises(ModelWorkerConfigurationError) as caught:
        runtime.retire(_deployment(), (_endpoint(service_name="foreign.service"),))

    assert caught.value.phase == "retire_contract"
    assert runner.calls == []


def test_retire_censors_runner_failure_details() -> None:
    runner = _Runner(
        {
            "status": "failed",
            "rc": 2,
            "error": "secret provider failure at 10.42.1.4",
            "events": [],
        }
    )

    with pytest.raises(ModelWorkerConfigurationError) as caught:
        AnsibleModelWorkerConfigurationRuntime(runner).retire(
            _deployment(),
            (_endpoint(),),
        )

    assert caught.value.phase == "retire"
    assert "secret" not in str(caught.value)
    assert "10.42.1.4" not in str(caught.value)
