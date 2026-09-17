"""Official Azure SDK read and GPU-utilization attestation contracts."""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any, cast
from unittest.mock import MagicMock, patch

import pytest

from general_ludd.infra import azure_containerapp_sdk as subject
from general_ludd.infra.azure_containerapp_arm import (
    ENVIRONMENT_PREFLIGHT_API_VERSION,
)
from general_ludd.infra.azure_containerapp_gpu import ModelServingRequirement
from general_ludd.infra.azure_containerapp_gpu_canary import CUDA_STARTUP_COMMAND
from general_ludd.infra.azure_containerapp_live_proof import (
    LIVE_PROOF_ACKNOWLEDGEMENT,
    AzureContainerAppLiveProofPolicy,
)
from general_ludd.infra.azure_containerapp_preflight import (
    AzureContainerAppReadOnlyPreflight,
)
from general_ludd.infra.azure_containerapp_sdk import (
    AzureContainerAppGPUUtilizationAttestor,
    AzureContainerAppsSDKReadError,
    AzureContainerAppsSDKReadTransports,
    AzureGPUUtilizationAttestationError,
)
from general_ludd.self_improve.model_candidates import (
    AzureContainerAppCandidateIdentity,
    BackendCallBudget,
    BackendFailure,
)

SUBSCRIPTION = "11111111-2222-3333-4444-555555555555"
REVISION = "7ae557604adf67be50417f59c2c2f167def9a775"
IMAGE_DIGEST = "sha256:" + ("a" * 64)
IMAGE = f"vllm/vllm-openai@{IMAGE_DIGEST}"


def _policy() -> AzureContainerAppLiveProofPolicy:
    return AzureContainerAppLiveProofPolicy(
        subscription_id=SUBSCRIPTION,
        resource_group="gludd-models-eastus",
        environment_name="gludd-gpu-environment",
        workload_profile_name="gpu-t4",
        workload_profile_type="Consumption-GPU-NC8as-T4",
        location="eastus",
        app_name="gludd-vllm-sdk-proof",
        allowed_cidr="192.0.2.41/32",
        container_image=IMAGE,
        model_name="Qwen/Qwen2.5-0.5B-Instruct",
        model_revision=REVISION,
        max_cost_usd=5.0,
        ttl_minutes=30,
        call_budget=BackendCallBudget(1, 512, 128, 640, 500_000, 60.0),
        estimated_request_cost_microusd=100_000,
        live=True,
        acknowledgement=LIVE_PROOF_ACKNOWLEDGEMENT,
        max_replicas=2,
        http_concurrent_requests=2,
    )


def _identity(policy: AzureContainerAppLiveProofPolicy | None = None) -> AzureContainerAppCandidateIdentity:
    active = policy or _policy()
    return AzureContainerAppCandidateIdentity(
        endpoint="https://gludd-vllm-sdk-proof.kindstone.eastus.azurecontainerapps.io",
        resource_id=active.expected_resource_id,
        revision_name=f"{active.app_name}--0000007",
        image_digest=IMAGE_DIGEST,
        model_name=active.model_name,
        model_revision=active.model_revision,
        workload_profile_type=active.workload_profile_type,
    )


def test_sdk_app_normalization_preserves_exact_startup_command() -> None:
    policy = _policy()
    document = subject._container_document(
        SimpleNamespace(
            id=policy.expected_resource_id,
            name=policy.app_name,
            type="Microsoft.App/containerApps",
            location="eastus",
            properties=SimpleNamespace(
                provisioning_state="Succeeded",
                latest_ready_revision_name=f"{policy.app_name}--0000007",
                workload_profile_name=policy.workload_profile_name,
                environment_id=policy.environment_id,
                configuration=SimpleNamespace(ingress=SimpleNamespace(fqdn="example")),
                template=SimpleNamespace(
                    containers=(
                        SimpleNamespace(
                            image=IMAGE,
                            command=list(CUDA_STARTUP_COMMAND),
                            args=["--model", policy.model_name],
                        ),
                    ),
                    scale=SimpleNamespace(min_replicas=1, max_replicas=1, rules=()),
                ),
            ),
        )
    )

    assert document["properties"]["template"]["containers"][0]["command"] == list(
        CUDA_STARTUP_COMMAND
    )


def test_revision_normalization_classifies_details_without_exposing_provider_text() -> None:
    provider_detail = "Workload Profile Full. tenant-secret=must-not-escape"

    document = subject._revision_document(
        SimpleNamespace(
            name="gludd-vllm-sdk-proof--0000007",
            active=True,
            replicas=0,
            health_state="None",
            provisioning_state="Provisioned",
            running_state="Unknown",
            running_state_details=provider_detail,
        )
    )

    assert document["properties"]["reasonClasses"] == ["capacity_exhausted"]
    assert provider_detail not in repr(document)


def test_sdk_client_builders_use_official_clients_without_hidden_retries() -> None:
    azure = ModuleType("azure")
    core = ModuleType("azure.core")
    credentials = ModuleType("azure.core.credentials")
    mgmt = ModuleType("azure.mgmt")
    appcontainers = ModuleType("azure.mgmt.appcontainers")
    monitor = ModuleType("azure.mgmt.monitor")
    app_client_type = MagicMock(name="ContainerAppsAPIClient")
    monitor_client_type = MagicMock(name="MonitorManagementClient")
    appcontainers.__dict__["ContainerAppsAPIClient"] = app_client_type
    monitor.__dict__["MonitorManagementClient"] = monitor_client_type
    credentials.__dict__["TokenCredential"] = object
    credential = object()
    modules = {
        "azure": azure,
        "azure.core": core,
        "azure.core.credentials": credentials,
        "azure.mgmt": mgmt,
        "azure.mgmt.appcontainers": appcontainers,
        "azure.mgmt.monitor": monitor,
    }

    with patch.dict(sys.modules, modules):
        assert (
            subject.build_container_apps_sdk_client(credential, SUBSCRIPTION)
            is app_client_type.return_value
        )
        assert (
            subject.build_monitor_sdk_client(credential, SUBSCRIPTION)
            is monitor_client_type.return_value
        )

    for client_type in (app_client_type, monitor_client_type):
        client_type.assert_called_once_with(
            credential=credential,
            subscription_id=SUBSCRIPTION,
            retry_total=0,
        )


@pytest.mark.parametrize(
    ("module_name", "builder", "message"),
    [
        (
            "azure.mgmt.appcontainers",
            subject.build_container_apps_sdk_client,
            "Container Apps SDK dependency",
        ),
        (
            "azure.mgmt.monitor",
            subject.build_monitor_sdk_client,
            "Monitor SDK dependency",
        ),
    ],
)
def test_sdk_client_builders_fail_closed_when_dependency_is_missing(
    module_name: str,
    builder: Any,
    message: str,
) -> None:
    with (
        patch.dict(sys.modules, {module_name: None}),
        pytest.raises(RuntimeError, match=message),
    ):
        builder(object(), SUBSCRIPTION)


