"""Concrete Azure clients and direct Terraform runtimes for managed bootstrap."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import cast

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
from general_ludd.infra.azure_containerapp_sdk import AzureContainerAppsSDKReadError
from general_ludd.self_improve.model_candidates import (
    BackendCallBudget,
    BackendFailure,
    BackendInfrastructureError,
)

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


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("healthState", "Unhealthy"),
        ("provisioningState", "Failed"),
        ("provisioningState", "Deprovisioned"),
        ("runningState", "Stopped"),
        ("runningState", "Degraded"),
        ("runningState", "Failed"),
    ],
)
def test_revision_terminal_states_are_locally_typed(field: str, value: str) -> None:
    """A pending revision can stop its own supervisor without provider text."""
    properties: dict[str, object] = {
        "active": True,
        "replicas": 0,
        "healthState": "None",
        "provisioningState": "Provisioning",
        "runningState": "Processing",
    }
    properties[field] = value

    assert resources_module._revision_state({"properties": properties}).terminal is True


def test_healthy_revision_tolerates_azure_running_state_lag() -> None:
    """Azure's Healthy state is authoritative when runningState stays Unknown."""
    state = resources_module._revision_state(
        {
            "properties": {
                "active": True,
                "replicas": 1,
                "healthState": "Healthy",
                "provisioningState": "Provisioned",
                "runningState": "Unknown",
            }
        }
    )

    assert state.ready(1) is True


def test_app_progress_state_rejects_provider_controlled_text() -> None:
    """Only an allowlisted state and ready-revision boolean may reach events."""
    secret = "private-provider-response-never-log"

    assert resources_module._app_provisioning_state(
        {
            "properties": {
                "provisioningState": secret,
                "latestReadyRevisionName": 7,
            }
        }
    ) == ("Unknown", False)


@pytest.mark.parametrize(
    ("revision", "expected_failure"),
    [
        (None, BackendFailure.TIMEOUT),
        (
            {
                "properties": {
                    "active": True,
                    "replicas": 0,
                    "healthState": "Unhealthy",
                    "provisioningState": "Failed",
                    "runningState": "Failed",
                }
            },
            BackendFailure.UNAVAILABLE,
        ),
    ],
)
def test_app_readiness_supervisor_returns_typed_failure(
    tmp_path: Path,
    revision: object | None,
    expected_failure: BackendFailure,
) -> None:
    """A stalled or terminal revision must fail without provider-controlled text."""
    policy = _policy()
    runtime_arguments: dict[str, object] = {}
    ticks = iter((0.0, 901.0))

    class Credential:
        def get_token(self, *_scopes: str) -> object:
            return SimpleNamespace(token="unit-token")

        def close(self) -> None:
            return None

    class Transport:
        def close(self) -> None:
            return None

    class AppTransport(Transport):
        def get_json(self, token: str) -> object:
            assert token == "unit-token"
            return {"properties": {"provisioningState": "Succeeded"}}

        def get_active_revision_json(self, token: str) -> object | None:
            assert token == "unit-token"
            return revision

    resources = resources_module.build_azure_containerapp_runtime_resources(
        credentials=_credentials(),
        policy=policy,
        requirement=_requirement(),
        work_root=tmp_path / "apps",
        environment_work_root=tmp_path / "environments",
        monotonic=lambda: next(ticks),
        sleep=lambda _seconds: None,
        _credential_factory=lambda _value: Credential(),
        _environment_transport_factory=lambda **_kwargs: Transport(),
        _lifecycle_transport_factory=lambda **_kwargs: Transport(),
        _app_transport_factory=lambda **_kwargs: AppTransport(),
        _app_runtime_factory=lambda **kwargs: (
            runtime_arguments.update(kwargs) or SimpleNamespace()
        ),
        _environment_runtime_factory=lambda **_kwargs: SimpleNamespace(),
    )
    read_app = cast(
        Callable[[AzureContainerAppLiveProofPolicy, bool], object],
        runtime_arguments["read_app"],
    )

    with pytest.raises(BackendInfrastructureError) as captured:
        read_app(policy, False)

    assert captured.value.failure is expected_failure
    resources.close()


