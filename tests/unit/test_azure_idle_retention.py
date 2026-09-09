"""Fail-closed Azure idle-layer retention planning."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pytest

from general_ludd.infra.azure_idle_retention import (
    AzureIdleCostEvidence,
    AzureIdleRetentionPolicy,
    AzureProvisioningLatencyEvidence,
    AzureRetentionDisposition,
    AzureRetentionEvidenceSource,
    AzureRetentionLayer,
    AzureRetentionLayerKind,
    AzureRetentionPreset,
    AzureRetentionReason,
    AzureRetentionTrace,
    AzureRetentionTraceEvent,
    container_apps_consumption_layers,
    idle_cost_evidence_from_retail_meters,
    plan_azure_idle_retention,
    plan_container_apps_idle_retention,
)
from general_ludd.infra.azure_retail_pricing import AzureRetailMeter

NOW = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)
METER = "11111111-2222-3333-4444-555555555555"
ROOT = Path(__file__).resolve().parents[2]


def _cost(
    hourly_microusd: int = 0,
    *,
    age_seconds: int = 0,
    source: AzureRetentionEvidenceSource = (
        AzureRetentionEvidenceSource.AZURE_BILLING_CONTRACT
    ),
) -> AzureIdleCostEvidence:
    return AzureIdleCostEvidence(
        hourly_cost_microusd=hourly_microusd,
        observed_at=NOW - timedelta(seconds=age_seconds),
        source=source,
        meter_ids=(METER,) if hourly_microusd else (),
    )


def _latency(
    p95_seconds: float = 964.0,
    *,
    p50_seconds: float = 18.0,
    age_seconds: int = 0,
    samples: int = 2,
) -> AzureProvisioningLatencyEvidence:
    return AzureProvisioningLatencyEvidence(
        p50_seconds=p50_seconds,
        p95_seconds=p95_seconds,
        sample_count=samples,
        observed_at=NOW - timedelta(seconds=age_seconds),
    )


def _layer(
    kind: AzureRetentionLayerKind,
    *,
    hourly_microusd: int = 0,
    p95_seconds: float = 964.0,
    dependencies: tuple[AzureRetentionLayerKind, ...] = (),
    idle_compute_replicas: int = 0,
    cost: AzureIdleCostEvidence | None | object = ...,
    latency: AzureProvisioningLatencyEvidence | None | object = ...,
) -> AzureRetentionLayer:
    selected_cost = _cost(hourly_microusd) if cost is ... else cost
    selected_latency = _latency(p95_seconds) if latency is ... else latency
    return AzureRetentionLayer(
        kind=kind,
        idle_cost=cast(AzureIdleCostEvidence | None, selected_cost),
        provisioning_latency=cast(
            AzureProvisioningLatencyEvidence | None,
            selected_latency,
        ),
        dependencies=dependencies,
        idle_compute_replicas=idle_compute_replicas,
    )


def _policy(**overrides: object) -> AzureIdleRetentionPolicy:
    values: dict[str, object] = {
        "preset": AzureRetentionPreset.BALANCED,
        "max_idle_hourly_cost_microusd": 1_000_000,
        "max_idle_monthly_cost_microusd": 50_000_000,
        "max_retention_cost_microusd": 5_000_000,
        "max_retention_seconds": 3_600,
        "max_price_age_seconds": 3_600,
        "max_latency_age_seconds": 86_400,
        "max_cost_per_saved_hour_microusd": 10_000_000,
    }
    values.update(overrides)
    return AzureIdleRetentionPolicy(**cast(Any, values))


def _plan(
    *layers: AzureRetentionLayer,
    policy: AzureIdleRetentionPolicy | None = None,
    expected_next_demand_seconds: int | None = 1_800,
    traces: list[AzureRetentionTrace] | None = None,
):
    return plan_azure_idle_retention(
        tuple(layers),
        policy=policy or _policy(),
        scope_digest="a" * 64,
        now=NOW,
        runnable_todo_count=0,
        expected_next_demand_seconds=expected_next_demand_seconds,
        trace_sink=(traces.append if traces is not None else None),
    )


def test_balanced_retains_free_environment_and_locked_zero_scale_app() -> None:
    environment, app = container_apps_consumption_layers(
        observed_at=NOW,
        environment_latency=_latency(964),
        app_latency=_latency(240, p50_seconds=90),
        min_replicas=0,
        activation_blocked_when_idle=True,
        has_dedicated_profiles=False,
        has_private_endpoint=False,
        has_planned_maintenance=False,
        has_paid_logging=False,
    )

    plan = _plan(environment, app)

    assert plan.retained_layers == (
        AzureRetentionLayerKind.MANAGED_ENVIRONMENT,
        AzureRetentionLayerKind.CONTAINER_APP,
    )
    assert plan.retention_seconds == 1_800
    assert plan.hourly_cost_microusd == 0
    assert plan.projected_cost_microusd == 0
    assert plan.p95_seconds_saved == 1_204
    assert all(
        decision.disposition is AzureRetentionDisposition.RETAIN
        for decision in plan.decisions
    )


def test_live_endpoint_is_destroyed_while_free_environment_is_retained() -> None:
    environment, app = container_apps_consumption_layers(
        observed_at=NOW,
        environment_latency=_latency(),
        app_latency=_latency(240),
        min_replicas=0,
        activation_blocked_when_idle=False,
        has_dedicated_profiles=False,
        has_private_endpoint=False,
        has_planned_maintenance=False,
        has_paid_logging=False,
    )

    plan = _plan(environment, app)

    assert plan.retained_layers == (AzureRetentionLayerKind.MANAGED_ENVIRONMENT,)
    app_decision = plan.decision_for(AzureRetentionLayerKind.CONTAINER_APP)
    assert app_decision.disposition is AzureRetentionDisposition.DESTROY
    assert app_decision.reason is AzureRetentionReason.IDLE_ACTIVATION_POSSIBLE


def test_container_apps_helper_builds_one_observable_environment_only_plan() -> None:
    traces: list[AzureRetentionTrace] = []

    plan = plan_container_apps_idle_retention(
        policy=_policy(
            preset=AzureRetentionPreset.ZERO_COST_ONLY,
            max_idle_hourly_cost_microusd=0,
            max_idle_monthly_cost_microusd=0,
            max_retention_cost_microusd=0,
            max_cost_per_saved_hour_microusd=0,
            max_price_age_seconds=31_536_000,
        ),
        scope_digest="a" * 64,
        now=NOW,
        environment_latency_seconds=964.0,
        app_latency_seconds=None,
        min_replicas=1,
        activation_blocked_when_idle=False,
        has_dedicated_profiles=False,
        has_private_endpoint=False,
        has_planned_maintenance=False,
        has_paid_logging=False,
        runnable_todo_count=0,
        expected_next_demand_seconds=1_800,
        trace_sink=traces.append,
    )

    assert plan.retained_layers == (AzureRetentionLayerKind.MANAGED_ENVIRONMENT,)
    assert plan.destroyed_layers == ()
    assert plan.retention_seconds == 1_800
    assert plan.hourly_cost_microusd == 0
    assert [trace.event for trace in traces] == [
        AzureRetentionTraceEvent.EVALUATION_STARTED,
        AzureRetentionTraceEvent.PLAN_SELECTED,
    ]


def test_container_apps_helper_keeps_public_app_out_of_retained_frontier() -> None:
    plan = plan_container_apps_idle_retention(
        policy=_policy(
            preset=AzureRetentionPreset.LATENCY_FIRST,
            max_price_age_seconds=31_536_000,
        ),
        scope_digest="a" * 64,
        now=NOW,
        environment_latency_seconds=964.0,
        app_latency_seconds=240.0,
        min_replicas=0,
        activation_blocked_when_idle=False,
        has_dedicated_profiles=False,
        has_private_endpoint=False,
        has_planned_maintenance=False,
        has_paid_logging=False,
        runnable_todo_count=0,
        expected_next_demand_seconds=1_800,
    )

    assert plan.retained_layers == (AzureRetentionLayerKind.MANAGED_ENVIRONMENT,)
    app = plan.decision_for(AzureRetentionLayerKind.CONTAINER_APP)
    assert app.disposition is AzureRetentionDisposition.DESTROY
    assert app.reason is AzureRetentionReason.IDLE_ACTIVATION_POSSIBLE


@pytest.mark.parametrize(
    "feature",
    [
        "has_dedicated_profiles",
        "has_private_endpoint",
        "has_planned_maintenance",
        "has_paid_logging",
    ],
)
def test_paid_environment_features_require_separate_current_price_evidence(
    feature: str,
) -> None:
    flags = {
        "has_dedicated_profiles": False,
        "has_private_endpoint": False,
        "has_planned_maintenance": False,
        "has_paid_logging": False,
    }
    flags[feature] = True
    environment, _app = container_apps_consumption_layers(
        observed_at=NOW,
        environment_latency=_latency(),
        app_latency=_latency(240),
        min_replicas=0,
        activation_blocked_when_idle=True,
        **flags,
    )

    decision = _plan(environment).decisions[0]

    assert decision.disposition is AzureRetentionDisposition.DESTROY
    assert decision.reason is AzureRetentionReason.PRICE_EVIDENCE_MISSING


def test_nonzero_minimum_replica_can_never_be_retained_with_empty_todo() -> None:
    environment, app = container_apps_consumption_layers(
        observed_at=NOW,
        environment_latency=_latency(),
        app_latency=_latency(240),
        min_replicas=1,
        activation_blocked_when_idle=True,
        has_dedicated_profiles=False,
        has_private_endpoint=False,
        has_planned_maintenance=False,
        has_paid_logging=False,
    )

    decision = _plan(environment, app).decision_for(
        AzureRetentionLayerKind.CONTAINER_APP
    )

    assert decision.reason is AzureRetentionReason.IDLE_COMPUTE_FORBIDDEN


def test_unknown_stale_or_future_evidence_fails_closed() -> None:
    missing_price = _layer(AzureRetentionLayerKind.MODEL_CACHE, cost=None)
    missing_latency = _layer(
        AzureRetentionLayerKind.CONTAINER_REGISTRY,
        latency=None,
    )
    stale_price = replace(
        _layer(AzureRetentionLayerKind.MANAGED_ENVIRONMENT),
        idle_cost=_cost(age_seconds=3_601),
    )
    stale_latency = replace(
        _layer(AzureRetentionLayerKind.RESOURCE_GROUP),
        provisioning_latency=_latency(age_seconds=86_401),
    )
    future_price = replace(
        _layer(AzureRetentionLayerKind.PRIVATE_ENDPOINT),
        idle_cost=replace(_cost(), observed_at=NOW + timedelta(seconds=1)),
    )

    plan = _plan(
        missing_price,
        missing_latency,
        stale_price,
        stale_latency,
        future_price,
    )

    assert {decision.reason for decision in plan.decisions} == {
        AzureRetentionReason.PRICE_EVIDENCE_MISSING,
        AzureRetentionReason.LATENCY_EVIDENCE_MISSING,
        AzureRetentionReason.PRICE_EVIDENCE_STALE,
        AzureRetentionReason.LATENCY_EVIDENCE_STALE,
        AzureRetentionReason.EVIDENCE_FROM_FUTURE,
    }
    assert plan.retained_layers == ()


def test_dependency_aware_budget_optimizer_maximizes_saved_latency() -> None:
    environment = _layer(
        AzureRetentionLayerKind.MANAGED_ENVIRONMENT,
        hourly_microusd=100,
        p95_seconds=900,
    )
    app = _layer(
        AzureRetentionLayerKind.CONTAINER_APP,
        hourly_microusd=100,
        p95_seconds=50,
        dependencies=(AzureRetentionLayerKind.MANAGED_ENVIRONMENT,),
    )
    registry = _layer(
        AzureRetentionLayerKind.CONTAINER_REGISTRY,
        hourly_microusd=200,
        p95_seconds=1_000,
    )
    policy = _policy(
        preset=AzureRetentionPreset.LATENCY_FIRST,
        max_idle_hourly_cost_microusd=200,
        max_idle_monthly_cost_microusd=146_000,
    )

    plan = _plan(environment, app, registry, policy=policy)

    assert plan.retained_layers == (AzureRetentionLayerKind.CONTAINER_REGISTRY,)
    assert plan.p95_seconds_saved == 1_000
    assert plan.decision_for(
        AzureRetentionLayerKind.MANAGED_ENVIRONMENT
    ).reason is AzureRetentionReason.BUDGET_TRADEOFF


def test_optimizer_retains_dependency_closure_when_it_wins() -> None:
    environment = _layer(
        AzureRetentionLayerKind.MANAGED_ENVIRONMENT,
        hourly_microusd=100,
        p95_seconds=900,
    )
    app = _layer(
        AzureRetentionLayerKind.CONTAINER_APP,
        hourly_microusd=100,
        p95_seconds=500,
        dependencies=(AzureRetentionLayerKind.MANAGED_ENVIRONMENT,),
    )
    registry = _layer(
        AzureRetentionLayerKind.CONTAINER_REGISTRY,
        hourly_microusd=200,
        p95_seconds=1_000,
    )
    policy = _policy(
        preset=AzureRetentionPreset.LATENCY_FIRST,
        max_idle_hourly_cost_microusd=200,
        max_idle_monthly_cost_microusd=146_000,
    )

    plan = _plan(
        environment,
        replace(app, provisioning_latency=_latency(700)),
        registry,
        policy=policy,
    )

    assert plan.retained_layers == (
        AzureRetentionLayerKind.MANAGED_ENVIRONMENT,
        AzureRetentionLayerKind.CONTAINER_APP,
    )
    assert plan.p95_seconds_saved == 1_600


def test_ineligible_dependency_makes_dependent_layer_ineligible() -> None:
    environment = _layer(
        AzureRetentionLayerKind.MANAGED_ENVIRONMENT,
        cost=None,
    )
    app = _layer(
        AzureRetentionLayerKind.CONTAINER_APP,
        p95_seconds=2_000,
        dependencies=(AzureRetentionLayerKind.MANAGED_ENVIRONMENT,),
    )

    plan = _plan(environment, app)

    assert plan.retained_layers == ()
    assert plan.decision_for(
        AzureRetentionLayerKind.MANAGED_ENVIRONMENT
    ).reason is AzureRetentionReason.PRICE_EVIDENCE_MISSING
    assert plan.decision_for(
        AzureRetentionLayerKind.CONTAINER_APP
    ).reason is AzureRetentionReason.BUDGET_TRADEOFF


def test_balanced_mode_applies_cost_per_saved_hour_ceiling() -> None:
    expensive = _layer(
        AzureRetentionLayerKind.MODEL_CACHE,
        hourly_microusd=1_000_000,
        p95_seconds=60,
    )

    plan = _plan(
        expensive,
        policy=_policy(max_cost_per_saved_hour_microusd=500_000),
    )

    assert plan.decisions[0].reason is AzureRetentionReason.VALUE_CEILING_EXCEEDED


def test_zero_cost_only_and_always_destroy_presets_are_explicit() -> None:
    free = _layer(AzureRetentionLayerKind.RESOURCE_GROUP)
    priced = _layer(
        AzureRetentionLayerKind.MODEL_CACHE,
        hourly_microusd=1,
    )

    zero_only = _plan(
        free,
        priced,
        policy=_policy(preset=AzureRetentionPreset.ZERO_COST_ONLY),
    )
    destroy = _plan(
        free,
        policy=_policy(preset=AzureRetentionPreset.ALWAYS_DESTROY),
    )

    assert zero_only.retained_layers == (AzureRetentionLayerKind.RESOURCE_GROUP,)
    assert zero_only.decision_for(
        AzureRetentionLayerKind.MODEL_CACHE
    ).reason is AzureRetentionReason.PRESET_EXCLUDES_COST
    assert destroy.retained_layers == ()
    assert destroy.decisions[0].reason is AzureRetentionReason.PRESET_DESTROY


def test_expected_demand_outside_bounded_window_destroys_now() -> None:
    plan = _plan(
        _layer(AzureRetentionLayerKind.MANAGED_ENVIRONMENT),
        expected_next_demand_seconds=3_601,
    )

    assert plan.retention_seconds == 0
    assert plan.decisions[0].reason is AzureRetentionReason.DEMAND_OUTSIDE_WINDOW


def test_unknown_next_demand_retains_only_until_maximum_then_requires_recheck() -> None:
    plan = _plan(
        _layer(AzureRetentionLayerKind.MANAGED_ENVIRONMENT),
        expected_next_demand_seconds=None,
    )

    assert plan.retention_seconds == 3_600
    assert plan.reconcile_at == NOW + timedelta(hours=1)


def test_hourly_monthly_and_horizon_budgets_are_all_independent() -> None:
    layer = _layer(
        AzureRetentionLayerKind.MODEL_CACHE,
        hourly_microusd=1_000,
        p95_seconds=3_600,
    )
    policies = (
        _policy(max_idle_hourly_cost_microusd=999),
        _policy(max_idle_monthly_cost_microusd=729_999),
        _policy(max_retention_cost_microusd=499),
    )

    for policy in policies:
        plan = _plan(layer, policy=policy)
        assert plan.retained_layers == ()
        assert plan.decisions[0].reason is AzureRetentionReason.BUDGET_TRADEOFF


def test_plan_is_order_independent_and_zdd_replay_stable() -> None:
    environment = _layer(AzureRetentionLayerKind.MANAGED_ENVIRONMENT)
    registry = _layer(AzureRetentionLayerKind.CONTAINER_REGISTRY)

    first = _plan(environment, registry)
    replay = _plan(registry, environment)

    assert first == replay
    assert first.plan_digest == replay.plan_digest


def test_trace_is_content_free_and_sink_failure_is_fail_closed() -> None:
    traces: list[AzureRetentionTrace] = []
    layer = _layer(AzureRetentionLayerKind.MANAGED_ENVIRONMENT)

    plan = _plan(layer, traces=traces)

    assert [trace.event for trace in traces] == [
        AzureRetentionTraceEvent.EVALUATION_STARTED,
        AzureRetentionTraceEvent.PLAN_SELECTED,
    ]
    assert traces[-1].plan_digest == plan.plan_digest
    assert "subscription" not in repr(traces).casefold()
    assert "resource" not in repr(traces).casefold()

    def broken(_trace: AzureRetentionTrace) -> None:
        raise RuntimeError("provider-secret")

    with pytest.raises(RuntimeError, match="Azure retention trace failed") as captured:
        plan_azure_idle_retention(
            (layer,),
            policy=_policy(),
            scope_digest="a" * 64,
            now=NOW,
            runnable_todo_count=0,
            expected_next_demand_seconds=1_800,
            trace_sink=broken,
        )
    assert "provider-secret" not in repr(captured.value)


def test_nonempty_todo_is_not_misrepresented_as_idle_retention() -> None:
    with pytest.raises(ValueError, match="empty durable todo"):
        plan_azure_idle_retention(
            (_layer(AzureRetentionLayerKind.MANAGED_ENVIRONMENT),),
            policy=_policy(),
            scope_digest="a" * 64,
            now=NOW,
            runnable_todo_count=1,
            expected_next_demand_seconds=None,
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("max_idle_hourly_cost_microusd", -1),
        ("max_idle_monthly_cost_microusd", True),
        ("max_retention_cost_microusd", -1),
        ("max_retention_seconds", 0),
        ("max_price_age_seconds", 0),
        ("max_latency_age_seconds", 0),
        ("max_cost_per_saved_hour_microusd", -1),
    ],
)
def test_policy_rejects_unbounded_or_ambiguous_values(field: str, value: object) -> None:
    with pytest.raises(ValueError, match=field):
        replace(_policy(), **cast(Any, {field: value}))


@pytest.mark.parametrize("value", [-1, True])
def test_cost_evidence_rejects_invalid_money(value: object) -> None:
    with pytest.raises(ValueError, match="hourly_cost_microusd"):
        replace(_cost(), hourly_cost_microusd=cast(Any, value))


@pytest.mark.parametrize(
    "latency",
    [
        {"p50_seconds": 0.0},
        {"p95_seconds": float("nan")},
        {"p50_seconds": 20.0, "p95_seconds": 10.0},
        {"sample_count": 0},
        {"sample_count": True},
        {"observed_at": datetime(2026, 9, 8, 12, 0)},
    ],
)
def test_latency_evidence_rejects_invalid_statistics(latency: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        replace(_latency(), **cast(Any, latency))


def test_duplicate_layers_cycles_and_missing_dependencies_are_rejected() -> None:
    environment = _layer(AzureRetentionLayerKind.MANAGED_ENVIRONMENT)
    with pytest.raises(ValueError, match="unique"):
        _plan(environment, environment)

    cyclic_environment = replace(
        environment,
        dependencies=(AzureRetentionLayerKind.CONTAINER_APP,),
    )
    cyclic_app = _layer(
        AzureRetentionLayerKind.CONTAINER_APP,
        dependencies=(AzureRetentionLayerKind.MANAGED_ENVIRONMENT,),
    )
    with pytest.raises(ValueError, match="cycle"):
        _plan(cyclic_environment, cyclic_app)

    missing = _layer(
        AzureRetentionLayerKind.CONTAINER_APP,
        dependencies=(AzureRetentionLayerKind.MANAGED_ENVIRONMENT,),
    )
    with pytest.raises(ValueError, match="dependency"):
        _plan(missing)


def test_empty_candidate_set_is_a_stable_zero_cost_destroy_plan() -> None:
    plan = _plan()

    assert plan.retained_layers == ()
    assert plan.destroyed_layers == ()
    assert plan.retention_seconds == 0
    assert plan.hourly_cost_microusd == 0
    assert plan.scope_digest == "a" * 64


@pytest.mark.parametrize("scope_digest", ["short", "A" * 64, "0" * 63 + "x"])
def test_plan_rejects_ambiguous_scope_identity(scope_digest: str) -> None:
    with pytest.raises(ValueError, match="scope_digest"):
        plan_azure_idle_retention(
            (),
            policy=_policy(),
            scope_digest=scope_digest,
            now=NOW,
            runnable_todo_count=0,
            expected_next_demand_seconds=None,
        )


def _meter(
    meter_id: str,
    *,
    price: float,
    unit: str,
    fetched_at: datetime = NOW,
    region: str = "eastus",
) -> AzureRetailMeter:
    return AzureRetailMeter(
        region=region,
        sku_name="Reviewed SKU",
        price_type="Consumption",
        meter_id=meter_id,
        meter_name="Reviewed Meter",
        retail_price=price,
        unit_of_measure=unit,
        effective_start_date=NOW - timedelta(days=1),
        fetched_at=fetched_at,
    )


def test_existing_retail_price_meters_feed_idle_optimizer_without_duplication() -> None:
    older = NOW - timedelta(minutes=2)
    evidence = idle_cost_evidence_from_retail_meters(
        (
            _meter(METER, price=0.25, unit="1 Hour"),
            _meter(
                "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                price=7.30,
                unit="1/Month",
                fetched_at=older,
            ),
        )
    )

    assert evidence.hourly_cost_microusd == 260_000
    assert evidence.observed_at == older
    assert evidence.source is AzureRetentionEvidenceSource.AZURE_RETAIL_PRICES
    assert evidence.meter_ids == (
        METER,
        "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
    )


@pytest.mark.parametrize(
    "meters",
    [
        (),
        (
            _meter(METER, price=0.25, unit="1 Day"),
        ),
        (
            _meter(METER, price=float("nan"), unit="1 Hour"),
        ),
        (
            _meter(METER, price=0.25, unit="1 Hour"),
            _meter(
                "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                price=0.25,
                unit="1 Hour",
                region="westus",
            ),
        ),
    ],
)
def test_retail_meter_adapter_fails_closed_on_ambiguous_prices(
    meters: tuple[AzureRetailMeter, ...],
) -> None:
    with pytest.raises(ValueError, match="retail meter"):
        idle_cost_evidence_from_retail_meters(meters)


def test_operator_guide_documents_strategies_costs_and_practitioner_reports() -> None:
    guide = (ROOT / "docs" / "azure-idle-retention.md").read_text(
        encoding="utf-8"
    )

    for strategy in (
        "always_destroy",
        "zero_cost_only",
        "balanced",
        "latency_first",
    ):
        assert strategy in guide
    for source in (
        "learn.microsoft.com/en-us/azure/container-apps/billing",
        "learn.microsoft.com/en-us/azure/container-apps/plans",
        "learn.microsoft.com/en-us/azure/container-registry/container-registry-storage",
        "github.com/microsoft/azure-container-apps/issues/388",
        "github.com/microsoft/azure-container-apps/issues/1511",
        "github.com/microsoft/azure-container-apps/issues/1628",
        "github.com/microsoft/azure-container-apps/issues/1800",
        "learn.microsoft.com/en-us/azure/container-apps/sessions",
    ):
        assert source in guide
    assert "964 seconds" in guide
    assert "18 seconds" in guide
    assert "unknown or stale" in guide.casefold()
    assert "idle_retention:" in guide
    assert "AZURE_CONTAINERAPP_LIVE_PROOF_RETENTION_PRESET=zero_cost_only" in guide
    assert "AZURE_CONTAINERAPP_RETENTION_TRACE" in guide