def test_sdk_normalization_helpers_cover_mapping_object_and_bounds() -> None:
    assert subject._member({"camelName": 3}, "snake_name", "camelName") == 3
    assert subject._member({}, "missing", default="fallback") == "fallback"
    assert subject._member(SimpleNamespace(camel_name=4), "snake_name", "camel_name") == 4
    assert subject._bounded_iterable((item for item in (1, 2)), "items") == (1, 2)

    for invalid in ("text", b"bytes", bytearray(b"bytes"), {"mapping": True}):
        with pytest.raises(AzureContainerAppsSDKReadError):
            subject._bounded_iterable(invalid, "items")
    with pytest.raises(AzureContainerAppsSDKReadError):
        subject._bounded_iterable(range(513), "items")
    with pytest.raises(AzureContainerAppsSDKReadError):
        subject._sequence("not-a-sequence-contract", "items")
    with pytest.raises(AzureContainerAppsSDKReadError):
        subject._sequence([1, 2], "items", limit=1)


def test_sdk_error_and_token_helpers_fail_closed_on_alternate_shapes() -> None:
    class ResponseFailure(RuntimeError):
        response = SimpleNamespace(status_code=503)

    original = AzureContainerAppsSDKReadError("already-censored", status_code=400)
    with pytest.raises(AzureContainerAppsSDKReadError) as raised:
        subject._sdk_read(lambda: (_ for _ in ()).throw(original))
    assert raised.value is original
    assert subject._status_code(ResponseFailure("private")) == 503

    for token in ("", "has space", "x" * 8193, b"bytes"):
        with pytest.raises(ValueError, match="bearer token"):
            subject._validated_token(cast(Any, token))


def test_environment_document_accepts_sdk_flattened_properties_shape() -> None:
    policy = _policy()
    value = SimpleNamespace(
        id=policy.environment_id,
        name=policy.environment_name,
        type="Microsoft.App/managedEnvironments",
        location=policy.location,
        tags={"gludd-owner": "b" * 64},
        provisioning_state="Succeeded",
        workload_profiles=[
            SimpleNamespace(
                name="gpu-t4",
                workload_profile_type="Consumption-GPU-NC8as-T4",
                minimum_count=0,
                maximum_count=1,
            )
        ],
    )

    document = subject._environment_document(value)

    assert document["properties"] == {
        "provisioningState": "Succeeded",
        "workloadProfiles": [
            {
                "name": "gpu-t4",
                "workloadProfileType": "Consumption-GPU-NC8as-T4",
                "minimumCount": 0,
                "maximumCount": 1,
            }
        ],
    }


def test_container_document_normalizes_sdk_enum_properties() -> None:
    """SDK enum wrappers must not turn a succeeded app into Unknown."""
    policy = _policy()
    value = SimpleNamespace(
        id=policy.expected_resource_id,
        name=policy.app_name,
        type="Microsoft.App/containerApps",
        location=policy.location,
        provisioning_state=SimpleNamespace(value="Succeeded"),
        latest_ready_revision_name=f"{policy.app_name}--0000007",
        workload_profile_name=policy.workload_profile_name,
        environment_id=policy.environment_id,
        configuration=SimpleNamespace(
            ingress=SimpleNamespace(fqdn="proof.example.invalid")
        ),
        template=SimpleNamespace(
            containers=[SimpleNamespace(image=IMAGE, args=[])],
            scale=SimpleNamespace(min_replicas=1, max_replicas=1, rules=[]),
        ),
    )

    document = cast(dict[str, Any], subject._container_document(value))

    assert document["properties"]["provisioningState"] == "Succeeded"
    assert (
        document["properties"]["latestReadyRevisionName"]
        == f"{policy.app_name}--0000007"
    )


def test_container_document_accepts_nested_sdk_properties_shape() -> None:
    """Normalize both SDK generations without losing ready-revision truth."""
    policy = _policy()
    value = SimpleNamespace(
        id=policy.expected_resource_id,
        name=policy.app_name,
        type="Microsoft.App/containerApps",
        location=policy.location,
        properties=SimpleNamespace(
            provisioning_state="Succeeded",
            latest_ready_revision_name=f"{policy.app_name}--0000007",
            event_stream_endpoint=(
                "https://eastus.azurecontainerapps.dev/subscriptions/ignored"
            ),
            workload_profile_name=policy.workload_profile_name,
            environment_id=policy.environment_id,
            configuration=SimpleNamespace(
                ingress=SimpleNamespace(fqdn="proof.example.invalid")
            ),
            template=SimpleNamespace(
                containers=[SimpleNamespace(image=IMAGE, args=[])],
                scale=SimpleNamespace(min_replicas=1, max_replicas=1, rules=[]),
            ),
        ),
    )

    document = cast(dict[str, Any], subject._container_document(value))

    assert document["properties"]["provisioningState"] == "Succeeded"
    assert (
        document["properties"]["latestReadyRevisionName"]
        == f"{policy.app_name}--0000007"
    )
    assert document["properties"]["workloadProfileName"] == "gpu-t4"
    assert document["properties"]["template"]["containers"] == [
        {"image": IMAGE, "command": [], "args": []}
    ]


class _ManagedEnvironments:
    def __init__(self, calls: list[tuple[str, tuple[object, ...]]]) -> None:
        self.calls = calls

    def get(self, resource_group_name: str, environment_name: str) -> object:
        self.calls.append(("environment.get", (resource_group_name, environment_name)))
        return SimpleNamespace(
            id=_policy().environment_id,
            name=environment_name,
            type="Microsoft.App/managedEnvironments",
            location="East US",
            tags={"gludd-owner": "b" * 64},
            properties=SimpleNamespace(
                provisioning_state="Succeeded",
                event_stream_endpoint=(
                    "https://eastus.azurecontainerapps.dev/subscriptions/ignored"
                ),
                workload_profiles=[
                    SimpleNamespace(
                        name="gpu-t4",
                        workload_profile_type="Consumption-GPU-NC8as-T4",
                        minimum_count=0,
                        maximum_count=4,
                    )
                ],
            ),
        )

    def get_auth_token(
        self,
        resource_group_name: str,
        environment_name: str,
    ) -> object:
        self.calls.append(
            ("environment.get_auth_token", (resource_group_name, environment_name))
        )
        return SimpleNamespace(properties=SimpleNamespace(token="environment-event-token"))

    def list_workload_profile_states(
        self,
        resource_group_name: str,
        environment_name: str,
    ) -> list[object]:
        self.calls.append(("profile_states.list", (resource_group_name, environment_name)))
        return [
            SimpleNamespace(
                name="gpu-t4",
                properties=SimpleNamespace(
                    current_count=1,
                    maximum_count=4,
                    minimum_count=0,
                ),
            )
        ]


class _ManagedEnvironmentUsages:
    def __init__(self, calls: list[tuple[str, tuple[object, ...]]]) -> None:
        self.calls = calls

    def list(self, resource_group_name: str, environment_name: str) -> list[object]:
        self.calls.append(("usages.list", (resource_group_name, environment_name)))
        return [
            SimpleNamespace(
                name=SimpleNamespace(
                    value="ConsumptionGPU-NC8as-T4",
                    localized_value="T4 quota",
                ),
                unit="Count",
                current_value=1.0,
                limit=4.0,
            )
        ]