def test_replica_diagnostics_stop_terminal_startup_without_leaking_details(
    tmp_path: Path,
) -> None:
    """A safe replica reason must stop doomed startup before its deadline."""
    policy = _policy()
    revision_name = f"{policy.app_name}--0000007"
    runtime_arguments: dict[str, object] = {}
    progress: list[str] = []

    class Credential:
        def get_token(self, *_scopes: str) -> object:
            return SimpleNamespace(token="unit-token")

        def close(self) -> None:
            return None

    class Transport:
        def close(self) -> None:
            return None

    class AppTransport(Transport):
        def get_json(self, _token: str) -> object:
            return {"properties": {"provisioningState": "Succeeded"}}

        def get_active_revision_json(self, _token: str) -> object:
            return {
                "name": revision_name,
                "properties": {
                    "active": True,
                    "replicas": 1,
                    "healthState": "None",
                    "provisioningState": "Provisioned",
                    "runningState": "Unknown",
                },
            }

        def get_replica_status_json(
            self,
            _token: str,
            observed_revision_name: str,
        ) -> object:
            assert observed_revision_name == revision_name
            return {
                "replicaCount": 1,
                "readyContainerCount": 0,
                "startedContainerCount": 0,
                "restartCount": 3,
                "replicaRunningStates": ["NotRunning"],
                "containerRunningStates": ["Waiting"],
                "reasonClasses": ["image_pull_failure"],
                "providerDetail": "private-provider-text",
            }

    resources = resources_module.build_azure_containerapp_runtime_resources(
        credentials=_credentials(),
        policy=policy,
        requirement=_requirement(),
        work_root=tmp_path / "apps",
        environment_work_root=tmp_path / "environments",
        monotonic=lambda: 0.0,
        sleep=lambda _seconds: pytest.fail("terminal replica must not sleep"),
        progress_sink=progress.append,
        _credential_factory=lambda _value: Credential(),
        _environment_transport_factory=lambda **_kwargs: Transport(),
        _lifecycle_transport_factory=lambda **_kwargs: Transport(),
        _app_transport_factory=lambda **_kwargs: AppTransport(),
        _app_runtime_factory=lambda **kwargs: (
            runtime_arguments.update(kwargs) or SimpleNamespace()
        ),
        _environment_runtime_factory=lambda **_kwargs: SimpleNamespace(),
    )
    read_app = cast(
        Callable[[AzureContainerAppLiveProofPolicy, bool], object],
        runtime_arguments["read_app"],
    )

    with pytest.raises(BackendInfrastructureError) as captured:
        read_app(policy, False)

    assert captured.value.failure is BackendFailure.UNAVAILABLE
    assert progress == [
        "azure_containerapp_replica_poll phase=readiness state=terminal "
        "replicas=1 ready_containers=0 started_containers=0 restarts=3 "
        "replica_state=NotRunning container_state=Waiting "
        "reason=image_pull_failure"
    ]
    assert "private-provider-text" not in repr(progress)
    resources.close()


def test_replica_diagnostics_confirm_ready_when_revision_running_state_lags(
    tmp_path: Path,
) -> None:
    """Healthy ready containers may resolve Azure's stale revision status."""
    policy = _policy()
    revision_name = f"{policy.app_name}--0000007"
    runtime_arguments: dict[str, object] = {}
    progress: list[str] = []

    class Credential:
        def get_token(self, *_scopes: str) -> object:
            return SimpleNamespace(token="unit-token")

        def close(self) -> None:
            return None

    class Transport:
        def close(self) -> None:
            return None

    class AppTransport(Transport):
        def get_json(self, _token: str) -> object:
            return {"properties": {"provisioningState": "Succeeded"}}

        def get_active_revision_json(self, _token: str) -> object:
            return {
                "name": revision_name,
                "properties": {
                    "active": True,
                    "replicas": 1,
                    "healthState": "Healthy",
                    "provisioningState": "Provisioned",
                    "runningState": "Unknown",
                },
            }

        def get_replica_status_json(
            self,
            _token: str,
            observed_revision_name: str,
        ) -> object:
            assert observed_revision_name == revision_name
            return {
                "replicaCount": 1,
                "readyContainerCount": 1,
                "startedContainerCount": 1,
                "restartCount": 0,
                "replicaRunningStates": ["Running"],
                "containerRunningStates": ["Running"],
                "reasonClasses": [],
            }

    resources = resources_module.build_azure_containerapp_runtime_resources(
        credentials=_credentials(),
        policy=policy,
        requirement=_requirement(),
        work_root=tmp_path / "apps",
        environment_work_root=tmp_path / "environments",
        monotonic=lambda: 0.0,
        sleep=lambda _seconds: pytest.fail("ready replica must not sleep"),
        progress_sink=progress.append,
        _credential_factory=lambda _value: Credential(),
        _environment_transport_factory=lambda **_kwargs: Transport(),
        _lifecycle_transport_factory=lambda **_kwargs: Transport(),
        _app_transport_factory=lambda **_kwargs: AppTransport(),
        _app_runtime_factory=lambda **kwargs: (
            runtime_arguments.update(kwargs) or SimpleNamespace()
        ),
        _environment_runtime_factory=lambda **_kwargs: SimpleNamespace(),
    )
    read_app = cast(
        Callable[[AzureContainerAppLiveProofPolicy, bool], object],
        runtime_arguments["read_app"],
    )

    document = cast(dict[str, object], read_app(policy, False))

    assert document == {
        "properties": {
            "provisioningState": "Succeeded",
            "latestReadyRevisionName": revision_name,
        }
    }
    assert progress == [
        "azure_containerapp_replica_poll phase=readiness state=ready "
        "replicas=1 ready_containers=1 started_containers=1 restarts=0 "
        "replica_state=Running container_state=Running reason=none"
    ]
    resources.close()


