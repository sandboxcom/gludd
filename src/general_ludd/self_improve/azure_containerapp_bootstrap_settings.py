"""Exact configuration parser for self-owned Azure Container App candidates."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from general_ludd.infra.azure_containerapp_live_proof import (
    LIVE_PROOF_ACKNOWLEDGEMENT,
)
from general_ludd.infra.azure_containerapp_topology import AzureProfileCapacity
from general_ludd.infra.azure_idle_retention import (
    AzureIdleRetentionPolicy,
    AzureRetentionPreset,
)

_BASE_CONFIG_KEYS = frozenset(
    {
        "schema_version",
        "enabled",
        "acknowledgement",
        "subscription_id",
        "resource_group",
        "environment_name",
        "location",
        "allowed_cidr",
        "container_image",
        "model_name",
        "model_revision",
        "parameter_count",
        "weight_bits",
        "kv_cache_mib",
        "runtime_overhead_mib",
        "peak_concurrency",
        "per_replica_concurrency",
        "profile_capacities",
        "max_hourly_cost_microusd",
        "max_cost_usd",
        "ttl_minutes",
        "max_input_tokens",
        "max_output_tokens",
        "max_total_tokens",
        "max_cost_microusd",
        "timeout_seconds",
        "estimated_request_cost_microusd",
        "idle_retention",
    }
)
_FILE_AUTH_KEYS = _BASE_CONFIG_KEYS | {"auth_file"}
_WORKLOAD_IDENTITY_KEYS = _BASE_CONFIG_KEYS | {
    "client_id",
    "tenant_id",
    "federated_token_file",
}
_RETENTION_KEYS = frozenset(
    {
        "schema_version",
        "preset",
        "max_idle_hourly_cost_microusd",
        "max_idle_monthly_cost_microusd",
        "max_retention_cost_microusd",
        "max_retention_seconds",
        "max_price_age_seconds",
        "max_latency_age_seconds",
        "max_cost_per_saved_hour_microusd",
        "expected_next_demand_seconds",
    }
)


@dataclass(frozen=True, slots=True)
class AzureContainerAppBootstrapSettings:
    """Fully parsed, immutable Azure bootstrap settings."""

    auth_file: Path | None = field(repr=False)
    client_id: str | None
    tenant_id: str | None
    federated_token_file: Path | None = field(repr=False)
    subscription_id: str
    resource_group: str
    environment_name: str
    location: str
    allowed_cidr: str
    container_image: str
    model_name: str
    model_revision: str
    parameter_count: int
    weight_bits: int
    kv_cache_mib: int
    runtime_overhead_mib: int
    peak_concurrency: int
    per_replica_concurrency: int
    profile_capacities: tuple[AzureProfileCapacity, ...]
    max_hourly_cost_microusd: int
    max_cost_usd: float
    ttl_minutes: int
    max_input_tokens: int
    max_output_tokens: int
    max_total_tokens: int
    max_cost_microusd: int
    timeout_seconds: float
    estimated_request_cost_microusd: int
    idle_retention_policy: AzureIdleRetentionPolicy
    expected_next_demand_seconds: int | None


def _text(config: Mapping[str, object], name: str) -> str:
    value = config.get(name)
    if not isinstance(value, str) or not value or value.strip() != value:
        raise ValueError(f"{name} must be non-empty text")
    return value


def _integer(config: Mapping[str, object], name: str) -> int:
    value = config.get(name)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be a bounded integer")
    return value


def _number(config: Mapping[str, object], name: str) -> float:
    value = config.get(name)
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{name} must be a bounded number")
    return float(value)


def _profile_capacities(config: Mapping[str, object]) -> tuple[AzureProfileCapacity, ...]:
    raw = config.get("profile_capacities")
    if not isinstance(raw, list) or not raw:
        raise ValueError("profile_capacities must be a non-empty array")
    capacities = tuple(AzureProfileCapacity.from_payload(item) for item in raw)
    if len({item.workload_profile_type for item in capacities}) != len(capacities):
        raise ValueError("profile_capacities contains duplicate profile types")
    return capacities


def _absolute_path(config: Mapping[str, object], name: str) -> Path:
    path = Path(_text(config, name)).expanduser()
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError(f"{name} must be an absolute confined path")
    return path


def _authentication_settings(
    config: Mapping[str, object],
) -> tuple[Path | None, str | None, str | None, Path | None]:
    keys = set(config)
    if keys == _FILE_AUTH_KEYS:
        return _absolute_path(config, "auth_file"), None, None, None
    if keys == _WORKLOAD_IDENTITY_KEYS:
        return (
            None,
            _text(config, "client_id"),
            _text(config, "tenant_id"),
            _absolute_path(config, "federated_token_file"),
        )
    raise ValueError("azure_containerapp must use the exact schema")


def _parse_idle_retention(
    raw: object,
) -> tuple[AzureIdleRetentionPolicy, int | None]:
    if not isinstance(raw, Mapping) or set(raw) != _RETENTION_KEYS:
        raise ValueError("idle retention exact schema is required")
    if raw.get("schema_version") != 1 or isinstance(
        raw.get("schema_version"),
        bool,
    ):
        raise ValueError("idle retention schema_version must equal 1")
    try:
        preset = AzureRetentionPreset(_text(raw, "preset"))
    except ValueError:
        raise ValueError("idle retention preset is invalid") from None
    expected = raw.get("expected_next_demand_seconds")
    if expected is not None and (
        isinstance(expected, bool)
        or not isinstance(expected, int)
        or expected <= 0
    ):
        raise ValueError(
            "expected_next_demand_seconds must be a positive integer or null"
        )
    policy = AzureIdleRetentionPolicy(
        preset=preset,
        max_idle_hourly_cost_microusd=_integer(
            raw,
            "max_idle_hourly_cost_microusd",
        ),
        max_idle_monthly_cost_microusd=_integer(
            raw,
            "max_idle_monthly_cost_microusd",
        ),
        max_retention_cost_microusd=_integer(
            raw,
            "max_retention_cost_microusd",
        ),
        max_retention_seconds=_integer(raw, "max_retention_seconds"),
        max_price_age_seconds=_integer(raw, "max_price_age_seconds"),
        max_latency_age_seconds=_integer(raw, "max_latency_age_seconds"),
        max_cost_per_saved_hour_microusd=_integer(
            raw,
            "max_cost_per_saved_hour_microusd",
        ),
    )
    return policy, expected


def parse_azure_containerapp_bootstrap_settings(
    config: Mapping[str, object],
) -> AzureContainerAppBootstrapSettings:
    """Parse the exact enabled Azure bootstrap schema."""
    auth_file, client_id, tenant_id, federated_token_file = (
        _authentication_settings(config)
    )
    if config.get("schema_version") != 1 or isinstance(
        config.get("schema_version"), bool
    ):
        raise ValueError("azure_containerapp schema_version must equal 1")
    if config.get("enabled") is not True:
        raise ValueError("azure_containerapp enabled must be true")
    if config.get("acknowledgement") != LIVE_PROOF_ACKNOWLEDGEMENT:
        raise ValueError("azure_containerapp acknowledgement is invalid")
    retention_policy, expected_next_demand_seconds = _parse_idle_retention(
        config.get("idle_retention")
    )
    return AzureContainerAppBootstrapSettings(
        auth_file=auth_file,
        client_id=client_id,
        tenant_id=tenant_id,
        federated_token_file=federated_token_file,
        subscription_id=_text(config, "subscription_id"),
        resource_group=_text(config, "resource_group"),
        environment_name=_text(config, "environment_name"),
        location=_text(config, "location"),
        allowed_cidr=_text(config, "allowed_cidr"),
        container_image=_text(config, "container_image"),
        model_name=_text(config, "model_name"),
        model_revision=_text(config, "model_revision"),
        parameter_count=_integer(config, "parameter_count"),
        weight_bits=_integer(config, "weight_bits"),
        kv_cache_mib=_integer(config, "kv_cache_mib"),
        runtime_overhead_mib=_integer(config, "runtime_overhead_mib"),
        peak_concurrency=_integer(config, "peak_concurrency"),
        per_replica_concurrency=_integer(config, "per_replica_concurrency"),
        profile_capacities=_profile_capacities(config),
        max_hourly_cost_microusd=_integer(config, "max_hourly_cost_microusd"),
        max_cost_usd=_number(config, "max_cost_usd"),
        ttl_minutes=_integer(config, "ttl_minutes"),
        max_input_tokens=_integer(config, "max_input_tokens"),
        max_output_tokens=_integer(config, "max_output_tokens"),
        max_total_tokens=_integer(config, "max_total_tokens"),
        max_cost_microusd=_integer(config, "max_cost_microusd"),
        timeout_seconds=_number(config, "timeout_seconds"),
        estimated_request_cost_microusd=_integer(
            config,
            "estimated_request_cost_microusd",
        ),
        idle_retention_policy=retention_policy,
        expected_next_demand_seconds=expected_next_demand_seconds,
    )


__all__ = (
    "AzureContainerAppBootstrapSettings",
    "parse_azure_containerapp_bootstrap_settings",
)