class _ContainerApps:
    def __init__(self, calls: list[tuple[str, tuple[object, ...]]]) -> None:
        self.calls = calls

    def _app(self) -> object:
        policy = _policy()
        return SimpleNamespace(
            id=policy.expected_resource_id,
            name=policy.app_name,
            type="Microsoft.App/containerApps",
            location="East US",
            provisioning_state="Succeeded",
            latest_ready_revision_name=f"{policy.app_name}--0000007",
            event_stream_endpoint=(
                "https://eastus.azurecontainerapps.dev/subscriptions/ignored"
            ),
            workload_profile_name=policy.workload_profile_name,
            environment_id=policy.environment_id,
            configuration=SimpleNamespace(ingress=SimpleNamespace(fqdn="gludd-vllm-sdk-proof.kindstone.eastus.azurecontainerapps.io")),
            template=SimpleNamespace(
                containers=[SimpleNamespace(image=IMAGE, args=["--model", policy.model_name])],
                scale=SimpleNamespace(
                    min_replicas=0,
                    max_replicas=2,
                    rules=[
                        SimpleNamespace(
                            name="inference-requests",
                            http=SimpleNamespace(metadata={"concurrentRequests": "2"}),
                        )
                    ],
                ),
            ),
        )

    def get(self, resource_group_name: str, container_app_name: str) -> object:
        self.calls.append(("app.get", (resource_group_name, container_app_name)))
        return self._app()

    def get_auth_token(
        self,
        resource_group_name: str,
        container_app_name: str,
    ) -> object:
        self.calls.append(
            ("app.get_auth_token", (resource_group_name, container_app_name))
        )
        return SimpleNamespace(properties=SimpleNamespace(token="event-token"))

    def list_by_resource_group(self, resource_group_name: str) -> list[object]:
        self.calls.append(("apps.list", (resource_group_name,)))
        return [self._app()]


class _ContainerAppsRevisions:
    def __init__(self, calls: list[tuple[str, tuple[object, ...]]]) -> None:
        self.calls = calls

    def get_revision(
        self,
        resource_group_name: str,
        container_app_name: str,
        revision_name: str,
    ) -> object:
        self.calls.append(
            (
                "revision.get",
                (resource_group_name, container_app_name, revision_name),
            )
        )
        return SimpleNamespace(
            name=revision_name,
            active=True,
            replicas=1,
            health_state="Healthy",
            provisioning_state="Provisioned",
            running_state="Running",
            provisioning_error="provider-secret-must-not-be-normalized",
        )

    def list_revisions(
        self,
        resource_group_name: str,
        container_app_name: str,
    ) -> list[object]:
        self.calls.append(
            ("revisions.list", (resource_group_name, container_app_name))
        )
        return [
            SimpleNamespace(
                name=f"{container_app_name}--0000007",
                active=True,
                replicas=0,
                health_state="None",
                provisioning_state="Provisioning",
                running_state="Processing",
                provisioning_error="provider-secret-must-not-be-normalized",
            )
        ]


class _ContainerAppsRevisionReplicas:
    def __init__(self, calls: list[tuple[str, tuple[object, ...]]]) -> None:
        self.calls = calls

    def list_replicas(
        self,
        resource_group_name: str,
        container_app_name: str,
        revision_name: str,
    ) -> object:
        self.calls.append(
            (
                "replicas.list",
                (resource_group_name, container_app_name, revision_name),
            )
        )
        return SimpleNamespace(
            value=[
                SimpleNamespace(
                    running_state="NotRunning",
                    running_state_details=(
                        "provider-controlled prefix: ImagePullBackOff on legion"
                    ),
                    containers=[
                        SimpleNamespace(
                            ready=False,
                            started=False,
                            restart_count=3,
                            running_state="Waiting",
                            running_state_details=(
                                "private detail: ErrImagePull on legion"
                            ),
                            log_stream_endpoint="https://secret.invalid/log",
                            exec_endpoint="wss://secret.invalid/exec",
                        )
                    ],
                    init_containers=[],
                )
            ]
        )


class _ContainerAppsClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[object, ...]]] = []
        self.managed_environments = _ManagedEnvironments(self.calls)
        self.managed_environment_usages = _ManagedEnvironmentUsages(self.calls)
        self.container_apps = _ContainerApps(self.calls)
        self.container_apps_revisions = _ContainerAppsRevisions(self.calls)
        self.container_apps_revision_replicas = _ContainerAppsRevisionReplicas(
            self.calls
        )
        self.close_count = 0

    def close(self) -> None:
        self.close_count += 1


def test_sdk_read_views_call_only_exact_microsoft_read_operations_and_normalize() -> None:
    policy = _policy()
    client = _ContainerAppsClient()
    transports = AzureContainerAppsSDKReadTransports(client=client, policy=policy)
    root = policy.environment_id

    environment = transports.preflight.get_json(
        f"{root}?api-version={ENVIRONMENT_PREFLIGHT_API_VERSION}",
        "bounded-token",
    )
    usages = transports.preflight.get_json(
        f"{root}/usages?api-version={ENVIRONMENT_PREFLIGHT_API_VERSION}",
        "bounded-token",
    )
    states = transports.preflight.get_json(
        f"{root}/workloadProfileStates"
        f"?api-version={ENVIRONMENT_PREFLIGHT_API_VERSION}",
        "bounded-token",
    )
    app = transports.app.get_json("bounded-token")
    active_revision = transports.app.get_active_revision_json("bounded-token")
    revision_name = f"{policy.app_name}--0000007"
    revision = transports.app.get_revision_json("bounded-token", revision_name)
    replica_status = transports.app.get_replica_status_json(
        "bounded-token",
        revision_name,
    )
    lifecycle_environment = transports.lifecycle.get_environment("bounded-token")
    app_ids = transports.lifecycle.list_environment_app_ids("bounded-token")
    environment_document = cast(dict[str, Any], environment)
    usage_document = cast(dict[str, Any], usages)
    states_document = cast(dict[str, Any], states)
    app_document = cast(dict[str, Any], app)
    revision_document = cast(dict[str, Any], revision)
    active_revision_document = cast(dict[str, Any], active_revision)
    lifecycle_document = cast(dict[str, Any], lifecycle_environment)

    assert environment_document["properties"]["workloadProfiles"][0] == {
        "name": "gpu-t4",
        "workloadProfileType": "Consumption-GPU-NC8as-T4",
        "minimumCount": 0,
        "maximumCount": 4,
    }
    assert usage_document["value"][0]["currentValue"] == 1.0
    assert states_document["value"][0]["properties"]["maximumCount"] == 4
    assert app_document["properties"]["latestReadyRevisionName"].endswith("--0000007")
    assert revision_document == {
        "name": revision_name,
        "properties": {
            "active": True,
            "replicas": 1,
            "healthState": "Healthy",
            "provisioningState": "Provisioned",
            "runningState": "Running",
            "reasonClasses": [],
        },
    }
    assert active_revision_document == {
        "name": revision_name,
        "properties": {
            "active": True,
            "replicas": 0,
            "healthState": "None",
            "provisioningState": "Provisioning",
            "runningState": "Processing",
            "reasonClasses": [],
        },
    }
    assert "provider-secret" not in repr(revision_document)
    assert replica_status == {
        "replicaCount": 1,
        "readyContainerCount": 0,
        "startedContainerCount": 0,
        "restartCount": 3,
        "replicaRunningStates": ["NotRunning"],
        "containerRunningStates": ["Waiting"],
        "reasonClasses": ["image_pull_failure"],
    }
    assert "private detail" not in repr(replica_status)
    assert "secret.invalid" not in repr(replica_status)
    assert lifecycle_document["id"] == policy.environment_id
    assert app_ids == (policy.expected_resource_id,)
    assert client.calls == [
        ("environment.get", (policy.resource_group, policy.environment_name)),
        ("usages.list", (policy.resource_group, policy.environment_name)),
        ("profile_states.list", (policy.resource_group, policy.environment_name)),
        ("app.get", (policy.resource_group, policy.app_name)),
        ("revisions.list", (policy.resource_group, policy.app_name)),
        (
            "revision.get",
            (policy.resource_group, policy.app_name, revision_name),
        ),
        (
            "replicas.list",
            (policy.resource_group, policy.app_name, revision_name),
        ),
        ("environment.get", (policy.resource_group, policy.environment_name)),
        ("apps.list", (policy.resource_group,)),
    ]
    transports.app.close()
    transports.lifecycle.close()
    transports.preflight.close()
    assert client.close_count == 1


