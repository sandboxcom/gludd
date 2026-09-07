"""Sanitize Azure Container Apps observations and select lifecycle actions.

The filters operate only on results returned by read-only Ansible information
modules. Terraform remains the sole resource writer; this module decides only
whether an already audited Terraform plan should be applied or an owned stack
should be destroyed.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from typing import Any, Final

_PROTOCOL: Final = "gludd-azure-containerapp-observation-v1"
_GPU_METRIC: Final = "GpuUtilizationPercentage"
_MAX_ITEMS: Final = 512
_DIGEST = re.compile(r"[0-9a-f]{64}")
_SENSITIVE_KEY = re.compile(
    r"(?:clientsecret|secret|token|password|credential|accesskey|apikey)",
    re.IGNORECASE,
)


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be a mapping")
    return value


def _text(value: object, label: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value):
        raise ValueError(f"{label} must be a non-empty string")
    return value


def _integer(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a number")
    integer = int(value)
    if integer != value or integer < 0:
        raise ValueError(f"{label} must be a non-negative integer")
    return integer


def _assert_safe_tree(value: object, *, depth: int = 0) -> None:
    if depth > 16:
        raise ValueError("Azure observation nesting is not bounded")
    if isinstance(value, Mapping):
        if len(value) > _MAX_ITEMS:
            raise ValueError("Azure observation mapping is not bounded")
        for raw_key, child in value.items():
            key = str(raw_key)
            if _SENSITIVE_KEY.search(key):
                raise ValueError("Azure observation contains secret-bearing fields")
            if key.lower() in {"nextlink", "next_page_link"} and child:
                raise ValueError("Azure observation pagination is incomplete")
            _assert_safe_tree(child, depth=depth + 1)
        return
    if isinstance(value, Sequence) and not isinstance(
        value,
        (str, bytes, bytearray),
    ):
        if len(value) > _MAX_ITEMS:
            raise ValueError("Azure observation collection is not bounded")
        for child in value:
            _assert_safe_tree(child, depth=depth + 1)


def _values(result: object, label: str) -> tuple[Mapping[str, object], ...]:
    if result is None:
        return ()
    outer = _mapping(result, label)
    response = outer.get("response", outer)
    # Registered Ansible results may include invocation metadata naming no_log
    # credential parameters.  Provider response data is the only evidence this
    # filter consumes or retains, so apply the content guard at that boundary.
    _assert_safe_tree(response)
    raw: object
    if isinstance(response, Sequence) and not isinstance(
        response,
        (str, bytes, bytearray),
    ):
        raw = response
    else:
        raw = _mapping(response, f"{label}.response").get("value", ())
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes, bytearray)):
        raise ValueError(f"{label}.value must be a bounded list")
    if len(raw) > _MAX_ITEMS:
        raise ValueError(f"{label}.value must be bounded")
    return tuple(_mapping(item, f"{label}.value item") for item in raw)


def _same_id(observed: object, expected: str) -> bool:
    return isinstance(observed, str) and observed.rstrip("/").casefold() == expected.rstrip(
        "/"
    ).casefold()


def _contract(raw: object) -> dict[str, object]:
    value = _mapping(raw, "containerapp contract")
    required: dict[str, object] = {
        key: _text(value.get(key), key, allow_empty=key == "revision_name")
        for key in (
            "subscription_id",
            "resource_group",
            "environment_name",
            "environment_id",
            "app_name",
            "app_id",
            "workload_profile_name",
            "workload_profile_type",
            "revision_name",
            "owner_digest",
        )
    }
    if _DIGEST.fullmatch(str(required["owner_digest"])) is None:
        raise ValueError("owner_digest must be one sha256 digest")
    required["required_profile_headroom"] = _integer(
        value.get("required_profile_headroom", 1),
        "required_profile_headroom",
    )
    return required


def _select_environment(
    values: tuple[Mapping[str, object], ...],
    contract: Mapping[str, object],
) -> Mapping[str, object] | None:
    expected_name = str(contract["environment_name"])
    expected_id = str(contract["environment_id"])
    candidates = [item for item in values if item.get("name") == expected_name]
    if len(candidates) > 1:
        raise ValueError("environment inventory is ambiguous")
    if not candidates:
        return None
    selected = candidates[0]
    if not _same_id(selected.get("id"), expected_id):
        raise ValueError("environment scope does not match the approved resource")
    return selected


def _app_inventory(
    values: tuple[Mapping[str, object], ...],
    contract: Mapping[str, object],
) -> tuple[Mapping[str, object] | None, tuple[str, ...]]:
    environment_id = str(contract["environment_id"])
    app_id = str(contract["app_id"])
    app_name = str(contract["app_name"])
    selected: Mapping[str, object] | None = None
    foreign: list[str] = []
    for item in values:
        properties = _mapping(item.get("properties", {}), "app.properties")
        item_id = _text(item.get("id"), "app.id")
        item_environment = properties.get("managedEnvironmentId")
        is_expected_app = item.get("name") == app_name or _same_id(item_id, app_id)
        if is_expected_app and not _same_id(item_environment, environment_id):
            raise ValueError("app environment does not match the approved environment")
        if not _same_id(item_environment, environment_id):
            continue
        if _same_id(item_id, app_id):
            if selected is not None:
                raise ValueError("app inventory is ambiguous")
            selected = item
        else:
            foreign.append(item_id)
    return selected, tuple(sorted(foreign, key=str.casefold))


def _tags_owned(item: Mapping[str, object], owner_digest: str) -> bool:
    tags = item.get("tags", {})
    return isinstance(tags, Mapping) and tags.get("gludd-owner") == owner_digest


def _normalized_profile_token(value: object) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).casefold())


def _capacity(
    environment: Mapping[str, object] | None,
    usages: tuple[Mapping[str, object], ...],
    states: tuple[Mapping[str, object], ...],
    contract: Mapping[str, object],
) -> dict[str, object]:
    profile_name = str(contract["workload_profile_name"])
    profile_type = str(contract["workload_profile_type"])
    empty: dict[str, object] = {
        "profile_name": profile_name,
        "profile_type": profile_type,
        "current_nodes": None,
        "maximum_nodes": None,
        "quota_current": None,
        "quota_limit": None,
        "headroom": None,
        "sufficient": None,
    }
    if environment is None:
        return empty
    properties = _mapping(environment.get("properties", {}), "environment.properties")
    raw_profiles = properties.get("workloadProfiles", ())
    if not isinstance(raw_profiles, Sequence) or isinstance(
        raw_profiles,
        (str, bytes, bytearray),
    ):
        raise ValueError("environment workload profiles must be bounded")
    profiles = [
        _mapping(item, "workload profile")
        for item in raw_profiles
        if isinstance(item, Mapping) and item.get("name") == profile_name
    ]
    if len(profiles) != 1:
        raise ValueError("approved workload profile is absent or ambiguous")
    if profiles[0].get("workloadProfileType") != profile_type:
        raise ValueError("approved workload profile type drifted")

    matching_states = [item for item in states if item.get("name") == profile_name]
    target_usage = _normalized_profile_token(profile_type)
    matching_usages = []
    for item in usages:
        name = item.get("name", {})
        name_value = name.get("value") if isinstance(name, Mapping) else name
        if _normalized_profile_token(name_value) == target_usage:
            matching_usages.append(item)
    if len(matching_states) != 1 or len(matching_usages) != 1:
        raise ValueError("workload profile capacity evidence is absent or ambiguous")
    state_properties = _mapping(
        matching_states[0].get("properties", {}),
        "workload profile state",
    )
    current_nodes = _integer(state_properties.get("currentCount"), "currentCount")
    maximum_nodes = _integer(state_properties.get("maximumCount"), "maximumCount")
    quota_current = _integer(matching_usages[0].get("currentValue"), "currentValue")
    quota_limit = _integer(matching_usages[0].get("limit"), "limit")
    headroom = min(maximum_nodes - current_nodes, quota_limit - quota_current)
    if headroom < 0:
        raise ValueError("workload profile capacity evidence is impossible")
    required = _integer(
        contract.get("required_profile_headroom"),
        "required_profile_headroom",
    )
    return {
        "profile_name": profile_name,
        "profile_type": profile_type,
        "current_nodes": current_nodes,
        "maximum_nodes": maximum_nodes,
        "quota_current": quota_current,
        "quota_limit": quota_limit,
        "headroom": headroom,
        "sufficient": headroom >= required,
    }


def _gpu_evidence(
    metrics: tuple[Mapping[str, object], ...],
    revision_name: str,
) -> dict[str, object]:
    empty: dict[str, object] = {
        "metric_name": _GPU_METRIC,
        "revision_name": revision_name,
        "maximum_percent": None,
        "positive_sample_count": 0,
        "observed": False,
    }
    if not metrics:
        return empty
    if not revision_name:
        raise ValueError("metric revision is required")
    if len(metrics) != 1:
        raise ValueError("GPU metric response is ambiguous")
    metric = metrics[0]
    name = metric.get("name", {})
    name_value = name.get("value") if isinstance(name, Mapping) else name
    if name_value != _GPU_METRIC:
        raise ValueError("metric name is not the approved GPU metric")
    raw_series = metric.get("timeseries", ())
    if not isinstance(raw_series, Sequence) or isinstance(
        raw_series,
        (str, bytes, bytearray),
    ):
        raise ValueError("GPU metric timeseries must be bounded")
    values: list[float] = []
    for raw in raw_series:
        series = _mapping(raw, "GPU metric series")
        metadata = series.get("metadatavalues", ())
        if not isinstance(metadata, Sequence) or isinstance(
            metadata,
            (str, bytes, bytearray),
        ):
            raise ValueError("GPU metric dimensions must be bounded")
        dimensions: dict[str, object] = {}
        for dimension_raw in metadata:
            dimension = _mapping(dimension_raw, "GPU metric dimension")
            dimension_name = dimension.get("name", {})
            key = (
                dimension_name.get("value")
                if isinstance(dimension_name, Mapping)
                else dimension_name
            )
            if isinstance(key, str):
                dimensions[key] = dimension.get("value")
        if dimensions.get("revisionName") != revision_name:
            raise ValueError("metric revision does not match the approved revision")
        data = series.get("data", ())
        if not isinstance(data, Sequence) or isinstance(
            data,
            (str, bytes, bytearray),
        ):
            raise ValueError("GPU metric samples must be bounded")
        for point_raw in data:
            point = _mapping(point_raw, "GPU metric point")
            sample = point.get("maximum")
            if sample is None:
                continue
            if isinstance(sample, bool) or not isinstance(sample, (int, float)):
                raise ValueError("GPU metric sample is invalid")
            numeric = float(sample)
            if not math.isfinite(numeric) or not 0.0 <= numeric <= 100.0:
                raise ValueError("GPU metric sample is outside the percent range")
            values.append(numeric)
            if len(values) > _MAX_ITEMS:
                raise ValueError("GPU metric sample collection must be bounded")
    if not values:
        return empty
    positive = tuple(value for value in values if value > 0.0)
    return {
        "metric_name": _GPU_METRIC,
        "revision_name": revision_name,
        "maximum_percent": max(values),
        "positive_sample_count": len(positive),
        "observed": bool(positive),
    }


def normalize_containerapp_observation(
    raw: object,
    contract: object,
) -> dict[str, object]:
    """Return one bounded, secret-free observation for the approved resources."""
    source = _mapping(raw, "containerapp observation")
    expected = _contract(contract)
    environments = _values(source.get("environments"), "environments")
    apps = _values(source.get("apps"), "apps")
    usages = _values(source.get("usages"), "usages") if source.get("usages") else ()
    states = (
        _values(source.get("profile_states"), "profile_states")
        if source.get("profile_states")
        else ()
    )
    metrics = _values(source.get("metrics"), "metrics") if source.get("metrics") else ()
    environment = _select_environment(environments, expected)
    app, foreign_apps = _app_inventory(apps, expected)
    owner = str(expected["owner_digest"])
    environment_properties = (
        {}
        if environment is None
        else _mapping(environment.get("properties", {}), "environment.properties")
    )
    app_properties = (
        {} if app is None else _mapping(app.get("properties", {}), "app.properties")
    )
    revision_name = str(expected["revision_name"])
    observed_revision = app_properties.get("latestReadyRevisionName")
    if revision_name and app is not None and observed_revision != revision_name:
        raise ValueError("app revision does not match the approved revision")
    if not revision_name and isinstance(observed_revision, str):
        revision_name = observed_revision
    environment_state = environment_properties.get("provisioningState")
    app_state = app_properties.get("provisioningState")
    return {
        "protocol": _PROTOCOL,
        "environment": {
            "exists": environment is not None,
            "id": None if environment is None else expected["environment_id"],
            "owned": False
            if environment is None
            else _tags_owned(environment, owner),
            "provisioning_state": environment_state,
            "ready": environment_state == "Succeeded",
        },
        "capacity": _capacity(environment, usages, states, expected),
        "app": {
            "exists": app is not None,
            "id": None if app is None else expected["app_id"],
            "owned": False if app is None else _tags_owned(app, owner),
            "provisioning_state": app_state,
            "ready_revision_name": observed_revision,
            "ready": app_state == "Succeeded" and observed_revision == revision_name,
        },
        "gpu": _gpu_evidence(metrics, revision_name),
        "foreign_app_ids": list(foreign_apps),
    }


def _digest(value: object, label: str) -> str:
    text = _text(value, label)
    if _DIGEST.fullmatch(text) is None:
        raise ValueError(f"{label} must be one sha256 digest")
    return text


def decide_containerapp_lifecycle(
    observation: object,
    request: object,
) -> dict[str, object]:
    """Choose an observable Terraform-only lifecycle transition."""
    observed = _mapping(observation, "containerapp observation")
    desired = _mapping(request, "containerapp lifecycle request")
    state = desired.get("state")
    if state not in {"observe", "planned", "present", "absent"}:
        raise ValueError("state must be observe, planned, present, or absent")
    if state == "observe":
        return {
            "action": "observe",
            "requires_terraform": False,
            "reason": "observation_requested",
        }
    if state == "planned":
        return {
            "action": "plan",
            "requires_terraform": False,
            "reason": "audited_plan_requested",
        }
    environment = _mapping(observed.get("environment"), "environment observation")
    app = _mapping(observed.get("app"), "app observation")
    if state == "absent":
        environment_exists = bool(environment.get("exists"))
        app_exists = bool(app.get("exists"))
        if environment_exists and not environment.get("owned"):
            raise ValueError("environment is not owned by the approved identity")
        if app_exists and not app.get("owned"):
            raise ValueError("app is not owned by the approved identity")
        if not environment_exists and not app_exists:
            return {
                "action": "noop",
                "requires_terraform": False,
                "reason": "desired_resources_absent",
            }
        foreign = observed.get("foreign_app_ids", ())
        if foreign:
            raise ValueError("environment contains foreign apps")
        return {
            "action": "destroy",
            "requires_terraform": True,
            "reason": "owned_resources_present",
            "operation_digest": _digest(
                desired.get("operation_digest"),
                "operation_digest",
            ),
        }
    if environment.get("exists") and not environment.get("owned"):
        raise ValueError("environment is not owned by the approved identity")
    if app.get("exists") and not app.get("owned"):
        raise ValueError("app is not owned by the approved identity")
    capacity = _mapping(observed.get("capacity"), "capacity observation")
    if environment.get("exists") and capacity.get("sufficient") is not True:
        raise ValueError("approved workload profile has insufficient capacity")
    if environment.get("ready") and app.get("ready"):
        return {
            "action": "noop",
            "requires_terraform": False,
            "reason": "desired_resources_ready",
        }
    if desired.get("plan_audited") is not True:
        raise ValueError("Terraform plan must be audited before apply")
    return {
        "action": "apply",
        "requires_terraform": True,
        "reason": "desired_resources_absent"
        if not environment.get("exists") and not app.get("exists")
        else "desired_resources_not_ready",
        "operation_digest": _digest(
            desired.get("operation_digest"),
            "operation_digest",
        ),
        "terraform_plan_sha256": _digest(
            desired.get("terraform_plan_sha256"),
            "terraform_plan_sha256",
        ),
    }


class FilterModule:
    """Expose the lifecycle filters to Ansible."""

    def filters(self) -> dict[str, Any]:
        return {
            "containerapp_observation": normalize_containerapp_observation,
            "containerapp_lifecycle_decision": decide_containerapp_lifecycle,
        }


__all__ = (
    "FilterModule",
    "decide_containerapp_lifecycle",
    "normalize_containerapp_observation",
)
