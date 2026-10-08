"""Core failure and redaction tests for named-environment GPU preflight."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

import pytest

import general_ludd.infra.azure_containerapp_preflight_parsing as preflight_parsing
from general_ludd.infra.azure_containerapp_arm import (
    ENVIRONMENT_PREFLIGHT_API_VERSION,
    AzureContainerAppARMError,
)
from general_ludd.infra.azure_containerapp_gpu import ModelServingRequirement
from general_ludd.infra.azure_containerapp_preflight import (
    ARM_SCOPE,
    AzureContainerAppPreflightError,
    AzureContainerAppReadOnlyPreflight,
    PreflightTrace,
)

SUBSCRIPTION_ID = "11111111-2222-3333-4444-555555555555"
RESOURCE_GROUP = "gludd-models-eastus"
ENVIRONMENT = "gludd-gpu-environment"
PROFILE_NAME = "gpu-profile"
MODEL_REVISION = "7ae557604adf67be50417f59c2c2f167def9a775"
TOKEN = "opaque-token-that-must-never-appear"
ROOT = (
    f"/subscriptions/{SUBSCRIPTION_ID}/resourceGroups/{RESOURCE_GROUP}/"
    f"providers/Microsoft.App/managedEnvironments/{ENVIRONMENT}"
)


def _requirement(*, parameter_count: int = 494_032_768) -> ModelServingRequirement:
    return ModelServingRequirement(
        model_id="Qwen/Qwen2.5-0.5B-Instruct",
        revision=MODEL_REVISION,
        parameter_count=parameter_count,
        weight_bits=16,
        kv_cache_mib=2_048,
        runtime_overhead_mib=3_072,
    )


@dataclass
class _Token:
    token: str = TOKEN


class _Credential:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.scopes: list[str] = []

    def get_token(self, *scopes: str) -> _Token:
        self.scopes.extend(scopes)
        if self.error is not None:
            raise self.error
        return _Token()


class _Transport:
    def __init__(
        self,
        *,
        profile_type: str = "Consumption-GPU-NC8as-T4",
        usages: object | None = None,
        error_phase: str | None = None,
    ) -> None:
        self.profile_type = profile_type
        self.usages = usages if usages is not None else {
            "value": [
                {
                    "name": {"value": "ManagedEnvironmentConsumptionCores"},
                    "currentValue": 0,
                    "limit": 10,
                    "unit": "Count",
                }
            ]
        }
        self.error_phase = error_phase
        self.calls: list[tuple[str, str]] = []

    def get_json(self, path: str, bearer_token: str) -> object:
        self.calls.append((path, bearer_token))
        if "/workloadProfileStates?" in path:
            phase = "workload_profile_states"
            payload: object = {
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
        elif "/usages?" in path:
            phase = "usages"
            payload = self.usages
        else:
            phase = "environment"
            payload = {
                "id": ROOT,
                "name": ENVIRONMENT,
                "type": "Microsoft.App/managedEnvironments",
                "location": "eastus",
                "properties": {
                    "provisioningState": "Succeeded",
                    "workloadProfiles": [
                        {
                            "name": PROFILE_NAME,
                            "workloadProfileType": self.profile_type,
                            "minimumCount": 0,
                            "maximumCount": 1,
                        }
                    ],
                },
            }
        if phase == self.error_phase:
            raise RuntimeError(f"provider body with {TOKEN}")
        return payload


def _check(
    credential: _Credential,
    transport: _Transport,
    *,
    requirement: ModelServingRequirement | None = None,
    traces: list[PreflightTrace] | None = None,
) -> object:
    return AzureContainerAppReadOnlyPreflight(
        credential,
        transport,
        trace_sink=None if traces is None else traces.append,
    ).check(
        subscription_id=SUBSCRIPTION_ID,
        resource_group=RESOURCE_GROUP,
        environment_name=ENVIRONMENT,
        workload_profile_name=PROFILE_NAME,
        location="eastus",
        requirement=requirement or _requirement(),
    )


def test_authentication_uses_only_arm_scope() -> None:
    credential = _Credential()
    transport = _Transport()

    result = _check(credential, transport)

    assert result.ready is True
    assert credential.scopes == [ARM_SCOPE]
    assert transport.calls
    assert all(
        path.endswith(f"?api-version={ENVIRONMENT_PREFLIGHT_API_VERSION}")
        for path, _bearer_token in transport.calls
    )


def test_authentication_failure_is_redacted_and_traced() -> None:
    traces: list[PreflightTrace] = []
    credential = _Credential(
        error=RuntimeError(f"login failed {TOKEN} {SUBSCRIPTION_ID}")
    )

    with pytest.raises(
        AzureContainerAppPreflightError,
        match="authentication failed",
    ) as captured:
        _check(credential, _Transport(), traces=traces)

    assert TOKEN not in repr(captured.value)
    assert SUBSCRIPTION_ID not in repr(captured.value)
    assert traces[-1].reason == "authentication_failed"


@pytest.mark.parametrize(
    "phase",
    ["environment", "workload_profile_states"],
)
def test_arm_failures_are_redacted_and_identify_only_the_phase(phase: str) -> None:
    traces: list[PreflightTrace] = []

    with pytest.raises(AzureContainerAppPreflightError, match="read failed") as captured:
        _check(_Credential(), _Transport(error_phase=phase), traces=traces)

    assert TOKEN not in repr(captured.value)
    assert traces[-1].reason == f"{phase}_read_failed"


def test_optional_usage_transport_failure_defers_to_profile_state() -> None:
    traces: list[PreflightTrace] = []

    result = _check(
        _Credential(),
        _Transport(error_phase="usages"),
        traces=traces,
    )

    assert result.ready is True
    assert result.quota_verified is True
    assert result.quota_remaining == 1
    assert any(
        trace.phase == "supplementary_usage_unavailable"
        and trace.reason == "usages_read_failed"
        for trace in traces
    )
    assert TOKEN not in repr(traces)


@pytest.mark.parametrize(
    ("status", "reason", "message"),
    [
        (401, "environment_unauthorized", "not authorized"),
        (403, "environment_unauthorized", "not authorized"),
        (404, "environment_not_found", "does not exist"),
        (400, "environment_http_400", "read failed"),
        (429, "environment_http_429", "read failed"),
        (503, "environment_http_503", "read failed"),
    ],
)
def test_environment_http_status_is_observable_without_provider_body(
    status: int,
    reason: str,
    message: str,
) -> None:
    traces: list[PreflightTrace] = []

    class StatusTransport(_Transport):
        def get_json(self, _path: str, _bearer_token: str) -> object:
            raise AzureContainerAppARMError(
                f"safe status; private={TOKEN}",
                status_code=status,
            )

    with pytest.raises(AzureContainerAppPreflightError, match=message) as captured:
        _check(_Credential(), StatusTransport(), traces=traces)

    assert traces[-1].reason == reason
    assert TOKEN not in repr(captured.value)


def test_optional_usage_502_defers_to_authoritative_profile_state() -> None:
    traces: list[PreflightTrace] = []

    class UnavailableUsageTransport(_Transport):
        def get_json(self, path: str, bearer_token: str) -> object:
            if "/usages?" in path:
                self.calls.append((path, bearer_token))
                raise AzureContainerAppARMError(
                    f"private provider response {TOKEN}",
                    status_code=502,
                )
            return super().get_json(path, bearer_token)

    transport = UnavailableUsageTransport()

    result = _check(_Credential(), transport, traces=traces)

    assert result.ready is True
    assert result.quota_verified is True
    assert result.quota_remaining == 1
    assert any(
        trace.phase == "supplementary_usage_unavailable"
        and trace.reason == "usages_http_502"
        for trace in traces
    )
    assert any("/workloadProfileStates?" in path for path, _token in transport.calls)
    assert TOKEN not in repr(traces)


@pytest.mark.parametrize(
    ("status", "reason", "message"),
    [
        (401, "usages_unauthorized", "not authorized"),
        (403, "usages_unauthorized", "not authorized"),
        (404, "usages_not_found", "does not exist"),
        (429, "usages_http_429", "read failed"),
    ],
)
def test_usage_http_refusals_are_censored_and_phase_specific(
    status: int,
    reason: str,
    message: str,
) -> None:
    traces: list[PreflightTrace] = []

    class RefusedUsageTransport(_Transport):
        def get_json(self, path: str, bearer_token: str) -> object:
            if "/usages?" in path:
                raise AzureContainerAppARMError(
                    f"private provider response {TOKEN}",
                    status_code=status,
                )
            return super().get_json(path, bearer_token)

    with pytest.raises(AzureContainerAppPreflightError, match=message) as captured:
        _check(_Credential(), RefusedUsageTransport(), traces=traces)

    assert traces[-1].reason == reason
    assert TOKEN not in repr(captured.value)


def test_usage_error_without_http_status_defers_to_profile_state() -> None:
    traces: list[PreflightTrace] = []

    class OpaqueUsageTransport(_Transport):
        def get_json(self, path: str, bearer_token: str) -> object:
            if "/usages?" in path:
                raise AzureContainerAppARMError(f"private provider response {TOKEN}")
            return super().get_json(path, bearer_token)

    result = _check(_Credential(), OpaqueUsageTransport(), traces=traces)

    assert result.ready is True
    assert any(trace.reason == "usages_read_failed" for trace in traces)
    assert TOKEN not in repr(traces)


def test_serverless_capacity_without_provider_evidence_is_explicitly_deferred() -> None:
    traces: list[PreflightTrace] = []

    class DeferredCapacityTransport(_Transport):
        def get_json(self, path: str, bearer_token: str) -> object:
            if "/usages?" in path:
                self.calls.append((path, bearer_token))
                raise AzureContainerAppARMError(
                    f"private provider response {TOKEN}",
                    status_code=502,
                )
            if "/workloadProfileStates?" in path:
                self.calls.append((path, bearer_token))
                return {"value": []}
            return super().get_json(path, bearer_token)

    result = _check(_Credential(), DeferredCapacityTransport(), traces=traces)

    assert result.ready is True
    assert result.quota_verified is False
    assert result.quota_scope == "deployment"
    assert result.quota_name is None
    assert result.quota_remaining is None
    assert any(
        trace.phase == "quota_verification_deferred"
        and trace.reason == "provider_evidence_unavailable"
        for trace in traces
    )


def test_serverless_usage_quota_is_sufficient_when_profile_state_is_absent() -> None:
    traces: list[PreflightTrace] = []
    usage_name = "Managed Environment Consumption T4 Gpus"

    class UsageOnlyCapacityTransport(_Transport):
        def __init__(self) -> None:
            super().__init__(
                usages={
                    "value": [
                        {
                            "name": {"value": usage_name},
                            "currentValue": 1,
                            "limit": 3,
                            "unit": "Count",
                        }
                    ]
                }
            )

        def get_json(self, path: str, bearer_token: str) -> object:
            if "/workloadProfileStates?" in path:
                self.calls.append((path, bearer_token))
                return {"value": []}
            return super().get_json(path, bearer_token)

    result = _check(_Credential(), UsageOnlyCapacityTransport(), traces=traces)

    assert result.ready is True
    assert result.quota_verified is True
    assert result.quota_scope == "environment"
    assert result.quota_name == usage_name
    assert result.quota_remaining == 2


@pytest.mark.parametrize(
    "usages",
    [
        [],
        {},
        {"value": "not-a-list"},
        {"value": [{"name": {"value": "T4"}, "currentValue": -1, "limit": 1}]},
        {"value": [{"name": {"value": "T4"}, "currentValue": 2, "limit": 1}]},
        {"value": [], "nextLink": "https://attacker.invalid"},
    ],
)
def test_malformed_or_paginated_usage_results_fail_closed(usages: object) -> None:
    traces: list[PreflightTrace] = []

    with pytest.raises(AzureContainerAppPreflightError, match="usage response"):
        _check(_Credential(), _Transport(usages=usages), traces=traces)

    assert traces[-1].reason == "usage_response_invalid"


def test_a100_model_requires_exact_a100_environment_profile() -> None:
    result = _check(
        _Credential(),
        _Transport(profile_type="Consumption-GPU-NC24-A100"),
        requirement=_requirement(parameter_count=7_000_000_000),
    )

    assert result.profile.name == "A100"
    assert result.quota_verified is True


def test_model_too_large_fails_before_authentication_or_network() -> None:
    credential = _Credential()
    transport = _Transport()
    traces: list[PreflightTrace] = []

    with pytest.raises(AzureContainerAppPreflightError, match="exceeds A100"):
        _check(
            credential,
            transport,
            requirement=_requirement(parameter_count=100_000_000_000),
            traces=traces,
        )

    assert credential.scopes == []
    assert transport.calls == []
    assert traces[-1].reason == "profile_unavailable"


def test_trace_sink_failure_stops_preflight_before_authentication() -> None:
    credential = _Credential()

    def broken_sink(_trace: PreflightTrace) -> None:
        raise RuntimeError("private sink detail")

    with pytest.raises(RuntimeError, match="preflight trace publication failed"):
        AzureContainerAppReadOnlyPreflight(
            credential,
            _Transport(),
            trace_sink=broken_sink,
        ).check(
            subscription_id=SUBSCRIPTION_ID,
            resource_group=RESOURCE_GROUP,
            environment_name=ENVIRONMENT,
            workload_profile_name=PROFILE_NAME,
            location="eastus",
            requirement=_requirement(),
        )

    assert credential.scopes == []


def test_constructor_rejects_missing_protocol_methods() -> None:
    with pytest.raises(ValueError, match="get_token"):
        AzureContainerAppReadOnlyPreflight(object(), _Transport())  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="get_json"):
        AzureContainerAppReadOnlyPreflight(_Credential(), object())  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="trace_sink"):
        AzureContainerAppReadOnlyPreflight(
            _Credential(),
            _Transport(),
            trace_sink=object(),  # type: ignore[arg-type]
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("subscription_id", "not-a-uuid"),
        ("subscription_id", "AAAAAAAA-BBBB-4CCC-8DDD-EEEEEEEEEEEE"),
        ("resource_group", "../other"),
        ("environment_name", "other/environment"),
        ("workload_profile_name", "other/profile"),
        ("location", "East US"),
        ("location", 7),
    ],
)
def test_path_inputs_reject_noncanonical_or_ambiguous_resource_identity(
    field: str,
    value: object,
) -> None:
    arguments: dict[str, object] = {
        "subscription_id": SUBSCRIPTION_ID,
        "resource_group": RESOURCE_GROUP,
        "environment_name": ENVIRONMENT,
        "workload_profile_name": PROFILE_NAME,
        "location": "eastus",
    }
    arguments[field] = value

    with pytest.raises(ValueError):
        preflight_parsing.validate_path_inputs(**cast(Any, arguments))


@pytest.mark.parametrize("value", [None, "", "x" * 201, "line\nbreak"])
def test_provider_names_are_bounded_nonempty_printable_strings(value: object) -> None:
    with pytest.raises(AzureContainerAppPreflightError, match="invalid name"):
        preflight_parsing.provider_name(value, "provider")


@pytest.mark.parametrize("value", [True, "1", float("inf"), float("nan"), -1])
def test_quota_numbers_reject_boolean_nonfinite_and_negative_values(
    value: object,
) -> None:
    with pytest.raises(AzureContainerAppPreflightError, match="invalid quota"):
        preflight_parsing.quota_number(value)


@pytest.mark.parametrize("value", [True, "1", -1, 1_000_001])
def test_profile_counts_reject_boolean_noninteger_negative_and_unbounded_values(
    value: object,
) -> None:
    with pytest.raises(ValueError):
        preflight_parsing.count(value)


def _environment_payload() -> dict[str, Any]:
    return cast(dict[str, Any], _Transport().get_json(ROOT, TOKEN))


@pytest.mark.parametrize(
    "mutation",
    [
        lambda payload: payload.update(id=7),
        lambda payload: payload.update(id="/wrong"),
        lambda payload: payload.update(name=7),
        lambda payload: payload.update(name="other-environment"),
        lambda payload: payload.update(type=7),
        lambda payload: payload.update(type="Microsoft.App/containerApps"),
        lambda payload: payload.update(location=7),
        lambda payload: payload.update(location="westus"),
        lambda payload: payload.update(properties=None),
        lambda payload: payload["properties"].update(provisioningState="Updating"),
        lambda payload: payload["properties"].update(workloadProfiles="gpu-profile"),
        lambda payload: payload["properties"].update(workloadProfiles=[7]),
        lambda payload: payload["properties"]["workloadProfiles"][0].update(
            minimumCount=2,
            maximumCount=1,
        ),
        lambda payload: payload["properties"]["workloadProfiles"][0].update(
            name="other-profile"
        ),
        lambda payload: payload["properties"]["workloadProfiles"][0].update(
            workloadProfileType="Consumption-GPU-NC24-A100"
        ),
        lambda payload: payload["properties"]["workloadProfiles"][0].update(
            maximumCount=0
        ),
    ],
)
def test_environment_parser_rejects_identity_readiness_and_profile_drift(
    mutation: Any,
) -> None:
    payload = _environment_payload()
    mutation(payload)

    with pytest.raises(AzureContainerAppPreflightError):
        preflight_parsing.parse_environment(
            payload,
            expected_resource_id=ROOT,
            expected_environment_name=ENVIRONMENT,
            expected_location="eastus",
            expected_profile_name=PROFILE_NAME,
            expected_profile_type="Consumption-GPU-NC8as-T4",
        )


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {"value": [7]},
        {"value": [{"name": PROFILE_NAME, "properties": None}]},
        {
            "value": [
                {
                    "name": PROFILE_NAME,
                    "properties": {
                        "currentCount": 2,
                        "maximumCount": 1,
                        "minimumCount": 0,
                    },
                }
            ]
        },
        {
            "value": [
                {
                    "name": "other-profile",
                    "properties": {
                        "currentCount": 0,
                        "maximumCount": 1,
                        "minimumCount": 0,
                    },
                }
            ]
        },
    ],
)
def test_profile_state_parser_rejects_malformed_or_nonmatching_evidence(
    payload: object,
) -> None:
    with pytest.raises(AzureContainerAppPreflightError):
        preflight_parsing.parse_workload_profile_state(payload, PROFILE_NAME)