def test_sdk_system_events_classify_capacity_without_exposing_logs() -> None:
    """The official token flow may retain reason classes, never provider text."""
    policy = _policy()
    client = _ContainerAppsClient()
    transports = AzureContainerAppsSDKReadTransports(client=client, policy=policy)
    revision_name = f"{policy.app_name}--0000007"
    provider_line = json.dumps(
        {
            "RevisionName": revision_name,
            "Type": "Error",
            "Log": "Workload Profile Full; tenant-secret=hidden",
        }
    ).encode()
    response = MagicMock(ok=True, status_code=200)
    response.iter_lines.return_value = (provider_line,)

    with patch("requests.get", return_value=response) as request:
        document = transports.app.get_system_event_reason_classes(
            "bounded-token",
            revision_name,
        )

    assert document == {
        "reasonClasses": ["capacity_exhausted"],
        "eventCount": 1,
        "scopedEventCount": 1,
        "classifiedEventCount": 1,
        "errorEventCount": 1,
        "warningEventCount": 0,
        "unclassifiedErrorCount": 0,
    }
    assert "tenant-secret" not in repr(document)
    request.assert_called_once_with(
        (
            "https://eastus.azurecontainerapps.dev/subscriptions/"
            f"{SUBSCRIPTION}/resourceGroups/{policy.resource_group}/"
            f"containerApps/{policy.app_name}/eventstream"
        ),
        timeout=(5.0, 15.0),
        stream=True,
        allow_redirects=False,
        params={"follow": "false", "output": "json", "tailLines": 300},
        headers={"Authorization": "Bearer event-token"},
    )
    assert client.calls == [
        ("app.get", (policy.resource_group, policy.app_name)),
        ("app.get_auth_token", (policy.resource_group, policy.app_name)),
    ]


def test_sdk_environment_system_events_are_scoped_and_content_free() -> None:
    """Platform events use the owned environment token and reject other apps."""
    policy = _policy()
    client = _ContainerAppsClient()
    transports = AzureContainerAppsSDKReadTransports(client=client, policy=policy)
    private_detail = "tenant-secret=must-not-escape"
    response = MagicMock(ok=True, status_code=200)
    response.iter_lines.return_value = (
        json.dumps(
            {
                "ContainerAppName": "unrelated-app",
                "RevisionName": "unrelated-app--0000007",
                "Type": "Error",
                "Log": "Error provisioning revision. ErrorCode: [ErrImagePull]",
            }
        ).encode(),
        json.dumps(
            {
                "ContainerAppName": policy.app_name,
                "RevisionName": f"{policy.app_name}--0000007",
                "Type": "Error",
                "Log": (
                    "Error provisioning revision. ErrorCode: [ContainerCrashing]; "
                    f"{private_detail}"
                ),
            }
        ).encode(),
    )

    with patch("requests.get", return_value=response) as request:
        document = transports.lifecycle.get_environment_system_event_reason_classes(
            "bounded-token",
            f"{policy.app_name}--0000007",
        )

    assert document == {
        "reasonClasses": ["container_crash"],
        "eventCount": 2,
        "scopedEventCount": 1,
        "classifiedEventCount": 1,
        "errorEventCount": 1,
        "warningEventCount": 0,
        "unclassifiedErrorCount": 0,
    }
    assert private_detail not in repr(document)
    request.assert_called_once_with(
        (
            "https://eastus.azurecontainerapps.dev/subscriptions/"
            f"{SUBSCRIPTION}/resourceGroups/{policy.resource_group}/"
            f"managedEnvironments/{policy.environment_name}/eventstream"
        ),
        timeout=(5.0, 15.0),
        stream=True,
        allow_redirects=False,
        params={"follow": "false", "tailLines": 300},
        headers={"Authorization": "Bearer environment-event-token"},
    )
    assert client.calls == [
        ("environment.get", (policy.resource_group, policy.environment_name)),
        (
            "environment.get_auth_token",
            (policy.resource_group, policy.environment_name),
        ),
    ]


def test_sdk_system_events_reduce_large_structured_lines_by_approved_fields() -> None:
    """Unrelated provider payload cannot hide a bounded typed startup reason."""
    private_detail = "private-must-not-escape-" + ("x" * 5000)
    response = SimpleNamespace(
        ok=True,
        status_code=200,
        iter_lines=lambda: iter(
            (
                json.dumps(
                    {
                        "Log": "Error provisioning revision. ErrorCode: [Time-out]",
                        "ProviderMetadata": private_detail,
                    }
                ),
            )
        ),
    )

    document = subject._system_event_reason_classes(response)

    assert document == {
        "reasonClasses": ["startup_timeout"],
        "eventCount": 1,
        "scopedEventCount": 1,
        "classifiedEventCount": 1,
        "errorEventCount": 0,
        "warningEventCount": 0,
        "unclassifiedErrorCount": 0,
    }
    assert private_detail not in repr(document)


def test_sdk_system_events_classify_deployment_deadline_as_terminal_timeout() -> None:
    """Azure's deadline event becomes a fixed class without leaking its payload."""
    private_detail = "deployment-id=private-must-not-escape"
    response = SimpleNamespace(
        ok=True,
        status_code=200,
        iter_lines=lambda: iter(
            (
                "Deployment Progress Deadline Exceeded. "
                f"ErrorCode: [Time-out]; {private_detail}",
            )
        ),
    )

    document = subject._system_event_reason_classes(response)

    assert document == {
        "reasonClasses": ["startup_timeout"],
        "eventCount": 1,
        "scopedEventCount": 1,
        "classifiedEventCount": 1,
        "errorEventCount": 0,
        "warningEventCount": 0,
        "unclassifiedErrorCount": 0,
    }
    assert private_detail not in repr(document)


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://eastus.azurecontainerapps.dev/events",
        "https://azurecontainerapps.dev.attacker.invalid/events",
        "https://user@eastus.azurecontainerapps.dev/events",
        "https://eastus.azurecontainerapps.dev:444/events",
    ],
)
def test_sdk_system_event_url_rejects_scope_escape(endpoint: str) -> None:
    """Provider metadata cannot redirect the app token outside Azure's origin."""
    with pytest.raises(
        AzureContainerAppsSDKReadError,
        match="event endpoint is incomplete or ambiguous",
    ):
        subject._system_event_stream_url(endpoint, _policy())


def test_sdk_system_events_reject_redirect_response_and_censor_body() -> None:
    """A redirect is not event evidence and its provider body must not escape."""
    policy = _policy()
    client = _ContainerAppsClient()
    transports = AzureContainerAppsSDKReadTransports(client=client, policy=policy)
    private_line = b'{"Log":"Workload Profile Full; secret=must-not-escape"}'
    response = MagicMock(ok=True, status_code=302)
    response.iter_lines.return_value = (private_line,)

    with (
        patch("requests.get", return_value=response) as request,
        pytest.raises(
            AzureContainerAppsSDKReadError,
            match="event stream read failed",
        ) as raised,
    ):
        transports.app.get_system_event_reason_classes(
            "bounded-token",
            f"{policy.app_name}--0000007",
        )

    assert "must-not-escape" not in str(raised.value)
    assert request.call_args.kwargs["allow_redirects"] is False
    response.close.assert_called_once_with()


