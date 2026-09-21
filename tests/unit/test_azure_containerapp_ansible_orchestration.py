"""Contracts for Ansible-owned Azure Container Apps orchestration."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest
import yaml
from ansible_collections.general_ludd.azure.plugins.filter import (
    containerapp_lifecycle,
)

ROOT = Path(__file__).resolve().parents[2]
COLLECTION = (
    ROOT / "collections" / "ansible_collections" / "general_ludd" / "azure"
)
ROLE = COLLECTION / "roles" / "container_app_deploy"

SUBSCRIPTION = "11111111-2222-3333-4444-555555555555"
RESOURCE_GROUP = "gludd-models-eastus"
ENVIRONMENT_NAME = "gludd-gpu-environment"
APP_NAME = "gludd-vllm-proof"
PROFILE_NAME = "gpu-t4"
PROFILE_TYPE = "Consumption-GPU-NC8as-T4"
REVISION = f"{APP_NAME}--0000007"
OWNER = "b" * 64
ENVIRONMENT_ID = (
    f"/subscriptions/{SUBSCRIPTION}/resourceGroups/{RESOURCE_GROUP}"
    f"/providers/Microsoft.App/managedEnvironments/{ENVIRONMENT_NAME}"
)
APP_ID = (
    f"/subscriptions/{SUBSCRIPTION}/resourceGroups/{RESOURCE_GROUP}"
    f"/providers/Microsoft.App/containerApps/{APP_NAME}"
)


def _contract(**overrides: object) -> dict[str, object]:
    result: dict[str, object] = {
        "subscription_id": SUBSCRIPTION,
        "resource_group": RESOURCE_GROUP,
        "environment_name": ENVIRONMENT_NAME,
        "environment_id": ENVIRONMENT_ID,
        "app_name": APP_NAME,
        "app_id": APP_ID,
        "workload_profile_name": PROFILE_NAME,
        "workload_profile_type": PROFILE_TYPE,
        "revision_name": REVISION,
        "owner_digest": OWNER,
        "required_profile_headroom": 1,
    }
    result.update(overrides)
    return result


def _raw(*, gpu: float = 37.5, foreign_apps: bool = False) -> dict[str, object]:
    apps: list[dict[str, object]] = [
        {
            "id": APP_ID,
            "name": APP_NAME,
            "tags": {"gludd-owner": OWNER},
            "properties": {
                "managedEnvironmentId": ENVIRONMENT_ID,
                "provisioningState": "Succeeded",
                "latestReadyRevisionName": REVISION,
                "workloadProfileName": PROFILE_NAME,
            },
        }
    ]
    if foreign_apps:
        apps.append(
            {
                "id": f"{APP_ID}-foreign",
                "name": f"{APP_NAME}-foreign",
                "tags": {"gludd-owner": "c" * 64},
                "properties": {"managedEnvironmentId": ENVIRONMENT_ID},
            }
        )
    return {
        "environments": {
            "response": {
                "value": [
                    {
                        "id": ENVIRONMENT_ID,
                        "name": ENVIRONMENT_NAME,
                        "location": "eastus",
                        "tags": {"gludd-owner": OWNER},
                        "properties": {
                            "provisioningState": "Succeeded",
                            "workloadProfiles": [
                                {
                                    "name": PROFILE_NAME,
                                    "workloadProfileType": PROFILE_TYPE,
                                    "minimumCount": 0,
                                    "maximumCount": 4,
                                }
                            ],
                        },
                    }
                ]
            }
        },
        "apps": {"response": {"value": apps}},
        "usages": {
            "response": {
                "value": [
                    {
                        "name": {"value": "ConsumptionGPU-NC8as-T4"},
                        "currentValue": 1,
                        "limit": 4,
                        "unit": "Count",
                    }
                ]
            }
        },
        "profile_states": {
            "response": {
                "value": [
                    {
                        "name": PROFILE_NAME,
                        "properties": {
                            "currentCount": 1,
                            "minimumCount": 0,
                            "maximumCount": 4,
                        },
                    }
                ]
            }
        },
        "metrics": {
            "response": {
                "value": [
                    {
                        "name": {"value": "GpuUtilizationPercentage"},
                        "unit": "Percent",
                        "timeseries": [
                            {
                                "metadatavalues": [
                                    {
                                        "name": {"value": "revisionName"},
                                        "value": REVISION,
                                    }
                                ],
                                "data": [
                                    {"maximum": 0.0},
                                    {"maximum": gpu},
                                ],
                            }
                        ],
                    }
                ]
            }
        },
    }


def _startup_raw(
    *,
    provisioning_state: str = "Provisioning",
    health_state: str = "None",
    running_state: str = "Processing",
    summary_replicas: int = 0,
    observed_replicas: int = 0,
) -> dict[str, object]:
    return {
        "revisions": {
            "response": {
                "value": [
                    {
                        "name": REVISION,
                        "properties": {
                            "provisioningState": provisioning_state,
                            "healthState": health_state,
                            "runningState": running_state,
                            "replicas": summary_replicas,
                        },
                    }
                ]
            }
        },
        "replicas": {
            "response": {
                "value": [
                    {"name": f"replica-{index}", "properties": {}}
                    for index in range(observed_replicas)
                ]
            }
        },
    }


def _startup_request(**overrides: object) -> dict[str, object]:
    result: dict[str, object] = {
        "revision_name": REVISION,
        "requested_replicas": 1,
    }
    result.update(overrides)
    return result


def test_observation_normalizes_only_bounded_content_free_evidence() -> None:
    observed = containerapp_lifecycle.normalize_containerapp_observation(
        _raw(),
        _contract(),
    )

    assert observed["protocol"] == "gludd-azure-containerapp-observation-v1"
    assert observed["environment"] == {
        "exists": True,
        "id": ENVIRONMENT_ID,
        "owned": True,
        "provisioning_state": "Succeeded",
        "ready": True,
    }
    assert observed["capacity"] == {
        "profile_name": PROFILE_NAME,
        "profile_type": PROFILE_TYPE,
        "current_nodes": 1,
        "maximum_nodes": 4,
        "quota_current": 1,
        "quota_limit": 4,
        "headroom": 3,
        "sufficient": True,
    }
    assert observed["app"]["ready"] is True
    assert observed["gpu"] == {
        "metric_name": "GpuUtilizationPercentage",
        "revision_name": REVISION,
        "maximum_percent": 37.5,
        "positive_sample_count": 1,
        "observed": True,
    }
    serialized = json.dumps(observed, sort_keys=True)
    assert "token" not in serialized.lower()
    assert "secret" not in serialized.lower()
    assert "credential" not in serialized.lower()


def test_observation_represents_clean_absence_without_fabricating_capacity() -> None:
    observed = containerapp_lifecycle.normalize_containerapp_observation(
        {
            "environments": {"response": {"value": []}},
            "apps": {"response": {"value": []}},
        },
        _contract(revision_name=""),
    )

    assert observed["environment"]["exists"] is False
    assert observed["app"]["exists"] is False
    assert observed["capacity"]["sufficient"] is None
    assert observed["gpu"]["observed"] is False


def test_observation_accepts_the_upstream_resource_info_flat_response_shape() -> None:
    raw = _raw()
    flattened: dict[str, object] = {}
    for key, result in raw.items():
        response = cast(dict[str, Any], result)["response"]
        flattened[key] = {"response": cast(dict[str, Any], response)["value"]}

    observed = containerapp_lifecycle.normalize_containerapp_observation(
        flattened,
        _contract(),
    )

    assert observed["environment"]["ready"] is True
    assert observed["app"]["ready"] is True
    assert observed["gpu"]["maximum_percent"] == 37.5


def test_observation_ignores_registered_module_invocation_metadata() -> None:
    raw = cast(dict[str, Any], _raw())
    raw["apps"]["invocation"] = {
        "module_args": {
            "client_secret": "VALUE_SPECIFIED_IN_NO_LOG_PARAMETER",
            "auth_source": "env",
        }
    }

    observed = containerapp_lifecycle.normalize_containerapp_observation(
        raw,
        _contract(),
    )

    assert observed["app"]["ready"] is True
    assert "invocation" not in observed


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (("environments", "response", "value", 0, "id"), "scope"),
        (("apps", "response", "value", 0, "properties", "managedEnvironmentId"), "environment"),
        (("metrics", "response", "value", 0, "name", "value"), "metric"),
        (("metrics", "response", "value", 0, "timeseries", 0, "metadatavalues", 0, "value"), "revision"),
    ],
)
def test_observation_rejects_foreign_or_ambiguous_provider_evidence(
    mutation: tuple[object, ...],
    message: str,
) -> None:
    raw = _raw()
    cursor: Any = raw
    for part in mutation[:-1]:
        cursor = cursor[part]
    leaf = mutation[-1]
    if message == "metric":
        cursor[leaf] = "Requests"
    elif message == "revision":
        cursor[leaf] = f"{APP_NAME}--foreign"
    else:
        cursor[leaf] = "/subscriptions/foreign/providers/Microsoft.App/x/y"

    with pytest.raises(ValueError, match=message):
        containerapp_lifecycle.normalize_containerapp_observation(
            raw,
            _contract(),
        )


def test_observation_rejects_secrets_pagination_and_unbounded_results() -> None:
    secret = cast(dict[str, Any], _raw())
    secret["apps"]["response"]["clientSecret"] = "do-not-retain"
    with pytest.raises(ValueError, match="secret-bearing"):
        containerapp_lifecycle.normalize_containerapp_observation(
            secret,
            _contract(),
        )

    paginated = cast(dict[str, Any], _raw())
    paginated["apps"]["response"]["nextLink"] = "https://example.invalid"
    with pytest.raises(ValueError, match="pagination"):
        containerapp_lifecycle.normalize_containerapp_observation(
            paginated,
            _contract(),
        )

    unbounded = cast(dict[str, Any], _raw())
    unbounded["apps"]["response"]["value"] = [
        {"id": APP_ID, "properties": {"managedEnvironmentId": ENVIRONMENT_ID}}
    ] * 513
    with pytest.raises(ValueError, match="bounded"):
        containerapp_lifecycle.normalize_containerapp_observation(
            unbounded,
            _contract(),
        )


@pytest.mark.parametrize("gpu", [float("nan"), -1.0, 101.0])
def test_observation_rejects_impossible_gpu_samples(gpu: float) -> None:
    with pytest.raises(ValueError, match="GPU metric"):
        containerapp_lifecycle.normalize_containerapp_observation(
            _raw(gpu=gpu),
            _contract(),
        )


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"environment_name": ""}, "environment_name"),
        ({"owner_digest": "not-a-digest"}, "owner_digest"),
        ({"required_profile_headroom": True}, "number"),
        ({"required_profile_headroom": -1}, "non-negative"),
        ({"required_profile_headroom": 1.5}, "integer"),
    ],
)
def test_observation_rejects_malformed_contracts(
    overrides: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        containerapp_lifecycle.normalize_containerapp_observation(
            _raw(),
            _contract(**overrides),
        )


def test_observation_rejects_ambiguous_inventory() -> None:
    environments = cast(dict[str, Any], _raw())
    environment_values = environments["environments"]["response"]["value"]
    environment_values.append(dict(environment_values[0]))
    with pytest.raises(ValueError, match="environment inventory is ambiguous"):
        containerapp_lifecycle.normalize_containerapp_observation(
            environments,
            _contract(),
        )

    apps = cast(dict[str, Any], _raw())
    app_values = apps["apps"]["response"]["value"]
    app_values.append(dict(app_values[0]))
    with pytest.raises(ValueError, match="app inventory is ambiguous"):
        containerapp_lifecycle.normalize_containerapp_observation(
            apps,
            _contract(),
        )


@pytest.mark.parametrize(
    ("failure", "message"),
    [
        ("profiles_shape", "workload profiles"),
        ("profile_absent", "absent or ambiguous"),
        ("profile_type", "profile type drifted"),
        ("capacity_absent", "capacity evidence"),
        ("capacity_impossible", "capacity evidence is impossible"),
    ],
)
def test_observation_rejects_unusable_capacity_evidence(
    failure: str,
    message: str,
) -> None:
    raw = cast(dict[str, Any], _raw())
    environment_properties = raw["environments"]["response"]["value"][0][
        "properties"
    ]
    if failure == "profiles_shape":
        environment_properties["workloadProfiles"] = {}
    elif failure == "profile_absent":
        environment_properties["workloadProfiles"][0]["name"] = "other"
    elif failure == "profile_type":
        environment_properties["workloadProfiles"][0][
            "workloadProfileType"
        ] = "Consumption-GPU-A100"
    elif failure == "capacity_absent":
        raw["profile_states"]["response"]["value"] = []
    else:
        raw["profile_states"]["response"]["value"][0]["properties"][
            "currentCount"
        ] = 5

    with pytest.raises(ValueError, match=message):
        containerapp_lifecycle.normalize_containerapp_observation(
            raw,
            _contract(),
        )


@pytest.mark.parametrize(
    ("failure", "message"),
    [
        ("ambiguous", "ambiguous"),
        ("series_shape", "timeseries"),
        ("dimensions_shape", "dimensions"),
        ("samples_shape", "samples"),
        ("sample_type", "sample is invalid"),
    ],
)
def test_observation_rejects_unusable_gpu_evidence(
    failure: str,
    message: str,
) -> None:
    raw = cast(dict[str, Any], _raw())
    metrics = raw["metrics"]["response"]["value"]
    if failure == "ambiguous":
        metrics.append(dict(metrics[0]))
    elif failure == "series_shape":
        metrics[0]["timeseries"] = {}
    elif failure == "dimensions_shape":
        metrics[0]["timeseries"][0]["metadatavalues"] = {}
    elif failure == "samples_shape":
        metrics[0]["timeseries"][0]["data"] = {}
    else:
        metrics[0]["timeseries"][0]["data"][0]["maximum"] = "invalid"

    with pytest.raises(ValueError, match=message):
        containerapp_lifecycle.normalize_containerapp_observation(
            raw,
            _contract(),
        )


def test_observation_represents_metric_series_without_numeric_samples() -> None:
    raw = cast(dict[str, Any], _raw())
    raw["metrics"]["response"]["value"][0]["timeseries"][0]["data"] = [
        {"maximum": None}
    ]

    observed = containerapp_lifecycle.normalize_containerapp_observation(
        raw,
        _contract(),
    )

    assert observed["gpu"]["observed"] is False
    assert observed["gpu"]["maximum_percent"] is None


def test_startup_diagnosis_classifies_terminal_zero_replica_as_placement() -> None:
    diagnosis = containerapp_lifecycle.diagnose_containerapp_startup(
        _startup_raw(
            provisioning_state="Failed",
            health_state="Unhealthy",
            running_state="Failed",
        ),
        _startup_request(),
    )

    assert diagnosis == {
        "protocol": "gludd-azure-containerapp-startup-diagnosis-v1",
        "state": "placement_unavailable",
        "phase": "placement",
        "retryable": True,
        "model_quality_relevant": False,
        "action": "failover_profile_or_region",
        "requested_replicas": 1,
        "observed_replicas": 0,
    }
    serialized = json.dumps(diagnosis, sort_keys=True)
    assert REVISION not in serialized
    assert APP_NAME not in serialized


@pytest.mark.parametrize(
    ("raw", "state", "phase", "retryable", "action"),
    [
        (
            _startup_raw(),
            "pending",
            "placement",
            True,
            "wait_for_bounded_startup",
        ),
        (
            _startup_raw(
                provisioning_state="Provisioned",
                health_state="Healthy",
                running_state="Running",
                summary_replicas=1,
                observed_replicas=1,
            ),
            "ready",
            "ready",
            False,
            "continue_to_inference",
        ),
        (
            _startup_raw(
                provisioning_state="Failed",
                health_state="Unhealthy",
                running_state="Degraded",
                summary_replicas=1,
                observed_replicas=1,
            ),
            "runtime_unhealthy",
            "runtime",
            False,
            "inspect_runner_and_image",
        ),
    ],
)
def test_startup_diagnosis_distinguishes_pending_ready_and_runtime_failure(
    raw: dict[str, object],
    state: str,
    phase: str,
    retryable: bool,
    action: str,
) -> None:
    diagnosis = containerapp_lifecycle.diagnose_containerapp_startup(
        raw,
        _startup_request(),
    )

    assert diagnosis["state"] == state
    assert diagnosis["phase"] == phase
    assert diagnosis["retryable"] is retryable
    assert diagnosis["action"] == action
    assert diagnosis["model_quality_relevant"] is False


def test_startup_diagnosis_rejects_ambiguous_or_secret_bearing_evidence() -> None:
    ambiguous = cast(dict[str, Any], _startup_raw())
    revisions = ambiguous["revisions"]["response"]["value"]
    revisions.append(dict(revisions[0]))
    with pytest.raises(ValueError, match="revision inventory is absent or ambiguous"):
        containerapp_lifecycle.diagnose_containerapp_startup(
            ambiguous,
            _startup_request(),
        )

    secret = cast(dict[str, Any], _startup_raw())
    secret["replicas"]["response"]["clientSecret"] = "do-not-retain"
    with pytest.raises(ValueError, match="secret-bearing"):
        containerapp_lifecycle.diagnose_containerapp_startup(
            secret,
            _startup_request(),
        )


@pytest.mark.parametrize(
    ("startup_request", "message"),
    [
        ({"revision_name": REVISION}, "exact schema"),
        ({**_startup_request(), "unexpected": True}, "exact schema"),
        (_startup_request(requested_replicas=True), "number"),
        (_startup_request(requested_replicas=0), "at least one"),
    ],
)
def test_startup_diagnosis_rejects_invalid_request_contracts(
    startup_request: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        containerapp_lifecycle.diagnose_containerapp_startup(
            _startup_raw(),
            startup_request,
        )


def test_startup_diagnosis_represents_revision_not_yet_observed() -> None:
    diagnosis = containerapp_lifecycle.diagnose_containerapp_startup(
        _startup_raw(),
        _startup_request(revision_name="foreign--0000001"),
    )

    assert diagnosis["state"] == "not_observed"
    assert diagnosis["phase"] == "discovery"
    assert diagnosis["action"] == "wait_for_revision"
    assert diagnosis["observed_replicas"] == 0


def test_startup_diagnosis_filter_is_available_to_ansible() -> None:
    filters = containerapp_lifecycle.FilterModule().filters()

    assert filters["containerapp_startup_diagnosis"] is (
        containerapp_lifecycle.diagnose_containerapp_startup
    )


def test_lifecycle_decision_is_fail_closed_and_terraform_only() -> None:
    observed = containerapp_lifecycle.normalize_containerapp_observation(
        _raw(gpu=0.0),
        _contract(),
    )
    audited = {
        "state": "present",
        "plan_audited": True,
        "terraform_plan_sha256": "a" * 64,
        "operation_digest": "d" * 64,
    }

    assert containerapp_lifecycle.decide_containerapp_lifecycle(
        observed,
        {"state": "observe"},
    ) == {
        "action": "observe",
        "requires_terraform": False,
        "reason": "observation_requested",
    }
    ready = containerapp_lifecycle.decide_containerapp_lifecycle(
        observed,
        audited,
    )
    assert ready["action"] == "noop"
    assert ready["requires_terraform"] is False

    missing = containerapp_lifecycle.normalize_containerapp_observation(
        {
            "environments": {"response": {"value": []}},
            "apps": {"response": {"value": []}},
        },
        _contract(revision_name=""),
    )
    apply = containerapp_lifecycle.decide_containerapp_lifecycle(missing, audited)
    assert apply == {
        "action": "apply",
        "requires_terraform": True,
        "reason": "desired_resources_absent",
        "operation_digest": "d" * 64,
        "terraform_plan_sha256": "a" * 64,
    }

    with pytest.raises(ValueError, match="audited"):
        containerapp_lifecycle.decide_containerapp_lifecycle(
            missing,
            {**audited, "plan_audited": False},
        )


def test_lifecycle_decision_covers_plan_absence_and_capacity_drift() -> None:
    absent = containerapp_lifecycle.normalize_containerapp_observation(
        {
            "environments": {"response": {"value": []}},
            "apps": {"response": {"value": []}},
        },
        _contract(revision_name=""),
    )
    assert containerapp_lifecycle.decide_containerapp_lifecycle(
        absent,
        {"state": "planned"},
    )["action"] == "plan"
    assert containerapp_lifecycle.decide_containerapp_lifecycle(
        absent,
        {"state": "absent"},
    )["action"] == "noop"

    with pytest.raises(ValueError, match="state must"):
        containerapp_lifecycle.decide_containerapp_lifecycle(
            absent,
            {"state": "unknown"},
        )

    constrained = cast(dict[str, Any], _raw())
    constrained["profile_states"]["response"]["value"][0]["properties"][
        "currentCount"
    ] = 3
    observed = containerapp_lifecycle.normalize_containerapp_observation(
        constrained,
        _contract(required_profile_headroom=2),
    )
    with pytest.raises(ValueError, match="insufficient capacity"):
        containerapp_lifecycle.decide_containerapp_lifecycle(
            observed,
            {
                "state": "present",
                "plan_audited": True,
                "terraform_plan_sha256": "a" * 64,
                "operation_digest": "d" * 64,
            },
        )


def test_lifecycle_teardown_refuses_foreign_apps_and_wrong_owner() -> None:
    foreign = containerapp_lifecycle.normalize_containerapp_observation(
        _raw(foreign_apps=True),
        _contract(),
    )
    with pytest.raises(ValueError, match="foreign apps"):
        containerapp_lifecycle.decide_containerapp_lifecycle(
            foreign,
            {"state": "absent", "operation_digest": "d" * 64},
        )

    wrong_owner = cast(dict[str, Any], _raw())
    wrong_owner["environments"]["response"]["value"][0]["tags"] = {
        "gludd-owner": "c" * 64
    }
    observed = containerapp_lifecycle.normalize_containerapp_observation(
        wrong_owner,
        _contract(),
    )
    with pytest.raises(ValueError, match="owned"):
        containerapp_lifecycle.decide_containerapp_lifecycle(
            observed,
            {"state": "absent", "operation_digest": "d" * 64},
        )


def _retention_request(**overrides: object) -> dict[str, object]:
    result: dict[str, object] = {
        "now": "2026-09-08T12:00:00Z",
        "scope_digest": "d" * 64,
        "runnable_todo_count": 0,
        "expected_next_demand_seconds": 1_800,
        "policy": {
            "preset": "zero_cost_only",
            "max_idle_hourly_cost_microusd": 0,
            "max_idle_monthly_cost_microusd": 0,
            "max_retention_cost_microusd": 0,
            "max_retention_seconds": 21_600,
            "max_price_age_seconds": 31_536_000,
            "max_latency_age_seconds": 86_400,
            "max_cost_per_saved_hour_microusd": 0,
        },
        "environment_latency": {
            "p50_seconds": 18.0,
            "p95_seconds": 964.0,
            "sample_count": 2,
            "observed_at": "2026-09-08T12:00:00Z",
        },
        "app_latency": {
            "p50_seconds": 90.0,
            "p95_seconds": 240.0,
            "sample_count": 2,
            "observed_at": "2026-09-08T12:00:00Z",
        },
        "min_replicas": 0,
        "activation_blocked_when_idle": False,
        "has_dedicated_profiles": False,
        "has_private_endpoint": False,
        "has_planned_maintenance": False,
        "has_paid_logging": False,
    }
    result.update(overrides)
    return result


def test_ansible_filter_exposes_core_idle_retention_plan_as_safe_facts() -> None:
    planned = containerapp_lifecycle.plan_containerapp_idle_retention(
        _retention_request()
    )

    assert planned["protocol"] == "gludd-azure-idle-retention-fact-v1"
    assert planned["retained_layers"] == ["managed_environment"]
    assert planned["destroyed_layers"] == ["container_app"]
    assert planned["retention_seconds"] == 1_800
    assert planned["hourly_cost_microusd"] == 0
    assert planned["p95_seconds_saved"] == 964.0
    assert planned["scope_digest"] == "d" * 64
    assert "subscription" not in json.dumps(planned).casefold()
    assert "credential" not in json.dumps(planned).casefold()


def test_ansible_retention_filter_refuses_nonidle_or_ambiguous_input() -> None:
    with pytest.raises(ValueError, match="empty durable todo"):
        containerapp_lifecycle.plan_containerapp_idle_retention(
            _retention_request(runnable_todo_count=1)
        )
    with pytest.raises(ValueError, match="exact schema"):
        containerapp_lifecycle.plan_containerapp_idle_retention(
            {**_retention_request(), "unexpected": "field"}
        )

    wrong_app_owner = cast(dict[str, Any], _raw())
    wrong_app_owner["apps"]["response"]["value"][0]["tags"] = {
        "gludd-owner": "c" * 64
    }
    observed = containerapp_lifecycle.normalize_containerapp_observation(
        wrong_app_owner,
        _contract(),
    )
    with pytest.raises(ValueError, match="app is not owned"):
        containerapp_lifecycle.decide_containerapp_lifecycle(
            observed,
            {"state": "absent", "operation_digest": "d" * 64},
        )


def test_role_uses_mature_read_and_iac_collections_and_sets_fact() -> None:
    task_paths = (ROLE / "tasks" / "main.yml", ROLE / "tasks" / "observe.yml")
    task_documents = [
        yaml.safe_load(path.read_text(encoding="utf-8")) for path in task_paths
    ]
    tasks = "\n".join(path.read_text(encoding="utf-8") for path in task_paths)
    defaults = yaml.safe_load(
        (ROLE / "defaults" / "main.yml").read_text(encoding="utf-8")
    )
    galaxy = yaml.safe_load((COLLECTION / "galaxy.yml").read_text(encoding="utf-8"))

    assert all(isinstance(document, list) for document in task_documents)
    assert "azure.azcollection.azure_rm_resource_info:" in tasks
    assert "cloud.terraform.terraform:" in tasks
    assert 'binary_path: "{{ containerapp_iac_binary_path }}"' in tasks
    assert "general_ludd.azure.containerapp_observation" in tasks
    assert "general_ludd.azure.containerapp_startup_diagnosis" in tasks
    assert "{{ _cad_app_id }}/revisions" in tasks
    assert "containerapp_revision_name }}/replicas" in tasks
    assert "gludd_azure_containerapp:" in tasks
    assert 'diagnosis: "{{ _cad_diagnosis | default(None) }}"' in tasks
    assert "general_ludd.azure.containerapp_idle_retention" in tasks
    assert "retention: \"{{ _cad_retention | default(None) }}\"" in tasks
    assert "checksum_algorithm: sha256" in tasks
    assert "poll: \"{{ containerapp_poll_seconds }}\"" in tasks
    assert "check_destroy: true" in tasks
    assert "ansible.builtin.command:" not in tasks
    assert "ansible.builtin.shell:" not in tasks
    assert "azure_rm_resource:" not in tasks
    assert defaults["containerapp_parallelism"] <= 4
    assert defaults["containerapp_poll_seconds"] > 0
    assert defaults["containerapp_iac_binary_path"] == "/usr/local/bin/tofu"
    assert defaults["containerapp_idle_retention"] == {}
    assert defaults["containerapp_required_replicas"] == 1
    assert "containerapp_terraform_binary_path" not in defaults
    assert galaxy["dependencies"]["azure.azcollection"]
    assert galaxy["dependencies"]["cloud.terraform"]


def test_role_docs_record_sdk_reuse_fact_semantics_and_practitioner_failures() -> None:
    readme = (ROLE / "README.md").read_text(encoding="utf-8")
    collection_readme = (COLLECTION / "README.md").read_text(encoding="utf-8")
    combined = f"{readme}\n{collection_readme}"

    assert "Terraform" in combined and "sole writer" in combined
    assert "azure.azcollection.azure_rm_resource_info" in combined
    assert "cloud.terraform" in combined
    assert "OpenTofu" in combined
    assert "MPL-2.0" in combined
    assert "gludd_azure_containerapp" in combined
    assert "ansible/ansible/issues/84750" in combined
    assert "ansible-collections/azure/issues/2191" in combined
    assert "ansible-collections/community.general/issues/7422" in combined
    assert "opentofu/opentofu/issues/3347" in combined
    assert "opentofu/opentofu/issues/4225" in combined
    assert "questions/5939955" in combined
