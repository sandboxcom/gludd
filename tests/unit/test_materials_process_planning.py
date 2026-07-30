"""Tests for manufacturing route-card and process-planning integration
(spec MATE-001 §3 ``manufacturing_plan`` / ``inspection_plan`` roles, §9 ZDD).

Covers MATE-P5: combining multiple processes into a single route card with
quality gates between stages, cost estimation (material+labor+energy+overhead),
energy/waste tracking, scale-up considerations, and end-to-end traceability
from requirements to each process step (MATE-AT-005).
"""

from __future__ import annotations

from general_ludd.materials.process_planning import (
    ProcessStep,
    RouteCard,
    estimate_cost,
    estimate_energy,
    plan_inspection,
    plan_manufacturing,
)

# ---------------------------------------------------------------------------
# Route-card construction: multiple processes combine into one route
# ---------------------------------------------------------------------------


class TestRouteCardCombinesProcesses:
    def test_route_card_holds_ordered_process_steps(self) -> None:
        steps = [
            ProcessStep(operation="stamping", equipment_class="progressive_die_press"),
            ProcessStep(operation="drilling", equipment_class="vmc"),
            ProcessStep(operation="gmaw", equipment_class="robotic_weld_cell"),
        ]
        card = RouteCard(steps=steps)
        assert [s.operation for s in card.steps] == ["stamping", "drilling", "gmaw"]

    def test_plan_manufacturing_combines_forming_joining_machining_finishing(self) -> None:
        route = plan_manufacturing(
            material_id="aisi_1045",
            operations=["stamping", "drilling", "gmaw"],
        )
        ops = [s.operation for s in route.steps]
        assert "stamping" in ops
        assert "gmaw" in ops
        assert "drilling" in ops
        # Forming precedes joining, which precedes finishing where applicable.
        assert ops.index("stamping") < ops.index("gmaw")

    def test_empty_operations_yields_insufficient_data(self) -> None:
        route = plan_manufacturing(material_id="aisi_1045", operations=[])
        assert route.state == "insufficient_data"


# ---------------------------------------------------------------------------
# Quality gates between stages
# ---------------------------------------------------------------------------


class TestQualityGatesBetweenStages:
    def test_each_step_carries_a_quality_gate(self) -> None:
        route = plan_manufacturing(
            material_id="aa6061_t6",
            operations=["forging", "milling"],
        )
        for step in route.steps:
            assert step.quality_gate is not None
            assert "criterion" in step.quality_gate

    def test_quality_gate_links_to_inspection_method(self) -> None:
        route = plan_manufacturing(
            material_id="aisi_1045",
            operations=["stamping", "drilling"],
        )
        # At least one gate must reference an NDT/dimensional inspection method.
        methods = [s.quality_gate.get("inspection_method", "") for s in route.steps]
        assert any(m for m in methods), "at least one step must have an inspection method"


# ---------------------------------------------------------------------------
# Cost estimation: material + labor + energy + overhead
# ---------------------------------------------------------------------------


class TestCostEstimation:
    def test_cost_sums_four_components(self) -> None:
        route = plan_manufacturing(
            material_id="aisi_1045",
            operations=["stamping", "drilling"],
            quantity=100,
        )
        cost = estimate_cost(route)
        assert cost["total_usd"] > 0
        for key in ("material_usd", "labor_usd", "energy_usd", "overhead_usd"):
            assert key in cost
            assert cost[key] >= 0
        # Total equals the sum of components (no hidden double-count).
        components = cost["material_usd"] + cost["labor_usd"] + cost["energy_usd"] + cost["overhead_usd"]
        assert abs(cost["total_usd"] - components) < 1e-6

    def test_cost_scales_with_quantity(self) -> None:
        small = plan_manufacturing(material_id="aisi_1045", operations=["stamping"], quantity=10)
        large = plan_manufacturing(material_id="aisi_1045", operations=["stamping"], quantity=1000)
        assert estimate_cost(large)["total_usd"] > estimate_cost(small)["total_usd"]


# ---------------------------------------------------------------------------
# Energy estimation per process
# ---------------------------------------------------------------------------


class TestEnergyEstimation:
    def test_energy_estimated_per_step(self) -> None:
        route = plan_manufacturing(
            material_id="aa6061_t6",
            operations=["forging", "milling"],
        )
        energy = estimate_energy(route)
        assert energy["total_kwh"] > 0
        assert len(energy["per_step_kwh"]) == len(route.steps)

    def test_high_energy_processes_exceed_low_energy(self) -> None:
        # Forging (heating + deformation) consumes more energy than drilling.
        forge = plan_manufacturing(material_id="aisi_1045", operations=["forging"])
        drill = plan_manufacturing(material_id="aisi_1045", operations=["drilling"])
        assert estimate_energy(forge)["total_kwh"] > estimate_energy(drill)["total_kwh"]


# ---------------------------------------------------------------------------
# Waste / scrap tracking
# ---------------------------------------------------------------------------


class TestWasteTracking:
    def test_route_reports_material_yield_and_scrap(self) -> None:
        route = plan_manufacturing(
            material_id="aisi_1045",
            operations=["stamping", "drilling"],
        )
        assert "material_yield_pct" in route.sustainability
        assert route.sustainability["material_yield_pct"] <= 100.0
        assert route.sustainability.get("scrap_rate_pct", 0.0) >= 0.0


# ---------------------------------------------------------------------------
# Scale-up consideration
# ---------------------------------------------------------------------------


class TestScaleUpConsideration:
    def test_scale_up_notes_prototype_vs_production(self) -> None:
        route = plan_manufacturing(
            material_id="aa6061_t6",
            operations=["forging", "milling"],
            quantity=5000,
        )
        assert "scale_up" in route.notes
        assert isinstance(route.notes["scale_up"], str)
        assert len(route.notes["scale_up"]) > 0


# ---------------------------------------------------------------------------
# Inspection plan: incoming / in-process / final
# ---------------------------------------------------------------------------


class TestInspectionPlan:
    def test_inspection_plan_has_three_stages(self) -> None:
        route = plan_manufacturing(
            material_id="aisi_1045",
            operations=["stamping", "drilling"],
        )
        plan = plan_inspection(route)
        stages = {m["stage"] for m in plan["measurements"]}
        assert "incoming" in stages
        assert "in_process" in stages
        assert "final" in stages

    def test_each_measurement_has_acceptance_criterion(self) -> None:
        route = plan_manufacturing(
            material_id="aa6061_t6",
            operations=["forging"],
        )
        plan = plan_inspection(route)
        for m in plan["measurements"]:
            assert m.get("acceptance"), "measurement missing acceptance criterion"


# ---------------------------------------------------------------------------
# Traceability: requirements → each process step
# ---------------------------------------------------------------------------


class TestTraceability:
    def test_each_step_records_required_inputs(self) -> None:
        route = plan_manufacturing(
            material_id="aisi_1045",
            operations=["stamping", "drilling", "gmaw"],
        )
        for step in route.steps:
            assert step.inputs, f"step {step.operation} has no declared inputs"
            assert step.outputs, f"step {step.operation} has no declared outputs"

    def test_route_carries_requirement_origin(self) -> None:
        route = plan_manufacturing(
            material_id="aa6061_t6",
            operations=["forging", "milling"],
            requirement_refs=["yield_strength>=276MPa", "tolerance=IT8"],
        )
        assert route.traceability["requirement_refs"] == [
            "yield_strength>=276MPa",
            "tolerance=IT8",
        ]