def test_sdk_system_events_reject_unbounded_line_count() -> None:
    """The system-event reducer fails closed instead of consuming an endless feed."""
    response = SimpleNamespace(
        ok=True,
        status_code=200,
        iter_lines=lambda: iter(("container creating",) * 301),
    )

    with pytest.raises(
        AzureContainerAppsSDKReadError,
        match="event stream response is incomplete",
    ):
        subject._system_event_reason_classes(response)


def test_sdk_system_events_bind_exact_revision_and_count_unknown_errors() -> None:
    """Historical revisions cannot contaminate a terminal diagnostic decision."""
    app_name = _policy().app_name
    revision_name = f"{app_name}--0000007"
    response = SimpleNamespace(
        ok=True,
        status_code=200,
        iter_lines=lambda: iter(
            (
                json.dumps(
                    {
                        "ContainerAppName": app_name,
                        "RevisionName": f"{app_name}--0000006",
                        "Type": "Error",
                        "Log": "ErrorCode: [ErrImagePull]",
                    }
                ),
                json.dumps(
                    {
                        "ContainerAppName": app_name,
                        "RevisionName": revision_name,
                        "Type": "Info",
                        "Log": "Creating a new revision",
                    }
                ),
                json.dumps(
                    {
                        "ContainerAppName": app_name,
                        "RevisionName": revision_name,
                        "Type": "Error",
                        "Log": "ErrorCode: [ContainerCrashing]",
                    }
                ),
                json.dumps(
                    {
                        "ContainerAppName": app_name,
                        "RevisionName": revision_name,
                        "Type": "Warning",
                        "Log": "bounded warning without a known reason",
                    }
                ),
                json.dumps(
                    {
                        "ContainerAppName": app_name,
                        "RevisionName": revision_name,
                        "Type": "Error",
                        "Log": "unknown terminal provider condition",
                    }
                ),
            )
        ),
    )

    document = subject._system_event_reason_classes(
        response,
        app_name=app_name,
        revision_name=revision_name,
    )

    assert document == {
        "reasonClasses": ["container_crash"],
        "eventCount": 5,
        "scopedEventCount": 4,
        "classifiedEventCount": 1,
        "errorEventCount": 2,
        "warningEventCount": 1,
        "unclassifiedErrorCount": 1,
    }


def test_sdk_system_event_line_rejects_malformed_and_unbounded_records() -> None:
    """Malformed stream records are skipped without retaining provider bytes."""
    assert subject._system_event_line(b"\xff") is None
    assert subject._system_event_line(object()) is None
    assert subject._system_event_line("") is None
    assert subject._system_event_line("x" * 65_537) is None
    assert subject._system_event_line("not-json") == "not-json"


def test_sdk_environment_event_scope_uses_only_bounded_identity_fields() -> None:
    """Nested app/revision identity is accepted while foreign identity is denied."""
    app_name = _policy().app_name
    assert subject._system_event_matches_app(
        {"wrapper": [{"ContainerAppName": app_name}]},
        app_name,
    )
    assert not subject._system_event_matches_app(
        {"ContainerAppName": "foreign-app"},
        app_name,
    )
    assert subject._system_event_matches_app(
        {"RevisionName": f"{app_name}--0000007"},
        app_name,
    )
    assert not subject._system_event_matches_app(
        {"RevisionName": "foreign-app--0000007"},
        app_name,
    )
    assert subject._system_event_matches_app(
        {"Log": f"revision for {app_name} entered provisioning"},
        app_name,
    )
    assert subject._system_event_matches_app(
        f"revision for {app_name} entered provisioning",
        app_name,
    )
    assert not subject._system_event_matches_app(
        {f"key-{index}": "ignored" for index in range(513)},
        app_name,
    )


def test_sdk_system_events_reject_missing_or_broken_stream_iterators() -> None:
    """Incomplete and interrupted event streams fail closed without provider text."""
    missing = SimpleNamespace(ok=True, status_code=200)
    with pytest.raises(
        AzureContainerAppsSDKReadError,
        match="event stream response is incomplete",
    ):
        subject._system_event_reason_classes(missing)

    def broken_lines() -> object:
        raise RuntimeError("provider-secret-must-not-escape")

    interrupted = SimpleNamespace(
        ok=True,
        status_code=200,
        iter_lines=broken_lines,
    )
    with pytest.raises(
        AzureContainerAppsSDKReadError,
        match="event stream response is incomplete",
    ) as raised:
        subject._system_event_reason_classes(interrupted)
    assert "provider-secret" not in str(raised.value)


def test_read_only_preflight_and_sdk_transport_share_one_supported_api_contract() -> None:
    """The live preflight must be able to traverse the SDK's fixed-path allowlist."""
    policy = _policy()
    client = _ContainerAppsClient()
    transports = AzureContainerAppsSDKReadTransports(client=client, policy=policy)
    credential = SimpleNamespace(
        get_token=lambda *_scopes: SimpleNamespace(token="bounded-token")
    )
    requirement = ModelServingRequirement(
        model_id=policy.model_name,
        revision=policy.model_revision,
        parameter_count=494_032_768,
        weight_bits=16,
        kv_cache_mib=2_048,
        runtime_overhead_mib=3_072,
    )

    result = AzureContainerAppReadOnlyPreflight(
        credential,
        transports.preflight,
    ).check(
        subscription_id=policy.subscription_id,
        resource_group=policy.resource_group,
        environment_name=policy.environment_name,
        workload_profile_name=policy.workload_profile_name,
        location=policy.location,
        requirement=requirement,
    )

    assert result.ready is True
    assert result.profile.workload_profile_type == policy.workload_profile_type
    assert result.quota_remaining == 3


def test_sdk_read_views_reject_scope_escape_and_censor_provider_failure() -> None:
    policy = _policy()
    client = _ContainerAppsClient()
    transports = AzureContainerAppsSDKReadTransports(client=client, policy=policy)

    with pytest.raises(ValueError, match="approved SDK read"):
        transports.preflight.get_json(
            "/subscriptions/other/providers/Microsoft.Authorization/roleAssignments",
            "bounded-token",
        )
    with pytest.raises(ValueError, match="revision_name"):
        transports.app.get_revision_json(
            "bounded-token",
            "foreign-app--0000001",
        )
    with pytest.raises(ValueError, match="revision_name"):
        transports.app.get_replica_status_json(
            "bounded-token",
            "foreign-app--0000001",
        )


