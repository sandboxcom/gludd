"""Deep contracts for the collection-local Container Apps retention planner."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest
from ansible_collections.general_ludd.azure.plugins.module_utils.containerapp_retention import (
    AzureIdleRetentionPlan,
    AzureIdleRetentionPolicy,
    AzureProvisioningLatencyEvidence,
    AzureRetentionPreset,
    plan_container_apps_idle_retention,
)

NOW = datetime(2026, 9, 14, 12, tzinfo=UTC)
SCOPE = "d" * 64


def _latency(seconds: float, *, observed_at: datetime = NOW) -> AzureProvisioningLatencyEvidence:
    return AzureProvisioningLatencyEvidence(
        p50_seconds=seconds / 2,
        p95_seconds=seconds,
        sample_count=2,
        observed_at=observed_at,
    )


def _policy(
    preset: AzureRetentionPreset = AzureRetentionPreset.ZERO_COST_ONLY,
) -> AzureIdleRetentionPolicy:
    return AzureIdleRetentionPolicy(
        preset=preset,
        max_idle_hourly_cost_microusd=0,
        max_idle_monthly_cost_microusd=0,
        max_retention_cost_microusd=0,
        max_retention_seconds=21_600,
        max_price_age_seconds=31_536_000,
        max_latency_age_seconds=86_400,
        max_cost_per_saved_hour_microusd=0,
    )


def _plan(**overrides: object) -> AzureIdleRetentionPlan:
    values: dict[str, object] = {
        "policy": _policy(),
        "scope_digest": SCOPE,
        "now": NOW,
        "environment_latency": _latency(960),
        "app_latency": _latency(240),
        "min_replicas": 0,
        "activation_blocked_when_idle": True,
        "has_dedicated_profiles": False,
        "has_private_endpoint": False,
        "has_planned_maintenance": False,
        "has_paid_logging": False,
        "runnable_todo_count": 0,
        "expected_next_demand_seconds": 1_800,
    }
    values.update(overrides)
    return plan_container_apps_idle_retention(**cast(Any, values))


def test_zero_cost_idle_shape_retains_both_layers_deterministically() -> None:
    first = _plan()
    second = _plan()

    assert [item.value for item in first.retained_layers] == [
        "managed_environment",
        "container_app",
    ]
    assert first.destroyed_layers == ()
    assert first.retention_seconds == 1_800
    assert first.reconcile_at == NOW + timedelta(seconds=1_800)
    assert first.hourly_cost_microusd == 0
    assert first.monthly_cost_microusd == 0
    assert first.projected_cost_microusd == 0
    assert first.p95_seconds_saved == 1_200
    assert first.plan_digest == second.plan_digest
    assert len(first.plan_digest) == 64


@pytest.mark.parametrize(
    ("overrides", "retained", "app_reason"),
    [
        (
            {"activation_blocked_when_idle": False},
            ["managed_environment"],
            "idle_activation_possible",
        ),
        ({"min_replicas": 1}, ["managed_environment"], "idle_compute_forbidden"),
        ({"has_dedicated_profiles": True}, [], "budget_tradeoff"),
        ({"has_private_endpoint": True}, [], "budget_tradeoff"),
        ({"has_planned_maintenance": True}, [], "budget_tradeoff"),
        ({"has_paid_logging": True}, [], "budget_tradeoff"),
    ],
)
def test_paid_or_active_shapes_fail_closed_by_layer(
    overrides: dict[str, object],
    retained: list[str],
    app_reason: str,
) -> None:
    plan = _plan(**overrides)

    assert [item.value for item in plan.retained_layers] == retained
    assert plan.decisions[1].reason.value == app_reason


@pytest.mark.parametrize(
    "preset",
    [
        AzureRetentionPreset.ALWAYS_DESTROY,
        AzureRetentionPreset.ZERO_COST_ONLY,
        AzureRetentionPreset.BALANCED,
        AzureRetentionPreset.LATENCY_FIRST,
    ],
)
def test_all_reviewed_presets_have_bounded_deterministic_outcomes(
    preset: AzureRetentionPreset,
) -> None:
    plan = _plan(policy=_policy(preset))

    if preset is AzureRetentionPreset.ALWAYS_DESTROY:
        assert plan.retained_layers == ()
        assert {item.reason.value for item in plan.decisions} == {"preset_destroy"}
    else:
        assert len(plan.retained_layers) == 2


def test_demand_beyond_window_destroys_every_layer() -> None:
    plan = _plan(expected_next_demand_seconds=21_601)

    assert plan.retained_layers == ()
    assert plan.retention_seconds == 0
    assert plan.reconcile_at == NOW
    assert {item.reason.value for item in plan.decisions} == {"demand_outside_window"}


@pytest.mark.parametrize(
    ("observed_at", "reason"),
    [
        (NOW - timedelta(seconds=86_401), "latency_evidence_stale"),
        (NOW + timedelta(seconds=1), "evidence_from_future"),
    ],
)
def test_invalid_latency_age_prevents_dependent_app_retention(
    observed_at: datetime,
    reason: str,
) -> None:
    plan = _plan(environment_latency=_latency(960, observed_at=observed_at))

    assert plan.retained_layers == ()
    assert plan.decisions[0].reason.value == reason
    assert plan.decisions[1].reason.value == "budget_tradeoff"


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"scope_digest": "not-a-digest"}, "scope_digest"),
        ({"runnable_todo_count": 1}, "empty durable todo"),
        ({"expected_next_demand_seconds": 0}, "expected_next_demand_seconds"),
        ({"min_replicas": -1}, "min_replicas"),
        ({"has_paid_logging": 1}, "flags must be boolean"),
    ],
)
def test_invalid_or_nonidle_requests_are_refused(
    overrides: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        _plan(**overrides)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"p50_seconds": 0}, "p50_seconds"),
        ({"p95_seconds": float("nan")}, "p95_seconds"),
        ({"p50_seconds": 20, "p95_seconds": 10}, "p95_seconds must be"),
        ({"sample_count": 0}, "sample_count"),
        ({"observed_at": datetime(2026, 9, 14)}, "timezone-aware"),
    ],
)
def test_latency_evidence_is_strictly_validated(
    kwargs: dict[str, object],
    message: str,
) -> None:
    values: dict[str, object] = {
        "p50_seconds": 10,
        "p95_seconds": 20,
        "sample_count": 1,
        "observed_at": NOW,
    }
    values.update(kwargs)
    with pytest.raises(ValueError, match=message):
        AzureProvisioningLatencyEvidence(**cast(Any, values))


@pytest.mark.parametrize(
    "field",
    [
        "max_idle_hourly_cost_microusd",
        "max_idle_monthly_cost_microusd",
        "max_retention_cost_microusd",
        "max_cost_per_saved_hour_microusd",
        "max_retention_seconds",
        "max_price_age_seconds",
        "max_latency_age_seconds",
    ],
)
def test_policy_rejects_invalid_ceilings(field: str) -> None:
    values = {
        "preset": AzureRetentionPreset.ZERO_COST_ONLY,
        "max_idle_hourly_cost_microusd": 0,
        "max_idle_monthly_cost_microusd": 0,
        "max_retention_cost_microusd": 0,
        "max_retention_seconds": 1,
        "max_price_age_seconds": 1,
        "max_latency_age_seconds": 1,
        "max_cost_per_saved_hour_microusd": 0,
    }
    values[field] = -1 if "seconds" not in field else 0
    with pytest.raises(ValueError, match=field):
        AzureIdleRetentionPolicy(**cast(Any, values))
