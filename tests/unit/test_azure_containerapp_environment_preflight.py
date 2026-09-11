"""Named-environment Azure Container Apps GPU preflight contracts."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from general_ludd.infra.azure_containerapp_arm import (
    ENVIRONMENT_PREFLIGHT_API_VERSION,
)
from general_ludd.infra.azure_containerapp_gpu import ModelServingRequirement
from general_ludd.infra.azure_containerapp_preflight import (
    AzureContainerAppPreflightError,
    AzureContainerAppPreflightResult,
    AzureContainerAppReadOnlyPreflight,
    PreflightTrace,
)

SUBSCRIPTION_ID = "11111111-2222-3333-4444-555555555555"
RESOURCE_GROUP = "gludd-models-eastus"
ENVIRONMENT = "gludd-gpu-environment"
PROFILE_NAME = "gpu-t4"
PROFILE_TYPE = "Consumption-GPU-NC8as-T4"
MODEL_REVISION = "7ae557604adf67be50417f59c2c2f167def9a775"
TOKEN = "private-token-never-render"
ROOT = (
    f"/subscriptions/{SUBSCRIPTION_ID}/resourceGroups/{RESOURCE_GROUP}/"
    f"providers/Microsoft.App/managedEnvironments/{ENVIRONMENT}"
)


def _requirement() -> ModelServingRequirement:
    return ModelServingRequirement(
        model_id="Qwen/Qwen2.5-0.5B-Instruct",
        revision=MODEL_REVISION,
        parameter_count=494_032_768,
        weight_bits=16,
        kv_cache_mib=2_048,
        runtime_overhead_mib=3_072,
    )


@dataclass
class _Token:
    token: str = TOKEN


class _Credential:
    def __init__(self) -> None:
        self.scopes: list[str] = []

    def get_token(self, *scopes: str) -> _Token:
        self.scopes.extend(scopes)
        return _Token()


def _environment_payload() -> dict[str, object]:
    return {
        "id": ROOT,
        "name": ENVIRONMENT,
        "type": "Microsoft.App/managedEnvironments",
        "location": "eastus",
        "properties": {
            "provisioningState": "Succeeded",
            "workloadProfiles": [
                {
                    "name": PROFILE_NAME,
                    "workloadProfileType": PROFILE_TYPE,
                    "minimumCount": 0,
                    "maximumCount": 1,
                }
            ],
        },
    }


def _usages_payload() -> dict[str, object]:
    return {
        "value": [
            {
                "name": {"value": "ManagedEnvironmentConsumptionCores"},
                "currentValue": 0,
                "limit": 10,
                "unit": "Count",
            }
        ]
    }


def _states_payload() -> dict[str, object]:
    return {
        "value": [
            {
                "name": PROFILE_NAME,
                "properties": {
                    "currentCount": 0,
                    "maximumCount": 1,
                    "minimumCount": 0,
                },
            }
        ]
    }


class _Transport:
    def __init__(
        self,
        *,
        environment: object | None = None,
        usages: object | None = None,
        states: object | None = None,
    ) -> None:
        self.environment = _environment_payload() if environment is None else environment
        self.usages = _usages_payload() if usages is None else usages
        self.states = _states_payload() if states is None else states
        self.calls: list[tuple[str, str]] = []

    def get_json(self, path: str, bearer_token: str) -> object:
        self.calls.append((path, bearer_token))
        if "/workloadProfileStates?" in path:
            return self.states
        if "/usages?" in path:
            return self.usages
        return self.environment


def _check(
    transport: _Transport,
    *,
    trace_sink: list[PreflightTrace] | None = None,
) -> AzureContainerAppPreflightResult:
    return AzureContainerAppReadOnlyPreflight(
        _Credential(),
        transport,
        trace_sink=None if trace_sink is None else trace_sink.append,
    ).check(
        subscription_id=SUBSCRIPTION_ID,
        resource_group=RESOURCE_GROUP,
        environment_name=ENVIRONMENT,
        workload_profile_name=PROFILE_NAME,
        location="eastus",
        requirement=_requirement(),
    )


def test_named_environment_preflight_uses_only_three_exact_resource_gets() -> None:
    transport = _Transport()

    result = _check(transport)

    assert result.ready is True
    assert result.profile.workload_profile_type == PROFILE_TYPE
    assert result.available_profile_types == (PROFILE_TYPE,)
    assert result.quota_scope == "environment"
    assert result.quota_verified is True
    assert result.quota_name == PROFILE_NAME
    assert result.quota_remaining == 1
    assert transport.calls == [
        (f"{ROOT}?api-version={ENVIRONMENT_PREFLIGHT_API_VERSION}", TOKEN),
        (f"{ROOT}/usages?api-version={ENVIRONMENT_PREFLIGHT_API_VERSION}", TOKEN),
        (
            f"{ROOT}/workloadProfileStates"
            f"?api-version={ENVIRONMENT_PREFLIGHT_API_VERSION}",
            TOKEN,
        ),
    ]
    assert all("/locations/" not in path for path, _token in transport.calls)


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("location", "westus", "environment_location_mismatch"),
        ("name", "another-environment", "environment_identity_mismatch"),
        ("type", "Microsoft.App/containerApps", "environment_identity_mismatch"),
    ],
)
def test_environment_identity_mismatch_fails_before_quota_reads(
    field: str,
    value: str,
    reason: str,
) -> None:
    environment = _environment_payload()
    environment[field] = value
    traces: list[PreflightTrace] = []
    transport = _Transport(environment=environment)

    with pytest.raises(AzureContainerAppPreflightError, match="environment"):
        _check(transport, trace_sink=traces)

    assert len(transport.calls) == 1
    assert traces[-1].reason == reason


def test_environment_must_be_fully_provisioned_before_quota_reads() -> None:
    environment = _environment_payload()
    properties = environment["properties"]
    assert isinstance(properties, dict)
    properties["provisioningState"] = "Updating"
    traces: list[PreflightTrace] = []
    transport = _Transport(environment=environment)

    with pytest.raises(AzureContainerAppPreflightError, match="not ready"):
        _check(transport, trace_sink=traces)

    assert len(transport.calls) == 1
    assert traces[-1].reason == "environment_not_ready"


def test_serverless_gpu_profile_omitted_counts_defer_capacity_to_state_read() -> None:
    environment = _environment_payload()
    properties = environment["properties"]
    assert isinstance(properties, dict)
    profiles = properties["workloadProfiles"]
    assert isinstance(profiles, list)
    profile = profiles[0]
    assert isinstance(profile, dict)
    profile.pop("minimumCount", None)
    profile.pop("maximumCount", None)
    profiles.insert(
        0,
        {"name": "Consumption", "workloadProfileType": "Consumption"},
    )

    result = _check(_Transport(environment=environment))

    assert result.quota_verified is True
    assert result.quota_remaining == 1


@pytest.mark.parametrize(
    ("profile", "reason"),
    [
        ({"name": "other", "workloadProfileType": PROFILE_TYPE, "maximumCount": 1}, "workload_profile_missing"),
        ({"name": PROFILE_NAME, "workloadProfileType": "D4", "maximumCount": 1}, "workload_profile_type_mismatch"),
        ({"name": PROFILE_NAME, "workloadProfileType": PROFILE_TYPE, "maximumCount": 0}, "workload_profile_disabled"),
    ],
)
def test_exact_named_gpu_profile_must_be_configured(
    profile: dict[str, object],
    reason: str,
) -> None:
    environment = _environment_payload()
    properties = environment["properties"]
    assert isinstance(properties, dict)
    properties["workloadProfiles"] = [profile]
    traces: list[PreflightTrace] = []

    with pytest.raises(AzureContainerAppPreflightError, match="workload profile"):
        _check(_Transport(environment=environment), trace_sink=traces)

    assert traces[-1].reason == reason


@pytest.mark.parametrize(
    ("states", "reason", "message"),
    [
        (
            {
                "value": [
                    {
                        "name": PROFILE_NAME,
                        "properties": {"currentCount": 1, "maximumCount": 1, "minimumCount": 0},
                    }
                ]
            },
            "gpu_quota_exhausted",
            "quota is exhausted",
        ),
        (
            {
                "value": [
                    {
                        "name": PROFILE_NAME,
                        "properties": {"currentCount": 2, "maximumCount": 1, "minimumCount": 0},
                    }
                ]
            },
            "workload_profile_state_invalid",
            "invalid state",
        ),
        (
            {"value": [], "nextLink": "https://attacker.invalid/page"},
            "workload_profile_state_invalid",
            "paginated workload profile state response",
        ),
    ],
)
def test_workload_profile_state_must_prove_one_unit_of_headroom(
    states: object,
    reason: str,
    message: str,
) -> None:
    traces: list[PreflightTrace] = []

    with pytest.raises(AzureContainerAppPreflightError, match=message):
        _check(_Transport(states=states), trace_sink=traces)

    assert traces[-1].reason == reason


def test_missing_supplementary_profile_state_defers_to_deployment_capacity() -> None:
    traces: list[PreflightTrace] = []

    result = _check(_Transport(states={"value": []}), trace_sink=traces)

    assert result.ready is True
    assert result.quota_scope == "deployment"
    assert result.quota_verified is False
    assert any(
        trace.phase == "supplementary_profile_state_unavailable"
        and trace.reason == "workload_profile_state_missing"
        for trace in traces
    )


def test_matching_environment_usage_can_only_reduce_available_headroom() -> None:
    usages = {
        "value": [
            {
                "name": {"value": "ConsumptionGpuT4"},
                "currentValue": 1,
                "limit": 1,
                "unit": "Count",
            }
        ]
    }
    traces: list[PreflightTrace] = []

    with pytest.raises(AzureContainerAppPreflightError, match="quota is exhausted"):
        _check(_Transport(usages=usages), trace_sink=traces)

    assert traces[-1].reason == "gpu_quota_exhausted"


def test_trace_reports_every_read_without_project_identifiers_or_secrets() -> None:
    traces: list[PreflightTrace] = []

    _check(_Transport(), trace_sink=traces)

    assert [trace.phase for trace in traces] == [
        "authentication_started",
        "authentication_succeeded",
        "environment_discovered",
        "quota_discovered",
        "workload_profile_state_discovered",
        "preflight_ready",
    ]
    rendered = repr(traces)
    for private in (
        SUBSCRIPTION_ID,
        RESOURCE_GROUP,
        ENVIRONMENT,
        PROFILE_NAME,
        TOKEN,
        MODEL_REVISION,
        "Qwen",
    ):
        assert private not in rendered


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("resource_group", "../private"),
        ("resource_group", "trailing."),
        ("environment_name", "environment/other"),
        ("environment_name", ""),
        ("workload_profile_name", "profile?api-version=2099-01-01"),
        ("workload_profile_name", "line\nbreak"),
    ],
)
def test_untrusted_named_environment_inputs_fail_before_authentication(
    field: str,
    value: str,
) -> None:
    credential = _Credential()
    kwargs = {
        "subscription_id": SUBSCRIPTION_ID,
        "resource_group": RESOURCE_GROUP,
        "environment_name": ENVIRONMENT,
        "workload_profile_name": PROFILE_NAME,
        "location": "eastus",
        "requirement": _requirement(),
    }
    kwargs[field] = value

    with pytest.raises(ValueError, match=field):
        AzureContainerAppReadOnlyPreflight(credential, _Transport()).check(**kwargs)

    assert credential.scopes == []