def test_active_revision_inventory_handles_eventual_absence_and_ambiguity() -> None:
    """Startup observation accepts zero/one revisions and rejects ambiguous state."""
    policy = _policy()
    client = _ContainerAppsClient()

    class RevisionInventory:
        def __init__(self) -> None:
            self.records: list[object] = []

        def list_revisions(self, *_args: object) -> list[object]:
            return self.records

    revisions = RevisionInventory()
    client.container_apps_revisions = revisions
    transports = AzureContainerAppsSDKReadTransports(client=client, policy=policy)

    assert transports.app.get_active_revision_json("bounded-token") is None

    def revision(suffix: str) -> object:
        return SimpleNamespace(
            name=f"{policy.app_name}--{suffix}",
            active=True,
            replicas=0,
            health_state="None",
            provisioning_state="Provisioning",
            running_state="Processing",
        )

    revisions.records = [
        revision("0000001"),
        revision("0000002"),
    ]
    with pytest.raises(AzureContainerAppsSDKReadError, match="ambiguous"):
        transports.app.get_active_revision_json("bounded-token")

    secret = "provider-secret-must-not-appear"
    revisions.records = [SimpleNamespace(name=secret, active=True)]
    with pytest.raises(AzureContainerAppsSDKReadError, match="ambiguous") as caught:
        transports.app.get_active_revision_json("bounded-token")
    assert secret not in repr(caught.value)

    class ProviderFailure(RuntimeError):
        status_code = 500

    def fail(*_args: object, **_kwargs: object) -> object:
        raise ProviderFailure("provider-secret")

    with (
        patch.object(client.container_apps, "get", side_effect=fail),
        pytest.raises(AzureContainerAppsSDKReadError) as captured,
    ):
        transports.app.get_json("bounded-token")

    assert "provider-secret" not in repr(captured.value)


def test_sdk_exact_get_represents_only_not_found_as_absent() -> None:
    policy = _policy()
    client = _ContainerAppsClient()
    transports = AzureContainerAppsSDKReadTransports(client=client, policy=policy)

    class NotFound(RuntimeError):
        status_code = 404

    with (
        patch.object(
            client.container_apps,
            "get",
            side_effect=NotFound("secret"),
        ),
        patch.object(
            client.managed_environments,
            "get",
            side_effect=NotFound("secret"),
        ),
    ):
        assert transports.app.get_json("bounded-token") is None
        assert transports.lifecycle.get_environment("bounded-token") is None


def _metric_response(revision: str, values: list[float | None]) -> object:
    return SimpleNamespace(
        value=[
            SimpleNamespace(
                name=SimpleNamespace(value="GpuUtilizationPercentage"),
                unit="Percent",
                timeseries=[
                    SimpleNamespace(
                        metadatavalues=[
                            SimpleNamespace(
                                name=SimpleNamespace(value="revisionName"),
                                value=revision,
                            )
                        ],
                        data=[SimpleNamespace(maximum=value) for value in values],
                    )
                ],
            )
        ]
    )


class _Metrics:
    def __init__(self, responses: list[object]) -> None:
        self.responses = responses
        self.calls: list[dict[str, object]] = []

    def list(self, **kwargs: object) -> object:
        self.calls.append(dict(kwargs))
        return self.responses.pop(0)


class _MonitorClient:
    def __init__(self, responses: list[object]) -> None:
        self.metrics = _Metrics(responses)
        self.close_count = 0

    def close(self) -> None:
        self.close_count += 1


def test_gpu_attestor_requires_positive_exact_app_metric_and_unfiltered_sdk_query() -> None:
    """Query one exact app without relying on Azure's lagging dimension index."""
    identity = _identity()
    now = datetime(2026, 9, 7, 12, 0, tzinfo=UTC)
    client = _MonitorClient([_metric_response(identity.revision_name, [0.0, 37.5])])
    attestor = AzureContainerAppGPUUtilizationAttestor(
        client=client,
        expected_resource_id=identity.resource_id,
        now=lambda: now,
        sleep=lambda _seconds: pytest.fail("positive sample must not poll"),
        poll_timeout_seconds=120.0,
        poll_interval_seconds=10.0,
        lookback=timedelta(minutes=15),
    )

    evidence = attestor.attest(identity)

    assert evidence.metric_name == "GpuUtilizationPercentage"
    assert evidence.maximum_percent == 37.5
    assert evidence.positive_sample_count == 1
    assert client.metrics.calls == [
        {
            "resource_uri": identity.resource_id,
            "timespan": "2026-09-07T11:45:00+00:00/2026-09-07T12:00:00+00:00",
            "interval": "PT1M",
            "metricnames": "GpuUtilizationPercentage",
            "aggregation": "Maximum",
        }
    ]
    attestor.close()
    attestor.close()
    assert client.close_count == 1


def test_gpu_attestor_accepts_dimensionless_series_from_exact_ephemeral_app() -> None:
    """An unsplit metric stays bound by its exact owner-verified resource URI."""
    identity = _identity()
    response = _metric_response(identity.revision_name, [31.25])
    response.value[0].timeseries[0].metadatavalues = []
    client = _MonitorClient([response])
    attestor = AzureContainerAppGPUUtilizationAttestor(
        client=client,
        expected_resource_id=identity.resource_id,
        now=lambda: datetime(2026, 9, 7, 12, 0, tzinfo=UTC),
        sleep=lambda _seconds: pytest.fail("positive aggregate evidence must not poll"),
    )

    evidence = attestor.attest(identity)

    assert evidence.maximum_percent == 31.25
    assert evidence.revision_name == identity.revision_name


def test_gpu_attestor_keeps_polling_values_without_definition_discovery() -> None:
    """Use the documented contract while the exact metric values are still pending."""
    identity = _identity()
    clock = [datetime(2026, 9, 7, 12, 0, tzinfo=UTC)]
    events: list[str] = []
    client = _MonitorClient(
        [
            _metric_response(identity.revision_name, [0.0]),
            _metric_response(identity.revision_name, [9.0]),
        ]
    )

    def sleep(seconds: float) -> None:
        clock[0] += timedelta(seconds=seconds)

    attestor = AzureContainerAppGPUUtilizationAttestor(
        client=client,
        expected_resource_id=identity.resource_id,
        progress_sink=events.append,
        now=lambda: clock[0],
        sleep=sleep,
        poll_timeout_seconds=20.0,
        poll_interval_seconds=10.0,
    )

    assert attestor.attest(identity).maximum_percent == 9.0
    assert len(client.metrics.calls) == 2
    assert events == [
        "azure_containerapp_gpu_metric phase=awaiting_positive_sample "
        "state=heartbeat secret_output=false",
    ]


def test_gpu_attestor_never_reads_optional_definition_catalog() -> None:
    """A lagging or forbidden catalog cannot block a documented metric query."""
    identity = _identity()
    now = datetime(2026, 9, 7, 12, 0, tzinfo=UTC)
    client = _MonitorClient([_metric_response(identity.revision_name, [17.5])])
    definition_list = MagicMock(
        side_effect=AssertionError("definition catalog must not be read")
    )
    client.metric_definitions = SimpleNamespace(list=definition_list)
    attestor = AzureContainerAppGPUUtilizationAttestor(
        client=client,
        expected_resource_id=identity.resource_id,
        now=lambda: now,
        sleep=lambda _seconds: pytest.fail("documented metric query must not sleep"),
        poll_timeout_seconds=20.0,
        poll_interval_seconds=10.0,
    )

    assert attestor.attest(identity).maximum_percent == 17.5
    assert "metricnamespace" not in client.metrics.calls[0]
    definition_list.assert_not_called()


def test_gpu_attestor_needs_only_the_least_privilege_metric_value_reader() -> None:
    """The published query contract must not require definition-list permission."""
    identity = _identity()
    metrics = _Metrics([_metric_response(identity.revision_name, [22.0])])
    close = MagicMock()
    client = SimpleNamespace(metrics=metrics, close=close)
    attestor = AzureContainerAppGPUUtilizationAttestor(
        client=client,
        expected_resource_id=identity.resource_id,
        now=lambda: datetime(2026, 9, 7, 12, 0, tzinfo=UTC),
        sleep=lambda _seconds: pytest.fail("positive evidence must not poll"),
    )

    assert attestor.attest(identity).maximum_percent == 22.0
    attestor.close()
    close.assert_called_once_with()


