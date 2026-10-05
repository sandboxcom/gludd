"""Deterministic Azure Container Apps runner-fleet planning contracts."""

from __future__ import annotations

from dataclasses import replace
from typing import Any, cast

import pytest

from general_ludd.infra.azure_containerapp_gpu import (
    A100_PROFILE,
    T4_PROFILE,
    AzureContainerAppGPUProfile,
    ModelServingRequirement,
)
from general_ludd.infra.azure_containerapp_topology import (
    AzureFleetConstraints,
    AzureProfileCapacity,
    AzureRunnerDemand,
    AzureRunnerTopologyError,
    TopologyTrace,
    plan_azure_runner_topology,
)

_REVISION = "7ae557604adf67be50417f59c2c2f167def9a775"


def _requirement(
    *,
    model_id: str = "Qwen/Qwen2.5-0.5B-Instruct",
    parameter_count: int = 494_032_768,
    weight_bits: int = 16,
    kv_cache_mib: int = 2_048,
) -> ModelServingRequirement:
    return ModelServingRequirement(
        model_id=model_id,
        revision=_REVISION,
        parameter_count=parameter_count,
        weight_bits=weight_bits,
        kv_cache_mib=kv_cache_mib,
        runtime_overhead_mib=3_072,
    )


def _capacity(
    profile: AzureContainerAppGPUProfile,
    *,
    max_replicas: int = 8,
    hourly_cost_microusd: int = 1_000_000,
) -> AzureProfileCapacity:
    return AzureProfileCapacity(
        profile=profile,
        max_replicas=max_replicas,
        hourly_cost_microusd_per_replica=hourly_cost_microusd,
    )


def _constraints(**overrides: object) -> AzureFleetConstraints:
    values: dict[str, object] = {
        "profile_capacities": (
            _capacity(T4_PROFILE),
            _capacity(
                A100_PROFILE,
                max_replicas=4,
                hourly_cost_microusd=4_000_000,
            ),
        ),
        "max_apps": 4,
        "max_total_replicas": 8,
        "max_replicas_per_app": 4,
        "max_hourly_cost_microusd": 20_000_000,
        "ttl_minutes": 60,
    }
    values.update(overrides)
    return AzureFleetConstraints(**values)  # type: ignore[arg-type]


def _demand(
    task_id: str,
    *,
    requirement: ModelServingRequirement | None = None,
    peak_concurrency: int = 3,
    per_replica_concurrency: int = 2,
    depends_on: frozenset[str] = frozenset(),
) -> AzureRunnerDemand:
    return AzureRunnerDemand(
        task_id=task_id,
        requirement=requirement or _requirement(),
        peak_concurrency=peak_concurrency,
        per_replica_concurrency=per_replica_concurrency,
        depends_on=depends_on,
        runtime="vllm-openai",
    )


def test_serial_stages_share_one_right_sized_runner_and_reuse_capacity() -> None:
    demands = (
        _demand("plan"),
        _demand("code", depends_on=frozenset({"plan"})),
        _demand("review", depends_on=frozenset({"code"})),
    )

    plan = plan_azure_runner_topology(demands, _constraints())

    assert plan.batches == (("plan",), ("code",), ("review",))
    assert len(plan.apps) == 1
    app = plan.apps[0]
    assert app.task_ids == ("code", "plan", "review")
    assert app.workload_profile_type == T4_PROFILE.workload_profile_type
    assert app.profile_name == "gpu-t4"
    assert app.min_replicas == 1
    assert app.max_replicas == 2
    assert len(plan.profiles) == 1
    assert plan.profiles[0].max_replicas == 2
    assert {route.task_id for route in plan.routes} == {"plan", "code", "review"}
    assert {route.via for route in plan.routes} == {"gludd-gateway"}
    assert {route.runner_id for route in plan.routes} == {app.runner_id}


def test_parallel_stages_sum_peak_concurrency_for_replica_count() -> None:
    plan = plan_azure_runner_topology(
        (_demand("alpha"), _demand("beta")),
        _constraints(),
    )

    assert plan.batches == (("alpha", "beta"),)
    assert len(plan.apps) == 1
    assert plan.apps[0].max_replicas == 3
    assert plan.profiles[0].max_replicas == 3
    assert plan.max_hourly_cost_microusd == 3_000_000


