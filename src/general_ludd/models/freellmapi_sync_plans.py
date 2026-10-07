"""Validate migration, test-reuse, live, and ZDD sync plans."""

from __future__ import annotations

from collections.abc import Mapping

from general_ludd.models.freellmapi_sync_contracts import (
    FreeLLMAPISyncFault,
    exact_mapping,
    fail_sync,
    nonempty,
    positive_int,
    string_list,
)


def _normalise_schema_plan(value: object) -> dict[str, object]:
    fault = FreeLLMAPISyncFault.SCHEMA_PLAN_INVALID
    schema = exact_mapping(
        value,
        keys={"from_version", "to_version", "compatibility"},
        fault=fault,
    )
    from_version = positive_int(schema["from_version"], fault)
    to_version = positive_int(schema["to_version"], fault)
    if (
        to_version <= from_version
        or to_version > 2_147_483_647
        or schema["compatibility"] != "backward"
    ):
        fail_sync(fault)
    return {
        "from_version": from_version,
        "to_version": to_version,
        "compatibility": "backward",
    }


def _normalise_migration_plan(value: object) -> dict[str, object]:
    fault = FreeLLMAPISyncFault.MIGRATION_PLAN_INVALID
    migration = exact_mapping(
        value,
        keys={"strategy", "steps", "destructive", "contract_deferred"},
        fault=fault,
    )
    steps = ["expand", "dual_read", "backfill", "cutover"]
    if (
        migration["strategy"] != "expand-migrate-contract"
        or migration["steps"] != steps
        or migration["destructive"] is not False
        or migration["contract_deferred"] is not True
    ):
        fail_sync(fault)
    return {
        "strategy": "expand-migrate-contract",
        "steps": steps,
        "destructive": False,
        "contract_deferred": True,
    }


def _normalise_test_reuse_plan(value: object) -> dict[str, object]:
    fault = FreeLLMAPISyncFault.TEST_REUSE_PLAN_INVALID
    plan = exact_mapping(
        value,
        keys={
            "gludd_suites",
            "upstream_suites",
            "upstream_source",
            "copied_upstream_tests",
        },
        fault=fault,
    )
    if (
        plan["upstream_source"] != "ephemeral_verified_archive"
        or plan["copied_upstream_tests"] is not False
    ):
        fail_sync(fault)
    return {
        "gludd_suites": string_list(plan["gludd_suites"], fault),
        "upstream_suites": string_list(plan["upstream_suites"], fault),
        "upstream_source": "ephemeral_verified_archive",
        "copied_upstream_tests": False,
    }


def normalise_plans(value: object) -> dict[str, object]:
    """Validate schema, non-destructive migration, and test-reuse plans."""
    plans = exact_mapping(
        value,
        keys={"schema", "migration", "test_reuse"},
        fault=FreeLLMAPISyncFault.SCHEMA_PLAN_INVALID,
    )
    return {
        "schema": _normalise_schema_plan(plans["schema"]),
        "migration": _normalise_migration_plan(plans["migration"]),
        "test_reuse": _normalise_test_reuse_plan(plans["test_reuse"]),
    }


def normalise_live_boundary(
    value: object,
    *,
    mode: object,
    allow_live: bool,
) -> dict[str, object]:
    """Require hermetic defaults or an explicit bounded metadata-only mode."""
    fault = FreeLLMAPISyncFault.LIVE_BOUNDARY_INVALID
    boundary = exact_mapping(
        value,
        keys={
            "network_enabled",
            "max_requests",
            "timeout_seconds",
            "max_response_bytes",
            "download_artifacts",
            "mutate_config",
        },
        fault=fault,
    )
    if mode == "offline":
        expected: dict[str, object] = {
            "network_enabled": False,
            "max_requests": 0,
            "timeout_seconds": 0,
            "max_response_bytes": 0,
            "download_artifacts": False,
            "mutate_config": False,
        }
        if dict(boundary) != expected:
            fail_sync(fault)
        return expected
    if mode != "live":
        fail_sync(fault)
    if allow_live is not True:
        fail_sync(FreeLLMAPISyncFault.LIVE_MODE_NOT_ADMITTED)
    request_count = positive_int(boundary["max_requests"], fault)
    timeout = positive_int(boundary["timeout_seconds"], fault)
    response_bytes = positive_int(boundary["max_response_bytes"], fault)
    if (
        boundary["network_enabled"] is not True
        or request_count > 8
        or timeout > 30
        or response_bytes > 1_048_576
        or boundary["download_artifacts"] is not False
        or boundary["mutate_config"] is not False
    ):
        fail_sync(fault)
    return {
        "network_enabled": True,
        "max_requests": request_count,
        "timeout_seconds": timeout,
        "max_response_bytes": response_bytes,
        "download_artifacts": False,
        "mutate_config": False,
    }


def normalise_zdd(value: object, baseline: Mapping[str, object]) -> dict[str, object]:
    """Bind rollback to the retained baseline without pre-admission writes."""
    fault = FreeLLMAPISyncFault.ZDD_ROLLBACK_INVALID
    zdd = exact_mapping(
        value,
        keys={
            "strategy",
            "baseline_commit",
            "baseline_tree",
            "baseline_artifact_sha256",
            "rollback_test_id",
            "rollback_ready",
            "no_downtime",
            "state_write_before_admission",
        },
        fault=fault,
    )
    upstream = baseline["upstream"]
    supply = baseline["supply_chain"]
    assert isinstance(upstream, Mapping)
    assert isinstance(supply, Mapping)
    if (
        zdd["strategy"] != "dual-read-shadow"
        or zdd["baseline_commit"] != upstream["commit"]
        or zdd["baseline_tree"] != upstream["tree"]
        or zdd["baseline_artifact_sha256"] != supply["artifact_sha256"]
        or zdd["rollback_ready"] is not True
        or zdd["no_downtime"] is not True
        or zdd["state_write_before_admission"] is not False
    ):
        fail_sync(fault)
    return {
        "strategy": "dual-read-shadow",
        "baseline_commit": upstream["commit"],
        "baseline_tree": upstream["tree"],
        "baseline_artifact_sha256": supply["artifact_sha256"],
        "rollback_test_id": nonempty(zdd["rollback_test_id"], fault),
        "rollback_ready": True,
        "no_downtime": True,
        "state_write_before_admission": False,
    }