@pytest.mark.parametrize(
    ("metric_name", "unit", "reason"),
    [
        ("CpuPercentage", "Percent", "metric_name_mismatch"),
        ("GpuUtilizationPercentage", "Bytes", "metric_unit_mismatch"),
    ],
)
def test_gpu_attestor_rejects_incompatible_metric_value_contract(
    metric_name: str,
    unit: str,
    reason: str,
) -> None:
    """A value response cannot override the independently documented contract."""
    identity = _identity()
    response = _metric_response(identity.revision_name, [10.0])
    metric = response.value[0]
    metric.name = SimpleNamespace(value=metric_name)
    metric.unit = unit
    client = _MonitorClient([response])
    attestor = AzureContainerAppGPUUtilizationAttestor(
        client=client,
        expected_resource_id=identity.resource_id,
    )

    with pytest.raises(AzureGPUUtilizationAttestationError) as captured:
        attestor.attest(identity)

    assert captured.value.failure is BackendFailure.INVALID_RESPONSE
    assert captured.value.reason == reason
    assert len(client.metrics.calls) == 1


def test_gpu_attestor_polls_content_free_until_a_positive_sample() -> None:
    identity = _identity()
    clock = [datetime(2026, 9, 7, 12, 0, tzinfo=UTC)]
    events: list[str] = []
    client = _MonitorClient(
        [
            _metric_response(identity.revision_name, [0.0]),
            _metric_response(identity.revision_name, [5.0]),
        ]
    )

    def sleep(seconds: float) -> None:
        clock[0] += timedelta(seconds=seconds)

    attestor = AzureContainerAppGPUUtilizationAttestor(
        client=client,
        expected_resource_id=identity.resource_id,
        progress_sink=events.append,
        now=lambda: clock[0],
        sleep=sleep,
        poll_timeout_seconds=20.0,
        poll_interval_seconds=10.0,
        lookback=timedelta(minutes=15),
    )

    assert attestor.attest(identity).maximum_percent == 5.0
    assert len(client.metrics.calls) == 2
    assert events == [
        "azure_containerapp_gpu_metric phase=awaiting_positive_sample state=heartbeat secret_output=false"
    ]


@pytest.mark.parametrize(
    "pending_response",
    [
        SimpleNamespace(value=[]),
        SimpleNamespace(
            value=[
                SimpleNamespace(
                    name=SimpleNamespace(value="GpuUtilizationPercentage"),
                    unit="Percent",
                    timeseries=[],
                )
            ]
        ),
        SimpleNamespace(
            value=[
                SimpleNamespace(
                    name=SimpleNamespace(value="GpuUtilizationPercentage"),
                    unit="Percent",
                    timeseries=[SimpleNamespace(metadatavalues=[], data=[])],
                )
            ]
        ),
    ],
    ids=(
        "empty-metric-collection",
        "empty-time-series",
        "empty-dimension-series",
    ),
)
def test_gpu_attestor_polls_when_monitor_has_not_ingested_samples_yet(
    pending_response: object,
) -> None:
    """A valid no-data response is eventual absence, never malformed evidence."""
    identity = _identity()
    clock = [datetime(2026, 9, 7, 12, 0, tzinfo=UTC)]
    events: list[str] = []
    client = _MonitorClient(
        [
            pending_response,
            _metric_response(identity.revision_name, [6.25]),
        ]
    )

    def sleep(seconds: float) -> None:
        clock[0] += timedelta(seconds=seconds)

    attestor = AzureContainerAppGPUUtilizationAttestor(
        client=client,
        expected_resource_id=identity.resource_id,
        progress_sink=events.append,
        now=lambda: clock[0],
        sleep=sleep,
        poll_timeout_seconds=20.0,
        poll_interval_seconds=10.0,
    )

    assert attestor.attest(identity).maximum_percent == 6.25
    assert len(client.metrics.calls) == 2
    assert events == [
        "azure_containerapp_gpu_metric phase=awaiting_positive_sample state=heartbeat secret_output=false"
    ]


@pytest.mark.parametrize(
    "response",
    [
        SimpleNamespace(),
        SimpleNamespace(value=None),
        _metric_response("gludd-vllm-sdk-proof--foreign", [50.0]),
        _metric_response("gludd-vllm-sdk-proof--0000007", [float("nan")]),
        _metric_response("gludd-vllm-sdk-proof--0000007", [101.0]),
    ],
    ids=(
        "missing-metric-collection",
        "null-metric-collection",
        "foreign-revision",
        "nan",
        "above-percent-range",
    ),
)
def test_gpu_attestor_refuses_ambiguous_or_impossible_metric_evidence(
    response: object,
) -> None:
    identity = _identity()
    now = datetime(2026, 9, 7, 12, 0, tzinfo=UTC)
    client = _MonitorClient([response])
    attestor = AzureContainerAppGPUUtilizationAttestor(
        client=client,
        expected_resource_id=identity.resource_id,
        now=lambda: now,
        sleep=lambda _seconds: None,
        poll_timeout_seconds=1.0,
        poll_interval_seconds=1.0,
        lookback=timedelta(minutes=15),
    )

    with pytest.raises(AzureGPUUtilizationAttestationError) as captured:
        attestor.attest(identity)

    assert captured.value.failure is BackendFailure.INVALID_RESPONSE


def test_gpu_attestor_emits_content_free_metric_rejection_reason() -> None:
    """Malformed provider data identifies only the rejected invariant."""
    identity = _identity()
    secret = "provider-secret-revision"
    events: list[str] = []
    client = _MonitorClient([_metric_response(secret, [50.0])])
    attestor = AzureContainerAppGPUUtilizationAttestor(
        client=client,
        expected_resource_id=identity.resource_id,
        progress_sink=events.append,
    )

    with pytest.raises(AzureGPUUtilizationAttestationError) as captured:
        attestor.attest(identity)

    assert getattr(captured.value, "reason", None) == "revision_dimension_mismatch"
    assert events == [
        "azure_containerapp_gpu_metric phase=response_rejected state=failed "
        "reason=revision_dimension_mismatch secret_output=false"
    ]
    assert secret not in repr(captured.value)
    assert secret not in "\n".join(events)


@pytest.mark.parametrize(
    ("status_code", "expected"),
    [
        (401, BackendFailure.AUTHENTICATION),
        (403, BackendFailure.AUTHORIZATION),
        (404, BackendFailure.NOT_FOUND),
        (408, BackendFailure.TIMEOUT),
        (429, BackendFailure.RATE_LIMITED),
        (500, BackendFailure.TRANSPORT),
    ],
)
def test_gpu_attestor_classifies_monitor_failures_without_provider_text(
    status_code: int,
    expected: BackendFailure,
) -> None:
    """Monitor errors retain only an actionable model-neutral category."""
    identity = _identity()
    secret = "provider-secret-must-not-escape"

    class ProviderFailure(RuntimeError):
        def __init__(self) -> None:
            super().__init__(secret)
            self.status_code = status_code

    client = _MonitorClient([])
    client.metrics.list = MagicMock(side_effect=ProviderFailure())
    attestor = AzureContainerAppGPUUtilizationAttestor(
        client=client,
        expected_resource_id=identity.resource_id,
    )

    with pytest.raises(AzureGPUUtilizationAttestationError) as captured:
        attestor.attest(identity)

    assert captured.value.failure is expected
    assert captured.value.http_status == status_code
    assert secret not in str(captured.value)
    assert secret not in repr(captured.value)


