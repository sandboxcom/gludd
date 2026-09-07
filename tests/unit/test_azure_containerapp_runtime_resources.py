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
        teardown_when_idle=True,
    )


def test_resources_build_polling_runtimes_and_release_credentials_last(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
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
        None,
    ]
    environment_documents: list[object | None] = [
        None,
        {"properties": {"provisioningState": "Succeeded"}},
        None,
    ]
    inventories = [(policy.expected_resource_id,), ()]
    clock = iter((0.0,) * 8)

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

    monkeypatch.setattr(resources_module, "_credential_client", lambda _value: Credential())
    monkeypatch.setattr(resources_module, "HttpxARMJSONTransport", EnvironmentTransport)
    monkeypatch.setattr(resources_module, "HttpxContainerAppARMTransport", AppTransport)
    monkeypatch.setattr(
        resources_module,
        "HttpxContainerAppEnvironmentLifecycleTransport",
        LifecycleTransport,
    )
    monkeypatch.setattr(resources_module, "AzureContainerAppTerraformRuntime", Runtime)
    monkeypatch.setattr(
        resources_module,
        "AzureContainerAppEnvironmentTerraformRuntime",
        EnvironmentRuntime,
    )

    resources = resources_module.build_azure_containerapp_runtime_resources(
        credentials=_credentials(),
        policy=policy,
        requirement=_requirement(),
        work_root=tmp_path / "apps",
        environment_work_root=tmp_path / "environments",
        credential_release=lambda: lifecycle.append("lease.release"),
        monotonic=lambda: next(clock),
        sleep=lambda _seconds: lifecycle.append("heartbeat.sleep"),
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


def test_resource_construction_failure_closes_partial_clients_and_releases_lease(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lifecycle: list[str] = []

    class Client:
        def __init__(self, name: str) -> None:
            self.name = name

        def close(self) -> None:
            lifecycle.append(self.name)

    monkeypatch.setattr(resources_module, "_credential_client", lambda _value: Client("credential"))
    monkeypatch.setattr(
        resources_module,
        "HttpxARMJSONTransport",
        lambda **_kwargs: Client("environment"),
    )
    monkeypatch.setattr(
        resources_module,
        "HttpxContainerAppEnvironmentLifecycleTransport",
        lambda **_kwargs: Client("lifecycle"),
    )
    monkeypatch.setattr(
        resources_module,
        "HttpxContainerAppARMTransport",
        lambda **_kwargs: Client("app"),
    )
    monkeypatch.setattr(
        resources_module,
        "AzureContainerAppTerraformRuntime",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("private failure")),
    )

    with pytest.raises(RuntimeError, match="private failure"):
        resources_module.build_azure_containerapp_runtime_resources(
            credentials=_credentials(),
            policy=_policy(),
            requirement=_requirement(),
            work_root=tmp_path / "apps",
            environment_work_root=tmp_path / "environments",
            credential_release=lambda: lifecycle.append("lease"),
        )

    assert lifecycle == ["app", "lifecycle", "environment", "credential", "lease"]