def test_replica_diagnostic_read_failure_is_supplementary_and_not_retried(
    tmp_path: Path,
) -> None:
    """A missing optional replica read must not require wider Azure privilege."""
    policy = _policy()
    revision_name = f"{policy.app_name}--0000007"
    runtime_arguments: dict[str, object] = {}
    progress: list[str] = []
    revision_documents = [
        {
            "name": revision_name,
            "properties": {
                "active": True,
                "replicas": 1,
                "healthState": "None",
                "provisioningState": "Provisioned",
                "runningState": "Unknown",
            },
        },
        {
            "name": revision_name,
            "properties": {
                "active": True,
                "replicas": 1,
                "healthState": "Healthy",
                "provisioningState": "Provisioned",
                "runningState": "Unknown",
            },
        },
    ]
    replica_calls = 0

    class Credential:
        def get_token(self, *_scopes: str) -> object:
            return SimpleNamespace(token="unit-token")

        def close(self) -> None:
            return None

    class Transport:
        def close(self) -> None:
            return None

    class AppTransport(Transport):
        def get_json(self, _token: str) -> object:
            return {"properties": {"provisioningState": "Succeeded"}}

        def get_active_revision_json(self, _token: str) -> object:
            return revision_documents.pop(0)

        def get_replica_status_json(
            self,
            _token: str,
            _revision_name: str,
        ) -> object:
            nonlocal replica_calls
            replica_calls += 1
            raise AzureContainerAppsSDKReadError("censored", status_code=403)

    ticks = iter((0.0, 0.0))
    resources = resources_module.build_azure_containerapp_runtime_resources(
        credentials=_credentials(),
        policy=policy,
        requirement=_requirement(),
        work_root=tmp_path / "apps",
        environment_work_root=tmp_path / "environments",
        monotonic=lambda: next(ticks),
        sleep=lambda _seconds: None,
        progress_sink=progress.append,
        _credential_factory=lambda _value: Credential(),
        _environment_transport_factory=lambda **_kwargs: Transport(),
        _lifecycle_transport_factory=lambda **_kwargs: Transport(),
        _app_transport_factory=lambda **_kwargs: AppTransport(),
        _app_runtime_factory=lambda **kwargs: (
            runtime_arguments.update(kwargs) or SimpleNamespace()
        ),
        _environment_runtime_factory=lambda **_kwargs: SimpleNamespace(),
    )
    read_app = cast(
        Callable[[AzureContainerAppLiveProofPolicy, bool], object],
        runtime_arguments["read_app"],
    )

    document = cast(dict[str, object], read_app(policy, False))

    assert replica_calls == 1
    assert document["properties"] == {
        "provisioningState": "Succeeded",
        "latestReadyRevisionName": revision_name,
    }
    assert progress[0] == (
        "azure_containerapp_replica_poll phase=readiness "
        "state=supplementary_unavailable reason=sdk_read_failed"
    )
    resources.close()


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
    pending_revision_documents = [
        {
            "name": f"{policy.app_name}--0000007",
            "properties": {
                "active": True,
                "replicas": 0,
                "healthState": "None",
                "provisioningState": "Provisioning",
                "runningState": "Processing",
            },
        }
    ]
    revision_documents = [
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

        def get_active_revision_json(self, token: str) -> object | None:
            assert token == "unit-token"
            return pending_revision_documents.pop(0)

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
        (
            "azure_containerapp_revision_poll phase=readiness state=heartbeat "
            "provisioning_state=Provisioning health_state=None "
            "running_state=Processing replicas=0"
        ),
        (
            "azure_containerapp_poll phase=readiness state=heartbeat "
            "provisioning_state=Updating latest_ready_revision=false"
        ),
        (
            "azure_containerapp_poll phase=absence state=heartbeat "
            "provisioning_state=Succeeded latest_ready_revision=true"
        ),
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


def test_partial_legacy_transport_failure_closes_every_acquired_owner(
    tmp_path: Path,
) -> None:
    """Construction rollback closes completed transports before identity release."""
    lifecycle: list[str] = []

    class Client:
        def __init__(self, name: str) -> None:
            self.name = name

        def close(self) -> None:
            lifecycle.append(self.name)

    def fail_lifecycle(**_kwargs: object) -> object:
        raise RuntimeError("private provider failure")

    with pytest.raises(RuntimeError, match="private provider failure"):
        resources_module.build_azure_containerapp_runtime_resources(
            credentials=_credentials(),
            policy=_policy(),
            requirement=_requirement(),
            work_root=tmp_path / "apps",
            environment_work_root=tmp_path / "environments",
            credential_release=lambda: lifecycle.append("lease"),
            _credential_factory=lambda _value: Client("credential"),
            _environment_transport_factory=lambda **_kwargs: Client("environment"),
            _lifecycle_transport_factory=fail_lifecycle,
            _app_transport_factory=lambda **_kwargs: Client("app"),
        )

    assert lifecycle == ["environment", "credential", "lease"]


def test_incomplete_legacy_transport_set_fails_before_transport_construction(
    tmp_path: Path,
) -> None:
    """A mixed legacy/SDK configuration cannot acquire an ambiguous owner set."""
    lifecycle: list[str] = []

    class Credential:
        def close(self) -> None:
            lifecycle.append("credential")

    with pytest.raises(ValueError, match="all legacy Azure read transports"):
        resources_module.build_azure_containerapp_runtime_resources(
            credentials=_credentials(),
            policy=_policy(),
            requirement=_requirement(),
            work_root=tmp_path / "apps",
            environment_work_root=tmp_path / "environments",
            credential_release=lambda: lifecycle.append("lease"),
            _credential_factory=lambda _value: Credential(),
            _environment_transport_factory=lambda **_kwargs: object(),
        )

    assert lifecycle == ["credential", "lease"]


def test_sdk_view_construction_failure_closes_unowned_client_first(
    tmp_path: Path,
) -> None:
    """The official SDK client cannot leak when its bounded views fail to build."""
    lifecycle: list[str] = []

    class Client:
        def __init__(self, name: str) -> None:
            self.name = name

        def close(self) -> None:
            lifecycle.append(self.name)

    def fail_views(**_kwargs: object) -> object:
        raise RuntimeError("private view failure")

    with pytest.raises(RuntimeError, match="private view failure"):
        resources_module.build_azure_containerapp_runtime_resources(
            credentials=_credentials(),
            policy=_policy(),
            requirement=_requirement(),
            work_root=tmp_path / "apps",
            environment_work_root=tmp_path / "environments",
            credential_release=lambda: lifecycle.append("lease"),
            _credential_factory=lambda _value: Client("credential"),
            _sdk_client_factory=lambda *_args: Client("sdk"),
            _sdk_transports_factory=fail_views,
        )

    assert lifecycle == ["sdk", "credential", "lease"]


def test_cleanup_attempts_every_owner_when_clients_and_release_fail() -> None:
    """One close failure never prevents later resources from being released."""
    lifecycle: list[str] = []

    class Client:
        def __init__(self, name: str, *, fail: bool = False) -> None:
            self.name = name
            self.fail = fail

        def close(self) -> None:
            lifecycle.append(self.name)
            if self.fail:
                raise RuntimeError("private cleanup failure")

    def release() -> None:
        lifecycle.append("lease")
        raise RuntimeError("private lease failure")

    resources = resources_module.AzureContainerAppRuntimeResources(
        runtime=cast(object, SimpleNamespace()),
        environment_runtime=cast(object, SimpleNamespace()),
        credential=Client("credential"),
        environment_transport=Client("environment"),
        lifecycle_transport=Client("lifecycle", fail=True),
        app_transport=Client("app", fail=True),
        credential_release=release,
    )

    with pytest.raises(RuntimeError, match="Azure live resource cleanup failed"):
        resources.close()

    assert lifecycle == ["app", "lifecycle", "environment", "credential", "lease"]