def test_gpu_attestor_polls_transient_bad_request_until_metric_index_is_ready() -> None:
    """A fresh resource's delayed metric registration is bounded eventual absence."""
    identity = _identity()
    clock = [datetime(2026, 9, 7, 12, 0, tzinfo=UTC)]
    events: list[str] = []
    secret = "provider-query-detail-must-not-escape"

    class MetricIndexPending(RuntimeError):
        status_code = 400

    client = _MonitorClient([])
    client.metrics.list = MagicMock(
        side_effect=[
            MetricIndexPending(secret),
            _metric_response(identity.revision_name, [12.5]),
        ]
    )

    def sleep(seconds: float) -> None:
        clock[0] += timedelta(seconds=seconds)

    attestor = AzureContainerAppGPUUtilizationAttestor(
        client=client,
        expected_resource_id=identity.resource_id,
        progress_sink=events.append,
        now=lambda: clock[0],
        sleep=sleep,
        poll_timeout_seconds=20.0,
        poll_interval_seconds=10.0,
    )

    assert attestor.attest(identity).maximum_percent == 12.5
    assert client.metrics.list.call_count == 2
    assert events == [
        "azure_containerapp_gpu_metric phase=query_pending state=heartbeat "
        "http_status=400 secret_output=false"
    ]
    assert secret not in "\n".join(events)


def test_gpu_attestor_reports_typed_rejection_when_bad_request_never_clears() -> None:
    """A malformed or unavailable metric query cannot poll or expose text forever."""
    identity = _identity()
    clock = [datetime(2026, 9, 7, 12, 0, tzinfo=UTC)]

    class MetricQueryRejected(RuntimeError):
        status_code = 400

    client = _MonitorClient([])
    client.metrics.list = MagicMock(side_effect=MetricQueryRejected("private"))

    def sleep(seconds: float) -> None:
        clock[0] += timedelta(seconds=seconds)

    attestor = AzureContainerAppGPUUtilizationAttestor(
        client=client,
        expected_resource_id=identity.resource_id,
        now=lambda: clock[0],
        sleep=sleep,
        poll_timeout_seconds=1.0,
        poll_interval_seconds=1.0,
    )

    with pytest.raises(AzureGPUUtilizationAttestationError) as captured:
        attestor.attest(identity)

    assert captured.value.failure is BackendFailure.INVALID_RESPONSE
    assert captured.value.reason == "metric_query_rejected"
    assert captured.value.http_status == 400
    assert "private" not in str(captured.value)


def test_gpu_attestor_classifies_bad_request_without_provider_text() -> None:
    """The first heartbeat explains a known Monitor rejection without leaking text."""
    identity = _identity()
    clock = [datetime(2026, 9, 7, 12, 0, tzinfo=UTC)]
    events: list[str] = []
    secret = "tenant-specific-provider-detail"

    class MetricUnavailable(RuntimeError):
        status_code = 400
        error = SimpleNamespace(
            message=(
                "Failed to find metric configuration for provider and metric; "
                + secret
            )
        )

    client = _MonitorClient([])
    client.metrics.list = MagicMock(side_effect=MetricUnavailable(secret))

    attestor = AzureContainerAppGPUUtilizationAttestor(
        client=client,
        expected_resource_id=identity.resource_id,
        progress_sink=events.append,
        now=lambda: clock[0],
        sleep=lambda _seconds: pytest.fail("known validation failures must not poll"),
        poll_timeout_seconds=1.0,
        poll_interval_seconds=1.0,
    )

    with pytest.raises(AzureGPUUtilizationAttestationError) as captured:
        attestor.attest(identity)

    assert captured.value.reason == "metric_unavailable"
    assert events == [
        "azure_containerapp_gpu_metric phase=response_rejected state=failed "
        "reason=metric_unavailable secret_output=false"
    ]
    assert secret not in repr(captured.value)
    assert secret not in "\n".join(events)


def test_gpu_attestor_classifies_sdk_response_json_without_exposing_it() -> None:
    """Azure Core can retain the useful Monitor detail only on response JSON."""
    identity = _identity()
    events: list[str] = []
    secret = "tenant-specific-response-detail"

    class ProviderResponse:
        status_code = 400

        @staticmethod
        def json() -> object:
            return {
                "error": {
                    "code": "BadRequest",
                    "message": (
                        "Failed to find metric configuration for provider and metric; "
                        + secret
                    ),
                }
            }

    class MetricUnavailable(RuntimeError):
        response = ProviderResponse()

    client = _MonitorClient([])
    client.metrics.list = MagicMock(side_effect=MetricUnavailable(secret))
    attestor = AzureContainerAppGPUUtilizationAttestor(
        client=client,
        expected_resource_id=identity.resource_id,
        progress_sink=events.append,
        sleep=lambda _seconds: pytest.fail("known validation failures must not poll"),
    )

    with pytest.raises(AzureGPUUtilizationAttestationError) as captured:
        attestor.attest(identity)

    assert captured.value.reason == "metric_unavailable"
    assert events == [
        "azure_containerapp_gpu_metric phase=response_rejected state=failed "
        "reason=metric_unavailable secret_output=false"
    ]
    assert secret not in repr(captured.value)
    assert secret not in "\n".join(events)


def test_gpu_attestor_reports_timeout_when_no_positive_sample_arrives() -> None:
    """A zero-only metric window cannot become evidence by exhausting polling."""
    identity = _identity()
    clock = [datetime(2026, 9, 7, 12, 0, tzinfo=UTC)]
    client = _MonitorClient(
        [
            _metric_response(identity.revision_name, [0.0]),
            _metric_response(identity.revision_name, [0.0]),
        ]
    )

    def sleep(seconds: float) -> None:
        clock[0] += timedelta(seconds=seconds)

    attestor = AzureContainerAppGPUUtilizationAttestor(
        client=client,
        expected_resource_id=identity.resource_id,
        now=lambda: clock[0],
        sleep=sleep,
        poll_timeout_seconds=1.0,
        poll_interval_seconds=1.0,
    )

    with pytest.raises(AzureGPUUtilizationAttestationError) as captured:
        attestor.attest(identity)

    assert captured.value.failure is BackendFailure.TIMEOUT


def test_gpu_attestor_rejects_foreign_identity_before_monitor_call() -> None:
    policy = _policy()
    identity = _identity(policy)
    client = _MonitorClient([])
    attestor = AzureContainerAppGPUUtilizationAttestor(
        client=client,
        expected_resource_id=policy.expected_resource_id.replace(
            policy.app_name,
            "another-app",
        ),
    )

    with pytest.raises(ValueError, match="exact Container App"):
        attestor.attest(identity)

    assert client.metrics.calls == []


def test_sdk_module_is_read_only_and_uses_microsoft_clients() -> None:
    source = Path(
        "src/general_ludd/infra/azure_containerapp_sdk.py"
    ).read_text(encoding="utf-8")

    assert "ContainerAppsAPIClient" in source
    assert "MonitorManagementClient" in source
    assert ".begin_create_or_update(" not in source
    assert ".begin_delete(" not in source
    assert "httpx" not in source
