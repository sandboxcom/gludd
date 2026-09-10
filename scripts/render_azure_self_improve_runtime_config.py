#!/usr/bin/env python3
"""Render one private runtime config for a bounded mixed-model canary."""

from __future__ import annotations

import argparse
import hmac
import json
import os
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import cast

from general_ludd.cloud.azure_game_runtime import resolve_public_ipv4_cidr
from general_ludd.infra.azure_containerapp_gpu import (
    ModelServingRequirement,
    select_smallest_sufficient_profile,
)
from general_ludd.infra.azure_containerapp_topology import AzureProfileCapacity
from general_ludd.infra.azure_idle_retention import AzureRetentionPreset
from general_ludd.self_improve.azure_model_selection import (
    AzureModelSelectionReason,
    azure_model_deployment_identity_digest,
)

_MAX_SELECTION_BYTES = 65_536
_SELECTION_KEYS = frozenset(
    {
        "container_image",
        "context_tokens",
        "kv_cache_mib",
        "max_hourly_cost_microusd",
        "model_id",
        "parameter_count",
        "profile_capacities",
        "revision",
        "runtime_overhead_mib",
        "schema_version",
        "selection_identity_digest",
        "selection_reason",
        "weight_bits",
        "workload_profile_type",
    }
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    authentication = parser.add_mutually_exclusive_group(required=True)
    authentication.add_argument("--auth-file")
    authentication.add_argument("--federated-token-file")
    parser.add_argument("--azure-client-id")
    parser.add_argument("--azure-tenant-id")
    parser.add_argument("--model-selection-file", required=True, type=Path)
    parser.add_argument("--subscription-id", required=True)
    parser.add_argument("--resource-group", required=True)
    parser.add_argument("--environment", required=True)
    parser.add_argument("--location", required=True)
    parser.add_argument("--allowed-cidr", required=True)
    parser.add_argument("--max-cost-usd", required=True, type=float)
    parser.add_argument("--ttl-minutes", required=True, type=int)
    parser.add_argument("--acknowledgement", required=True)
    parser.add_argument(
        "--idle-retention-preset",
        required=True,
        choices=tuple(preset.value for preset in AzureRetentionPreset),
    )
    parser.add_argument("--idle-retention-seconds", required=True, type=int)
    parser.add_argument("--output", required=True, type=Path)
    return parser


def _authentication(args: argparse.Namespace) -> dict[str, str]:
    if args.auth_file:
        if args.azure_client_id or args.azure_tenant_id:
            raise ValueError("file authentication cannot include workload identity")
        return {"auth_file": cast(str, args.auth_file)}
    if not args.azure_client_id or not args.azure_tenant_id:
        raise ValueError("workload identity requires client and tenant IDs")
    return {
        "client_id": cast(str, args.azure_client_id),
        "tenant_id": cast(str, args.azure_tenant_id),
        "federated_token_file": cast(str, args.federated_token_file),
    }


def _selection_integer(
    selection: Mapping[str, object],
    name: str,
    *,
    minimum: int,
) -> int:
    value = selection.get(name)
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{name} is invalid")
    return value


def _validated_model_selection(path: Path) -> Mapping[str, object]:
    try:
        if path.stat().st_size > _MAX_SELECTION_BYTES:
            raise ValueError("artifact is too large")
        selection = json.loads(path.read_text(encoding="utf-8"))
        if (
            not isinstance(selection, Mapping)
            or set(selection) != _SELECTION_KEYS
            or selection.get("schema_version") != 1
        ):
            raise ValueError("artifact schema is invalid")
        requirement = ModelServingRequirement(
            model_id=cast(str, selection["model_id"]),
            revision=cast(str, selection["revision"]),
            parameter_count=_selection_integer(
                selection, "parameter_count", minimum=1
            ),
            weight_bits=_selection_integer(selection, "weight_bits", minimum=1),
            kv_cache_mib=_selection_integer(selection, "kv_cache_mib", minimum=0),
            runtime_overhead_mib=_selection_integer(
                selection, "runtime_overhead_mib", minimum=0
            ),
        )
        context_tokens = _selection_integer(selection, "context_tokens", minimum=8192)
        if context_tokens > 10_000_000:
            raise ValueError("context_tokens is invalid")
        raw_capacities = selection["profile_capacities"]
        if not isinstance(raw_capacities, list) or not raw_capacities:
            raise ValueError("profile_capacities is invalid")
        capacities = tuple(
            AzureProfileCapacity.from_payload(raw) for raw in raw_capacities
        )
        if len({item.workload_profile_type for item in capacities}) != len(
            capacities
        ):
            raise ValueError("profile_capacities contains duplicates")
        selected = select_smallest_sufficient_profile(
            requirement,
            hardware_profiles=tuple(capacity.profile for capacity in capacities),
        )
        workload_profile_type = selection["workload_profile_type"]
        if workload_profile_type != selected.profile.workload_profile_type:
            raise ValueError("workload_profile_type does not match model fit")
        AzureModelSelectionReason(cast(str, selection["selection_reason"]))
        expected_identity = azure_model_deployment_identity_digest(
            model_id=requirement.model_id,
            model_revision=requirement.revision,
            weight_bits=requirement.weight_bits,
            container_image=cast(str, selection["container_image"]),
            workload_profile_type=selected.profile.workload_profile_type,
        )
        identity = selection["selection_identity_digest"]
        if not isinstance(identity, str) or not hmac.compare_digest(
            expected_identity, identity
        ):
            raise ValueError("selection identity does not match")
        max_cost = _selection_integer(
            selection, "max_hourly_cost_microusd", minimum=1
        )
        selected_cost = next(
            capacity.hourly_cost_microusd_per_replica
            for capacity in capacities
            if capacity.workload_profile_type
            == selected.profile.workload_profile_type
        )
        if selected_cost > max_cost:
            raise ValueError("selected profile exceeds hourly cost policy")
        return {
            **selection,
            "profile_capacities": [capacity.payload() for capacity in capacities],
        }
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
        raise ValueError("model selection artifact is invalid") from None


def build_runtime_config(args: argparse.Namespace) -> Mapping[str, object]:
    """Build one exact-schema coding canary configuration."""
    requested_cidr = cast(str, args.allowed_cidr)
    allowed_cidr = (
        resolve_public_ipv4_cidr()
        if requested_cidr.casefold() == "auto"
        else requested_cidr
    )
    model = _validated_model_selection(args.model_selection_file)
    context_tokens = cast(int, model["context_tokens"])
    max_output_tokens = min(4_096, context_tokens // 4)
    max_input_tokens = min(24_576, context_tokens - max_output_tokens)
    azure: dict[str, object] = {
        "schema_version": 1,
        "enabled": True,
        "acknowledgement": cast(str, args.acknowledgement),
        **_authentication(args),
        "subscription_id": cast(str, args.subscription_id),
        "resource_group": cast(str, args.resource_group),
        "environment_name": cast(str, args.environment),
        "location": cast(str, args.location),
        "allowed_cidr": allowed_cidr,
        "container_image": model["container_image"],
        "model_name": model["model_id"],
        "model_revision": model["revision"],
        "parameter_count": model["parameter_count"],
        "weight_bits": model["weight_bits"],
        "kv_cache_mib": model["kv_cache_mib"],
        "runtime_overhead_mib": model["runtime_overhead_mib"],
        "peak_concurrency": 1,
        "per_replica_concurrency": 1,
        "profile_capacities": model["profile_capacities"],
        "max_hourly_cost_microusd": model["max_hourly_cost_microusd"],
        "max_cost_usd": cast(float, args.max_cost_usd),
        "ttl_minutes": cast(int, args.ttl_minutes),
        "max_input_tokens": max_input_tokens,
        "max_output_tokens": max_output_tokens,
        "max_total_tokens": max_input_tokens + max_output_tokens,
        "max_cost_microusd": 500_000,
        "timeout_seconds": 600.0,
        "estimated_request_cost_microusd": 0,
        "idle_retention": {
            "schema_version": 1,
            "preset": cast(str, args.idle_retention_preset),
            "max_idle_hourly_cost_microusd": 0,
            "max_idle_monthly_cost_microusd": 0,
            "max_retention_cost_microusd": 0,
            "max_retention_seconds": cast(int, args.idle_retention_seconds),
            "max_price_age_seconds": 31_536_000,
            "max_latency_age_seconds": 86_400,
            "max_cost_per_saved_hour_microusd": 0,
            "expected_next_demand_seconds": None,
        },
    }
    return {"azure_containerapp": azure}


def write_runtime_config(output: Path, config: Mapping[str, object]) -> None:
    """Create one mode-0600 output without following or replacing a path."""
    try:
        descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except OSError:
        raise ValueError("runtime configuration needs a new private output path") from None
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            descriptor = -1
            json.dump(config, stream, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
            stream.write("\n")
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def main(argv: Sequence[str] | None = None) -> int:
    """Render the validated private configuration and report no secret data."""
    parser = _parser()
    args = parser.parse_args(argv)
    if args.federated_token_file and (
        not args.azure_client_id or not args.azure_tenant_id
    ):
        parser.error("workload identity requires client and tenant IDs")
    write_runtime_config(args.output, build_runtime_config(args))
    print("AZURE_SELF_IMPROVE_CONFIG_WRITTEN secret_output=false", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