def test_mixed_model_shapes_create_only_the_profiles_and_apps_needed() -> None:
    a100 = _requirement(
        model_id="example/seven-billion",
        parameter_count=7_000_000_000,
        kv_cache_mib=4_096,
    )

    plan = plan_azure_runner_topology(
        (_demand("small"), _demand("large", requirement=a100, peak_concurrency=1)),
        _constraints(),
    )

    assert len(plan.apps) == 2
    assert {app.workload_profile_type for app in plan.apps} == {
        T4_PROFILE.workload_profile_type,
        A100_PROFILE.workload_profile_type,
    }
    assert {profile.profile_name for profile in plan.profiles} == {
        "gpu-t4",
        "gpu-a100",
    }
    assert all(app.min_replicas == 1 for app in plan.apps)


def test_empty_demand_explicitly_plans_no_paid_environment_or_runner() -> None:
    plan = plan_azure_runner_topology((), _constraints())

    assert plan.apps == ()
    assert plan.profiles == ()
    assert plan.routes == ()
    assert plan.batches == ()
    assert plan.max_hourly_cost_microusd == 0
    assert len(plan.plan_digest) == 64


def test_plan_is_stable_across_input_order_and_has_immutable_identity() -> None:
    demands = (
        _demand("beta"),
        _demand("alpha"),
        _demand("omega", depends_on=frozenset({"alpha", "beta"})),
    )

    first = plan_azure_runner_topology(demands, _constraints())
    second = plan_azure_runner_topology(tuple(reversed(demands)), _constraints())

    assert first == second
    assert first.plan_digest == second.plan_digest
    assert all(app.model_revision == _REVISION for app in first.apps)
    assert all(app.runner_id.startswith("gludd-runner-") for app in first.apps)


@pytest.mark.parametrize(
    ("demands", "message"),
    [
        ((_demand("same"), _demand("same")), "duplicate task_id"),
        (
            (_demand("a", depends_on=frozenset({"missing"})),),
            "unknown task",
        ),
        (
            (
                _demand("a", depends_on=frozenset({"b"})),
                _demand("b", depends_on=frozenset({"a"})),
            ),
            "cycle",
        ),
    ],
)
def test_ambiguous_or_cyclic_task_graph_fails_closed(
    demands: tuple[AzureRunnerDemand, ...],
    message: str,
) -> None:
    with pytest.raises(AzureRunnerTopologyError, match=message):
        plan_azure_runner_topology(demands, _constraints())


@pytest.mark.parametrize(
    ("constraints", "message"),
    [
        (_constraints(max_apps=1), "app limit"),
        (_constraints(max_total_replicas=1), "total replica limit"),
        (_constraints(max_replicas_per_app=1), "per-app replica limit"),
        (_constraints(max_hourly_cost_microusd=1), "hourly cost limit"),
        (
            _constraints(
                profile_capacities=(
                    _capacity(T4_PROFILE, max_replicas=1),
                    _capacity(A100_PROFILE),
                )
            ),
            "profile quota",
        ),
    ],
)
def test_every_fleet_budget_is_enforced_before_a_plan_is_returned(
    constraints: AzureFleetConstraints,
    message: str,
) -> None:
    demands = (
        _demand("small-a"),
        _demand(
            "large",
            requirement=_requirement(
                model_id="example/seven-billion",
                parameter_count=7_000_000_000,
                kv_cache_mib=4_096,
            ),
            peak_concurrency=1,
        ),
    )

    with pytest.raises(AzureRunnerTopologyError, match=message):
        plan_azure_runner_topology(demands, constraints)


