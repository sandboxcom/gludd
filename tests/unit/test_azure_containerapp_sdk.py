"""Official Azure SDK read and GPU-utilization attestation contracts."""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any, cast
from unittest.mock import MagicMock, patch

import pytest

from general_ludd.infra import azure_containerapp_sdk as subject
from general_ludd.infra.azure_containerapp_live_proof import (
    LIVE_PROOF_ACKNOWLEDGEMENT,
    AzureContainerAppLiveProofPolicy,
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


class _ContainerAppsClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[object, ...]]] = []
        self.managed_environments = _ManagedEnvironments(self.calls)
        self.managed_environment_usages = _ManagedEnvironmentUsages(self.calls)
        self.container_apps = _ContainerApps(self.calls)
        self.container_apps_revisions = _ContainerAppsRevisions(self.calls)
        self.close_count = 0

    def close(self) -> None:
        self.close_count += 1


def test_sdk_read_views_call_only_exact_microsoft_read_operations_and_normalize() -> None:
    policy = _policy()
    client = _ContainerAppsClient()
    transports = AzureContainerAppsSDKReadTransports(client=client, policy=policy)
    root = policy.environment_id

    environment = transports.preflight.get_json(
        f"{root}?api-version=2025-07-01",
        "bounded-token",
    )
    usages = transports.preflight.get_json(
        f"{root}/usages?api-version=2025-07-01",
        "bounded-token",
    )
    states = transports.preflight.get_json(
        f"{root}/workloadProfileStates?api-version=2025-07-01",
        "bounded-token",
    )
    app = transports.app.get_json("bounded-token")
    revision_name = f"{policy.app_name}--0000007"
    revision = transports.app.get_revision_json("bounded-token", revision_name)
    lifecycle_environment = transports.lifecycle.get_environment("bounded-token")
    app_ids = transports.lifecycle.list_environment_app_ids("bounded-token")
    environment_document = cast(dict[str, Any], environment)
    usage_document = cast(dict[str, Any], usages)
    states_document = cast(dict[str, Any], states)
    app_document = cast(dict[str, Any], app)
    revision_document = cast(dict[str, Any], revision)
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
        },
    }
    assert "provider-secret" not in repr(revision_document)
    assert lifecycle_document["id"] == policy.environment_id
    assert app_ids == (policy.expected_resource_id,)
    assert client.calls == [
        ("environment.get", (policy.resource_group, policy.environment_name)),
        ("usages.list", (policy.resource_group, policy.environment_name)),
        ("profile_states.list", (policy.resource_group, policy.environment_name)),
        ("app.get", (policy.resource_group, policy.app_name)),
        (
            "revision.get",
            (policy.resource_group, policy.app_name, revision_name),
        ),
        ("environment.get", (policy.resource_group, policy.environment_name)),
        ("apps.list", (policy.resource_group,)),
    ]
    transports.app.close()
    transports.lifecycle.close()
    transports.preflight.close()
    assert client.close_count == 1


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


def test_gpu_attestor_requires_positive_exact_revision_metric_and_exact_sdk_query() -> None:
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
            "filter": f"revisionName eq '{identity.revision_name}'",
            "metricnamespace": "Microsoft.App/containerapps",
            "validate_dimensions": True,
        }
    ]
    attestor.close()
    attestor.close()
    assert client.close_count == 1


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
    "response",
    [
        _metric_response("gludd-vllm-sdk-proof--foreign", [50.0]),
        _metric_response("gludd-vllm-sdk-proof--0000007", [float("nan")]),
        _metric_response("gludd-vllm-sdk-proof--0000007", [101.0]),
    ],
    ids=("foreign-revision", "nan", "above-percent-range"),
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

    with pytest.raises(AzureGPUUtilizationAttestationError):
        attestor.attest(identity)


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
