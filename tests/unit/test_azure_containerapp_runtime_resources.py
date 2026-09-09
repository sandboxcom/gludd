"""Concrete Azure clients and direct Terraform runtimes for managed bootstrap."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from general_ludd.azure.accelerator_credentials import AzureAcceleratorCredentials
from general_ludd.infra import azure_containerapp_runtime_resources as resources_module
from general_ludd.infra.azure_containerapp_environment_lifecycle import (
    AzureEnvironmentLifecyclePolicy,
    AzureEnvironmentProfile,
)
from general_ludd.infra.azure_containerapp_gpu import ModelServingRequirement
from general_ludd.infra.azure_containerapp_live_proof import (
    LIVE_PROOF_ACKNOWLEDGEMENT,
    AzureContainerAppLiveProofPolicy,
)
from general_ludd.self_improve.model_candidates import BackendCallBudget

SUBSCRIPTION = "12345678-1234-1234-1234-123456789abc"


def _credentials() -> AzureAcceleratorCredentials:
    return AzureAcceleratorCredentials(
        client_id="33333333-4444-4555-8666-777777777777",
        client_secret="never-render-this-secret",
        subscription_id=SUBSCRIPTION,
        tenant_id="22222222-3333-4444-8555-666666666666",
    )


def _policy() -> AzureContainerAppLiveProofPolicy:
    return AzureContainerAppLiveProofPolicy(
        subscription_id=SUBSCRIPTION,
        resource_group="gludd-models-eastus",
        environment_name="gludd-gpu-environment",
        workload_profile_name="gpu-t4",
        workload_profile_type="Consumption-GPU-NC8as-T4",
        location="eastus",
        app_name="gludd-vllm-managed-abc123",
        allowed_cidr="203.0.113.7/32",
        container_image="vllm/vllm-openai@sha256:" + "a" * 64,
        model_name="Qwen/Qwen2.5-0.5B-Instruct",
        model_revision="b" * 40,
        max_cost_usd=5.0,
        ttl_minutes=60,
        call_budget=BackendCallBudget(1, 128, 64, 192, 500_000, 30.0),
        estimated_request_cost_microusd=250_000,
        live=True,
        acknowledgement=LIVE_PROOF_ACKNOWLEDGEMENT,
        min_replicas=1,
    )


def _requirement() -> ModelServingRequirement:
    return ModelServingRequirement(
        model_id="Qwen/Qwen2.5-0.5B-Instruct",
        revision="b" * 40,
        parameter_count=494_032_768,
        weight_bits=16,
        kv_cache_mib=2_048,
        runtime_overhead_mib=3_072,
    )


def _environment_policy(
    policy: AzureContainerAppLiveProofPolicy,
) -> AzureEnvironmentLifecyclePolicy:
    return AzureEnvironmentLifecyclePolicy(
        subscription_id=policy.subscription_id,
        resource_group=policy.resource_group,
        environment_name=policy.environment_name,
        location=policy.location,
        profiles=(
            AzureEnvironmentProfile(
                policy.workload_profile_name,
                policy.workload_profile_type,
            ),
        ),
        owner_digest="c" * 64,
        plan_digest=policy.operation_digest,
        expires_at_utc="2026-09-07T18:00:00Z",
    )


def test_resources_build_polling_runtimes_and_release_credentials_last(
    tmp_path: Path,
) -> None:
    policy = _policy()
    lifecycle: list[str] = []
    runtime_arguments: dict[str, object] = {}
    environment_runtime_arguments: dict[str, object] = {}
    app_documents: list[object | None] = [
        {"properties": {"provisioningState": "Updating"}},
        {
            "properties": {
                "provisioningState": "Succeeded",
                "latestReadyRevisionName": f"{policy.app_name}--0000007",
            }
        },
        {
            "properties": {
                "provisioningState": "Succeeded",
                "latestReadyRevisionName": f"{policy.app_name}--0000007",
            }
        },
        None,
    ]
    revision_documents = [
        {
            "name": f"{policy.app_name}--0000007",
            "properties": {
                "active": True,
                "replicas": 0,
                "healthState": "None",
                "provisioningState": "Provisioning",
                "runningState": "Processing",
            },
        },
        {
            "name": f"{policy.app_name}--0000007",
            "properties": {
                "active": True,
                "replicas": 1,
                "healthState": "Healthy",
                "provisioningState": "Provisioned",
                "runningState": "Running",
            },
        },
    ]
    environment_documents: list[object | None] = [
        None,
        {"properties": {"provisioningState": "Succeeded"}},
        None,
    ]
    inventories = [(policy.expected_resource_id,), ()]
    clock = iter((0.0,) * 8)
    progress: list[str] = []

    class Credential:
        def get_token(self, *_scopes: str) -> object:
            return SimpleNamespace(token="unit-token")

        def close(self) -> None:
            lifecycle.append("credential.close")

    class EnvironmentTransport:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def close(self) -> None:
            lifecycle.append("environment.close")

    class AppTransport:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def get_json(self, token: str) -> object | None:
            assert token == "unit-token"
            return app_documents.pop(0)

        def get_revision_json(self, token: str, revision_name: str) -> object:
            assert token == "unit-token"
            assert revision_name == f"{policy.app_name}--0000007"
            return revision_documents.pop(0)

        def close(self) -> None:
            lifecycle.append("app.close")

    class LifecycleTransport:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def get_environment(self, token: str) -> object | None:
            assert token == "unit-token"
            return environment_documents.pop(0)

        def list_environment_app_ids(self, token: str) -> tuple[str, ...]:
            assert token == "unit-token"
            return inventories.pop(0)

        def close(self) -> None:
            lifecycle.append("lifecycle.close")

    class Runtime:
        def __init__(self, **kwargs: object) -> None:
            runtime_arguments.update(kwargs)

    class EnvironmentRuntime:
        def __init__(self, **kwargs: object) -> None:
            environment_runtime_arguments.update(kwargs)

    resources = resources_module.build_azure_containerapp_runtime_resources(
        credentials=_credentials(),
        policy=policy,
        requirement=_requirement(),
        work_root=tmp_path / "apps",
        environment_work_root=tmp_path / "environments",
        credential_release=lambda: lifecycle.append("lease.release"),
        monotonic=lambda: next(clock),
        sleep=lambda _seconds: lifecycle.append("heartbeat.sleep"),
        progress_sink=progress.append,
        _credential_factory=lambda _value: Credential(),
        _environment_transport_factory=EnvironmentTransport,
        _lifecycle_transport_factory=LifecycleTransport,
        _app_transport_factory=AppTransport,
        _app_runtime_factory=Runtime,
        _environment_runtime_factory=EnvironmentRuntime,
    )
    environment_policy = _environment_policy(policy)

    assert runtime_arguments["read_app"](policy, False) == {
        "properties": {
            "provisioningState": "Succeeded",
            "latestReadyRevisionName": f"{policy.app_name}--0000007",
        }
    }
    assert runtime_arguments["read_app"](policy, True) is None
    assert environment_runtime_arguments["read_environment"](
        environment_policy,
        False,
    ) is None
    assert environment_runtime_arguments["read_environment"](
        environment_policy,
        False,
    ) == {"properties": {"provisioningState": "Succeeded"}}
    assert environment_runtime_arguments["list_environment_apps"](
        environment_policy
    ) == ()
    resources.close()
    resources.close()

    assert lifecycle[-5:] == [
        "app.close",
        "lifecycle.close",
        "environment.close",
        "credential.close",
        "lease.release",
    ]
    assert progress[:3] == [
        "azure_containerapp_poll phase=readiness state=heartbeat",
        (
            "azure_containerapp_revision_poll phase=readiness state=heartbeat "
            "provisioning_state=Provisioning health_state=None "
            "running_state=Processing replicas=0"
        ),
        "azure_containerapp_poll phase=readiness state=heartbeat",
    ]


def test_default_resources_build_one_shared_official_sdk_reader(
    tmp_path: Path,
) -> None:
    policy = _policy()
    calls: list[tuple[str, object]] = []

    class Client:
        def close(self) -> None:
            calls.append(("client.close", None))

    class Credential:
        def close(self) -> None:
            calls.append(("credential.close", None))

    class View:
        def close(self) -> None:
            calls.append(("view.close", None))

    client = Client()
    views = SimpleNamespace(preflight=View(), lifecycle=View(), app=View())

    resources = resources_module.build_azure_containerapp_runtime_resources(
        credentials=_credentials(),
        policy=policy,
        requirement=_requirement(),
        work_root=tmp_path / "apps",
        environment_work_root=tmp_path / "environments",
        _credential_factory=lambda _value: Credential(),
        _sdk_client_factory=lambda credential, subscription_id: (
            calls.append(("sdk.client", (credential, subscription_id))) or client
        ),
        _sdk_transports_factory=lambda **kwargs: (
            calls.append(("sdk.views", kwargs)) or views
        ),
        _app_runtime_factory=lambda **_kwargs: SimpleNamespace(),
        _environment_runtime_factory=lambda **_kwargs: SimpleNamespace(),
    )

    assert calls[0][0] == "sdk.client"
    assert calls[1] == (
        "sdk.views",
        {"client": client, "policy": policy},
    )
    resources.close()


def test_resource_construction_failure_closes_partial_clients_and_releases_lease(
    tmp_path: Path,
) -> None:
    lifecycle: list[str] = []

    class Client:
        def __init__(self, name: str) -> None:
            self.name = name

        def close(self) -> None:
            lifecycle.append(self.name)

    with pytest.raises(RuntimeError, match="private failure"):
        resources_module.build_azure_containerapp_runtime_resources(
            credentials=_credentials(),
            policy=_policy(),
            requirement=_requirement(),
            work_root=tmp_path / "apps",
            environment_work_root=tmp_path / "environments",
            credential_release=lambda: lifecycle.append("lease"),
            _credential_factory=lambda _value: Client("credential"),
            _environment_transport_factory=lambda **_kwargs: Client("environment"),
            _lifecycle_transport_factory=lambda **_kwargs: Client("lifecycle"),
            _app_transport_factory=lambda **_kwargs: Client("app"),
            _app_runtime_factory=lambda **_kwargs: (
                (_ for _ in ()).throw(RuntimeError("private failure"))
            ),
        )

    assert lifecycle == ["app", "lifecycle", "environment", "credential", "lease"]