def test_explicit_hardware_inventory_can_select_its_smallest_available_profile() -> None:
    constraints = _constraints(
        profile_capacities=(_capacity(A100_PROFILE),)
    )

    plan = plan_azure_runner_topology((_demand("small"),), constraints)

    assert plan.apps[0].workload_profile_type == A100_PROFILE.workload_profile_type


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("task_id", ""),
        ("task_id", "private task"),
        ("peak_concurrency", 0),
        ("per_replica_concurrency", 0),
        ("runtime", "unreviewed-runtime"),
    ],
)
def test_invalid_runner_demand_is_rejected_at_construction(
    field: str,
    value: object,
) -> None:
    with pytest.raises(ValueError, match=field):
        replace(_demand("task"), **cast(Any, {field: value}))


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"max_apps": 0}, "max_apps"),
        ({"max_total_replicas": 0}, "max_total_replicas"),
        ({"max_replicas_per_app": 0}, "max_replicas_per_app"),
        ({"max_hourly_cost_microusd": 0}, "max_hourly_cost"),
        ({"ttl_minutes": 0}, "ttl_minutes"),
        ({"ttl_minutes": 1_441}, "ttl_minutes"),
        (
            {
                "profile_capacities": (
                    _capacity(T4_PROFILE),
                    _capacity(T4_PROFILE),
                )
            },
            "duplicate profile",
        ),
    ],
)
def test_invalid_or_unbounded_fleet_constraint_is_rejected(
    overrides: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        _constraints(**overrides)


@pytest.mark.parametrize(
    ("factory", "message"),
    [
        (
            lambda: AzureProfileCapacity(
                profile=cast(Any, object()),
                max_replicas=1,
                hourly_cost_microusd_per_replica=1,
            ),
            "profile",
        ),
        (
            lambda: _capacity(T4_PROFILE, max_replicas=0),
            "max_replicas",
        ),
        (
            lambda: _capacity(
                T4_PROFILE,
                hourly_cost_microusd=0,
            ),
            "hourly_cost",
        ),
        (
            lambda: _constraints(profile_capacities=cast(Any, [])),
            "profile_capacities",
        ),
        (
            lambda: replace(_demand("task"), requirement=cast(Any, object())),
            "requirement",
        ),
        (
            lambda: replace(_demand("task"), depends_on=cast(Any, ["other"])),
            "depends_on",
        ),
        (
            lambda: replace(_demand("task"), depends_on=frozenset({"task"})),
            "depends_on",
        ),
    ],
)
def test_invalid_profile_and_demand_boundaries_fail_before_planning(
    factory: object,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        cast(Any, factory)()


def test_public_planner_rejects_untyped_effect_boundaries() -> None:
    with pytest.raises(ValueError, match="constraints"):
        plan_azure_runner_topology((), cast(Any, object()))
    with pytest.raises(ValueError, match="trace_sink"):
        plan_azure_runner_topology((), _constraints(), trace_sink=cast(Any, object()))
    with pytest.raises(ValueError, match="demands"):
        plan_azure_runner_topology(cast(Any, object()), _constraints())

    class DemandShapedObject:
        task_id = "task"

    with pytest.raises(ValueError, match="demands"):
        plan_azure_runner_topology(
            cast(Any, (DemandShapedObject(),)),
            _constraints(),
        )


def test_topology_accepts_provider_inventory_profile_without_code_key() -> None:
    inventory_profile = AzureContainerAppGPUProfile(
        name="inventory-profile",
        workload_profile_name="gpu-inventory-01",
        workload_profile_type="provider/Profile-vNext",
        gpu_vram_mib=48 * 1024,
        usable_vram_mib=44 * 1024,
        cpu_cores=16,
        memory_gib=128,
    )
    constraints = _constraints(
        profile_capacities=(_capacity(inventory_profile),),
    )

    plan = plan_azure_runner_topology(
        (
            _demand(
                "future",
                requirement=_requirement(parameter_count=12_000_000_000),
                peak_concurrency=1,
            ),
        ),
        constraints,
    )

    assert plan.apps[0].profile_name == "gpu-inventory-01"
    assert plan.apps[0].workload_profile_type == "provider/Profile-vNext"


def test_topology_traces_are_continuous_and_content_free() -> None:
    traces: list[TopologyTrace] = []

    plan = plan_azure_runner_topology(
        (_demand("private-task"),),
        _constraints(),
        trace_sink=traces.append,
    )

    assert [trace.phase for trace in traces] == [
        "topology_started",
        "topology_batches_planned",
        "topology_capacity_planned",
        "topology_completed",
    ]
    assert traces[-1].app_count == 1
    assert traces[-1].total_max_replicas == plan.apps[0].max_replicas
    rendered = repr(traces)
    assert "Qwen" not in rendered
    assert _REVISION not in rendered
    assert "private-task" not in rendered


def test_trace_sink_failure_aborts_planning() -> None:
    def broken_sink(_trace: TopologyTrace) -> None:
        raise RuntimeError("private sink failure")

    with pytest.raises(RuntimeError, match="topology trace publication failed"):
        plan_azure_runner_topology(
            (_demand("task"),),
            _constraints(),
            trace_sink=broken_sink,
        )
