"""Tests for manufacturing route-card and process-planning integration
(spec MATE-001 §3 ``manufacturing_plan`` / ``inspection_plan`` roles, §9 ZDD).

Covers MATE-P5: combining multiple processes into a single route card with
quality gates between stages, cost estimation (material+labor+energy+overhead),
energy/waste tracking, scale-up considerations, and end-to-end traceability
from requirements to each process step (MATE-AT-005).
"""

from __future__ import annotations

from general_ludd.materials.core import INSUFFICIENT_DATA
from general_ludd.materials.process_planning import (
    FINISHING_OPS,
    FORMING_OPS,
    JOINING_OPS,
    MACHINING_OPS,
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


# ---------------------------------------------------------------------------
# Deep: ProcessStep default initialization
# ---------------------------------------------------------------------------


class TestProcessStepDefaults:
    def test_default_equipment_class_empty_string(self) -> None:
        step = ProcessStep(operation="milling")
        assert step.equipment_class == ""
        assert step.parameters == {}
        assert step.inputs == []
        assert step.outputs == []
        assert step.quality_gate == {}
        assert step.inspection == {}

    def test_full_construction(self) -> None:
        step = ProcessStep(
            operation="forging",
            equipment_class="hydraulic_forge_press",
            parameters={"temp_C": 450},
            inputs=["billet"],
            outputs=["forged_blank"],
            quality_gate={"criterion": "no cracks"},
            inspection={"method": "UT"},
        )
        assert step.operation == "forging"
        assert step.equipment_class == "hydraulic_forge_press"
        assert step.parameters["temp_C"] == 450
        assert step.inputs == ["billet"]
        assert step.outputs == ["forged_blank"]


# ---------------------------------------------------------------------------
# Deep: RouteCard default initialization
# ---------------------------------------------------------------------------


class TestRouteCardDefaults:
    def test_default_route_card_is_empty(self) -> None:
        card = RouteCard()
        assert card.steps == []
        assert card.material_id == ""
        assert card.quantity == 1
        assert card.state == "ok"
        assert card.reason == ""
        assert card.sustainability == {}
        assert card.traceability == {}
        assert card.notes == {}

    def test_route_id_is_generated(self) -> None:
        card = RouteCard()
        assert card.route_id.startswith("route-")
        assert len(card.route_id) > 6

    def test_route_ids_are_unique(self) -> None:
        cards = [RouteCard() for _ in range(10)]
        ids = {c.route_id for c in cards}
        assert len(ids) == 10


# ---------------------------------------------------------------------------
# Deep: plan_manufacturing edge cases
# ---------------------------------------------------------------------------


class TestPlanManufacturingEdgeCases:
    def test_unknown_material_yields_insufficient_data(self) -> None:
        route = plan_manufacturing(
            material_id="unobtanium",
            operations=["stamping", "drilling"],
        )
        assert route.state == INSUFFICIENT_DATA
        assert "unknown material" in route.reason

    def test_single_operation_route(self) -> None:
        route = plan_manufacturing(
            material_id="aisi_1045",
            operations=["drilling"],
        )
        assert route.state == "ok"
        assert len(route.steps) == 1
        step = route.steps[0]
        assert step.operation == "drilling"
        assert step.equipment_class == "drill_press"
        assert step.inputs
        assert step.outputs

    def test_operations_sorted_by_sequence_rank(self) -> None:
        route = plan_manufacturing(
            material_id="aisi_1045",
            operations=["painting", "stamping", "drilling", "gmaw"],
        )
        ops = [s.operation for s in route.steps]
        stamp_idx = ops.index("stamping")
        gmaw_idx = ops.index("gmaw")
        drill_idx = ops.index("drilling")
        paint_idx = ops.index("painting")
        assert stamp_idx < gmaw_idx < drill_idx < paint_idx

    def test_unknown_operation_gets_general_equipment_and_default_rank(self) -> None:
        route = plan_manufacturing(
            material_id="aisi_1045",
            operations=["unknown_op", "stamping"],
        )
        ops = [s.operation for s in route.steps]
        assert ops[0] == "stamping"
        assert ops[1] == "unknown_op"
        unknown = route.steps[1]
        assert unknown.equipment_class == "general_purpose"


# ---------------------------------------------------------------------------
# Deep: scale-up boundaries
# ---------------------------------------------------------------------------


class TestScaleUpBoundaries:
    def test_prototype_volume_less_than_11(self) -> None:
        for qty in (1, 5, 10):
            route = plan_manufacturing(
                material_id="aisi_1045",
                operations=["stamping"],
                quantity=qty,
            )
            assert "prototype" in route.notes["scale_up"].lower()

    def test_pilot_volume_11_to_1000(self) -> None:
        for qty in (11, 100, 1000):
            route = plan_manufacturing(
                material_id="aisi_1045",
                operations=["stamping"],
                quantity=qty,
            )
            if qty <= 10:
                assert "prototype" in route.notes["scale_up"].lower()
            elif qty <= 1000:
                assert "pilot" in route.notes["scale_up"].lower() or "bridge" in route.notes["scale_up"].lower()

    def test_production_volume_above_1000(self) -> None:
        route = plan_manufacturing(
            material_id="aa6061_t6",
            operations=["forging"],
            quantity=5000,
        )
        assert "production" in route.notes["scale_up"].lower()


# ---------------------------------------------------------------------------
# Deep: estimate_cost / estimate_energy / plan_inspection insufficient_data routes
# ---------------------------------------------------------------------------


class TestInsufficientDataRoutes:
    def test_estimate_cost_on_insufficient_data_route(self) -> None:
        route = plan_manufacturing(material_id="aisi_1045", operations=[])
        cost = estimate_cost(route)
        assert cost["state"] == INSUFFICIENT_DATA
        assert cost["total_usd"] == 0.0

    def test_estimate_energy_on_insufficient_data_route(self) -> None:
        route = plan_manufacturing(material_id="aisi_1045", operations=[])
        energy = estimate_energy(route)
        assert energy["state"] == INSUFFICIENT_DATA
        assert energy["total_kwh"] == 0.0

    def test_plan_inspection_on_insufficient_data_route(self) -> None:
        route = plan_manufacturing(material_id="aisi_1045", operations=[])
        plan = plan_inspection(route)
        assert plan["state"] == INSUFFICIENT_DATA
        assert plan["measurements"] == []


# ---------------------------------------------------------------------------
# Deep: estimate_cost with different materials
# ---------------------------------------------------------------------------


class TestCostByMaterial:
    def test_aluminum_costs_more_than_steel_per_kg(self) -> None:
        steel = plan_manufacturing(material_id="aisi_1045", operations=["stamping"])
        alum = plan_manufacturing(material_id="aa6061_t6", operations=["stamping"])
        steel_cost = estimate_cost(steel)
        alum_cost = estimate_cost(alum)
        assert alum_cost["material_usd"] > steel_cost["material_usd"]

    def test_unknown_material_in_route_yields_zero_material_cost(self) -> None:
        card = RouteCard(
            steps=[ProcessStep(operation="drilling")],
            material_id="nonexistent",
            state="ok",
            quantity=1,
        )
        cost = estimate_cost(card)
        assert cost["state"] == "ok"
        assert cost["material_usd"] == 0.0

    def test_polymer_material_has_cost(self) -> None:
        route = plan_manufacturing(material_id="pa66_gf30", operations=["injection_molding"], quantity=10)
        cost = estimate_cost(route)
        assert cost["total_usd"] > 0
        assert cost["quantity"] == 10


# ---------------------------------------------------------------------------
# Deep: energy estimation — known vs default
# ---------------------------------------------------------------------------


class TestEnergyEdgeCases:
    def test_unknown_operation_uses_default_energy(self) -> None:
        card = RouteCard(
            steps=[ProcessStep(operation="unknown_op")],
            material_id="aisi_1045",
            state="ok",
        )
        energy = estimate_energy(card)
        assert energy["total_kwh"] > 0

    def test_empty_operations_uses_default_scrap(self) -> None:
        route = plan_manufacturing(material_id="aisi_1045", operations=["unknown_op"])
        assert route.sustainability["scrap_rate_pct"] >= 0.0


# ---------------------------------------------------------------------------
# Deep: operation classification sets
# ---------------------------------------------------------------------------


class TestOperationClassificationSets:
    def test_forming_includes_casting_and_additive(self) -> None:
        assert "casting" in FORMING_OPS
        assert "additive" in FORMING_OPS

    def test_joining_includes_brazing_and_soldering(self) -> None:
        assert "brazing" in JOINING_OPS
        assert "soldering" in JOINING_OPS

    def test_machining_includes_edm_and_waterjet(self) -> None:
        assert "edm" in MACHINING_OPS
        assert "waterjet" in MACHINING_OPS

    def test_finishing_includes_anodizing_and_heat_treatment(self) -> None:
        assert "anodizing" in FINISHING_OPS
        assert "heat_treatment" in FINISHING_OPS


# ---------------------------------------------------------------------------
# Deep: finishing operation route
# ---------------------------------------------------------------------------


class TestFinishingOperationRoute:
    def test_finishing_only_route(self) -> None:
        route = plan_manufacturing(
            material_id="aa6061_t6",
            operations=["anodizing"],
        )
        assert route.state == "ok"
        assert len(route.steps) == 1
        step = route.steps[0]
        assert "anodize" in step.equipment_class.lower()
        assert "coating thickness" in step.quality_gate["criterion"].lower()
        assert step.inputs
        assert step.outputs

    def test_finishing_step_has_inspection_method(self) -> None:
        route = plan_manufacturing(
            material_id="aa6061_t6",
            operations=["anodizing", "plating"],
        )
        for step in route.steps:
            assert step.inspection["method_ref"]


# ---------------------------------------------------------------------------
# Deep: inspection plan with multiple steps
# ---------------------------------------------------------------------------


class TestInspectionPlanMultiStep:
    def test_three_step_route_has_5_measurements(self) -> None:
        route = plan_manufacturing(
            material_id="aisi_1045",
            operations=["stamping", "drilling", "gmaw"],
        )
        plan = plan_inspection(route)
        assert len(plan["measurements"]) == 5

    def test_incoming_measurement_is_first(self) -> None:
        route = plan_manufacturing(
            material_id="aisi_1045",
            operations=["stamping"],
        )
        plan = plan_inspection(route)
        assert plan["measurements"][0]["stage"] == "incoming"

    def test_final_measurement_is_last(self) -> None:
        route = plan_manufacturing(
            material_id="aisi_1045",
            operations=["stamping"],
        )
        plan = plan_inspection(route)
        assert plan["measurements"][-1]["stage"] == "final"
