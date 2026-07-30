"""Manufacturing route-card and process-planning integration (spec MATE-001 §3,
§9 — roles ``manufacturing_plan`` and ``inspection_plan``, MATE-P5).

Combines forming / joining / machining / finishing operations into a single
traceable route card with quality gates between stages, cost/energy/waste
estimation, scale-up notes, and an incoming/in-process/final inspection plan.

Per MATE-AT-005 the route preserves traceability from design requirements to
each process control and inspection step. Per §9 the route is the unit of ZDD
promotion (BASELINE → COUPON → PILOT → SHADOW → RAMP → PRODUCTION).

All estimates are handbook-grade nominal values carrying ``basis`` and
``uncertainty``; they are not a substitute for lot-specific process data
(MATE-SAFE-003, MATE-DEC-003).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from general_ludd.materials.core import (
    INSUFFICIENT_DATA,
    METAL_FORMING_OPS,
    POLYMER_PROCESSES,
    lookup_material,
)

# ---------------------------------------------------------------------------
# Process classification — where a given operation sits in the manufacturing
# sequence. Forming precedes joining, which precedes machining/finishing.
# ---------------------------------------------------------------------------

FORMING_OPS: frozenset[str] = frozenset(set(POLYMER_PROCESSES) | set(METAL_FORMING_OPS) | {"casting", "additive"})

# Joining processes (mirrors joining.py FUSION/SOLID_STATE/COLD/PRESSURE union).
JOINING_OPS: frozenset[str] = frozenset(
    {
        "gmaw",
        "gtaw",
        "tig",
        "smaw",
        "friction_stir_welding",
        "fsw",
        "diffusion_bonding",
        "ultrasonic_welding",
        "resistance_welding",
        "brazing",
        "soldering",
        "adhesive_bonding",
        "mechanical_fastening",
    }
)

MACHINING_OPS: frozenset[str] = frozenset(
    {
        "milling",
        "turning",
        "drilling",
        "boring",
        "reaming",
        "grinding",
        "honing",
        "edm",
        "waterjet",
        "laser_machining",
    }
)

FINISHING_OPS: frozenset[str] = frozenset(
    {
        "anodizing",
        "plating",
        "painting",
        "powder_coating",
        "heat_treatment",
        "shot_peening",
        "polishing",
        "passivation",
    }
)

# Nominal energy intensity (kWh per part) per operation class.
# Source basis: ASM Energy Handbook / NIST manufacturing energy surveys; wide
# variation by machine size and material — these are order-of-magnitude figures.
_ENERGY_KWH_PER_OP: dict[str, float] = {
    "forging": 18.0,
    "casting": 25.0,
    "stamping": 4.0,
    "rolling": 6.0,
    "drawing": 3.0,
    "bending": 2.0,
    "spinning": 3.0,
    "hydroforming": 5.0,
    "injection_molding": 8.0,
    "extrusion": 10.0,
    "compression_molding": 6.0,
    "additive": 15.0,
    "milling": 5.0,
    "turning": 3.5,
    "drilling": 1.5,
    "boring": 4.0,
    "reaming": 1.0,
    "grinding": 6.0,
    "honing": 2.0,
    "edm": 8.0,
    "waterjet": 4.0,
    "laser_machining": 7.0,
    "gmaw": 12.0,
    "gtaw": 10.0,
    "tig": 10.0,
    "smaw": 9.0,
    "friction_stir_welding": 11.0,
    "fsw": 11.0,
    "brazing": 6.0,
    "soldering": 2.0,
    "adhesive_bonding": 1.0,
    "anodizing": 3.0,
    "plating": 5.0,
    "painting": 2.0,
    "powder_coating": 3.0,
    "heat_treatment": 14.0,
    "shot_peening": 2.0,
    "polishing": 1.5,
}

_DEFAULT_ENERGY_KWH = 4.0

# Nominal cycle time (minutes per part) for labor costing.
_CYCLE_TIME_MIN_PER_OP: dict[str, float] = {
    "forging": 2.5,
    "casting": 8.0,
    "stamping": 0.3,
    "rolling": 1.0,
    "injection_molding": 0.8,
    "extrusion": 1.5,
    "compression_molding": 2.0,
    "additive": 60.0,
    "milling": 4.0,
    "turning": 3.0,
    "drilling": 0.5,
    "grinding": 2.0,
    "gmaw": 3.0,
    "gtaw": 4.0,
    "tig": 4.0,
    "smaw": 3.5,
    "brazing": 2.0,
    "soldering": 0.5,
    "adhesive_bonding": 5.0,
    "anodizing": 2.0,
    "plating": 3.0,
    "painting": 1.5,
    "heat_treatment": 30.0,
}

_DEFAULT_CYCLE_TIME_MIN = 2.0

# Nominal scrap rate (percent) per operation class.
_SCRAP_PCT_PER_OP: dict[str, float] = {
    "stamping": 15.0,
    "forging": 8.0,
    "casting": 12.0,
    "milling": 10.0,
    "turning": 12.0,
    "drilling": 3.0,
    "grinding": 5.0,
    "injection_molding": 4.0,
    "extrusion": 6.0,
    "gmaw": 5.0,
    "brazing": 4.0,
}

_DEFAULT_SCRAP_PCT = 5.0

# Cost rates (USD).
_LABOR_RATE_PER_HR = 55.0
_ENERGY_RATE_PER_KWH = 0.12
_OVERHEAD_MULTIPLIER = 1.6  # overhead as fraction of (material+labor+energy)

# Nominal material density (kg/m^3) and price (USD/kg) for material cost.
_MATERIAL_COST: dict[str, tuple[float, float]] = {
    "aisi_1045": (7850.0, 1.2),  # medium carbon steel
    "aa6061_t6": (2700.0, 4.5),  # aluminum alloy
    "pa66_gf30": (1350.0, 3.8),  # glass-filled nylon
    "abs": (1050.0, 2.2),
    "epoxy_cast": (1150.0, 6.0),
}

_DEFAULT_PART_MASS_KG = 0.5


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class ProcessStep:
    """A single operation in a manufacturing route.

    Attributes:
        operation: canonical operation name (stamping, milling, gmaw, ...).
        equipment_class: equipment family required (vmc, progressive_die_press, ...).
        parameters: process parameters (temperature, feed rate, current, ...).
        inputs: incoming material / sub-assembly state required.
        outputs: outgoing state / feature produced.
        quality_gate: inspection criterion that must pass before the next stage.
        inspection: measurement details tied to the quality gate.
    """

    operation: str
    equipment_class: str = ""
    parameters: dict[str, Any] = field(default_factory=dict)
    inputs: list[str] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)
    quality_gate: dict[str, Any] = field(default_factory=dict)
    inspection: dict[str, Any] = field(default_factory=dict)


@dataclass
class RouteCard:
    """A complete manufacturing route: ordered steps + plan-level metadata.

    Per spec §3 ``manufacturing_plan``: combines processes, quality gates,
    cost, energy, waste, repair, recycling, and scale-up into a single card.
    Per spec §9: the route is the ZDD promotion unit.
    """

    steps: list[ProcessStep] = field(default_factory=list)
    route_id: str = field(default_factory=lambda: f"route-{uuid.uuid4().hex[:8]}")
    material_id: str = ""
    quantity: int = 1
    state: str = "ok"
    reason: str = ""
    sustainability: dict[str, Any] = field(default_factory=dict)
    traceability: dict[str, Any] = field(default_factory=dict)
    notes: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Sequence ordering: forming → joining → machining → finishing
# ---------------------------------------------------------------------------


def _sequence_rank(operation: str) -> int:
    """Return the manufacturing-sequence rank for an operation.

    Lower rank = earlier in the route.
    """
    if operation in FORMING_OPS:
        return 0
    if operation in JOINING_OPS:
        return 1
    if operation in MACHINING_OPS:
        return 2
    if operation in FINISHING_OPS:
        return 3
    return 2  # default to machining-stage position


def _equipment_for(operation: str) -> str:
    """Nominal equipment class for an operation."""
    _EQUIP: dict[str, str] = {
        "stamping": "progressive_die_press",
        "forging": "hydraulic_forge_press",
        "rolling": "rolling_mill",
        "drawing": "draw_bench",
        "bending": "press_brake",
        "spinning": "cnc_spinner",
        "hydroforming": "hydroform_press",
        "injection_molding": "injection_molding_machine",
        "extrusion": "extruder",
        "compression_molding": "compression_press",
        "casting": "die_cast_machine",
        "additive": "powder_bed_fusion_system",
        "milling": "vmc",
        "turning": "cnc_lathe",
        "drilling": "drill_press",
        "boring": "boring_mill",
        "reaming": "vmc",
        "grinding": "surface_grinder",
        "honing": "honing_machine",
        "edm": "edm_sink",
        "waterjet": "abrasive_waterjet",
        "laser_machining": "fiber_laser",
        "gmaw": "robotic_weld_cell",
        "gtaw": "tig_station",
        "tig": "tig_station",
        "smaw": "manual_weld_station",
        "friction_stir_welding": "fsw_machine",
        "fsw": "fsw_machine",
        "brazing": "vacuum_furnace",
        "soldering": "wave_solder",
        "adhesive_bonding": "dispense_cure_oven",
        "anodizing": "anodize_tank_line",
        "plating": "plating_tank_line",
        "painting": "spray_booth",
        "powder_coating": "powder_coat_booth",
        "heat_treatment": "atmosphere_furnace",
        "shot_peening": "peening_cabinet",
        "polishing": "polishing_station",
    }
    return _EQUIP.get(operation, "general_purpose")


def _inputs_outputs_for(operation: str, material_id: str) -> tuple[list[str], list[str]]:
    """Return (inputs, outputs) declared for an operation."""
    base = [f"material:{material_id}", "work_order"]
    forming = ["raw_stock", "billet_or_sheet"]
    joining = ["fixture", "consumable", "shielding_gas"]
    machining = ["datum_scheme", "fixture", "tooling"]
    finishing = ["surface_prep", "chemistry"]

    if operation in FORMING_OPS:
        return base + forming, [f"formed_feature:{operation}"]
    if operation in JOINING_OPS:
        return base + joining, ["joined_assembly"]
    if operation in MACHINING_OPS:
        return base + machining, [f"machined_feature:{operation}"]
    if operation in FINISHING_OPS:
        return base + finishing, [f"finished_surface:{operation}"]
    return base, [f"feature:{operation}"]


def _quality_gate_for(operation: str, material_id: str) -> dict[str, Any]:
    """Nominal quality gate criterion for an operation."""
    if operation in FORMING_OPS:
        return {
            "stage": "post_form",
            "criterion": "dimensional + visual; no cracks, tears, or thinning beyond spec",
            "inspection_method": "calipers + visual per ASTM E165 if surface crack-suspect",
        }
    if operation in JOINING_OPS:
        return {
            "stage": "post_join",
            "criterion": "weld bead geometry + penetration; no undercut, porosity, or cracks",
            "inspection_method": "VT (AWS D1.1) + dye-penetrant or X-ray per weld class",
        }
    if operation in MACHINING_OPS:
        return {
            "stage": "post_machine",
            "criterion": "dimensional tolerance per drawing; surface finish Ra within spec",
            "inspection_method": "CMM per ASME Y14.5 + profilometer",
        }
    if operation in FINISHING_OPS:
        return {
            "stage": "post_finish",
            "criterion": "coating thickness / surface treatment meets spec",
            "inspection_method": "eddy-current thickness gauge or cross-section per ASTM B487",
        }
    return {
        "stage": "post_op",
        "criterion": "conformance to drawing requirements",
        "inspection_method": "CMM / visual per inspection plan",
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def plan_manufacturing(
    material_id: str,
    operations: list[str],
    quantity: int = 1,
    requirement_refs: list[str] | None = None,
) -> RouteCard:
    """Combine forming / joining / machining / finishing ops into a route card.

    Per spec §3 ``manufacturing_plan``: produces a traceable route card with
    quality gates between stages, cost/energy/waste estimates, and scale-up
    notes. An empty operation list returns ``insufficient_data``.
    """
    if not operations:
        return RouteCard(
            steps=[],
            material_id=material_id,
            quantity=quantity,
            state=INSUFFICIENT_DATA,
            reason="no operations specified",
        )

    mat = lookup_material(material_id)
    if mat is None:
        return RouteCard(
            steps=[],
            material_id=material_id,
            quantity=quantity,
            state=INSUFFICIENT_DATA,
            reason="unknown material",
        )

    # Order operations by manufacturing sequence rank.
    ordered = sorted(operations, key=_sequence_rank)

    steps: list[ProcessStep] = []
    for op in ordered:
        inputs, outputs = _inputs_outputs_for(op, material_id)
        steps.append(
            ProcessStep(
                operation=op,
                equipment_class=_equipment_for(op),
                parameters={"nominal_cycle_time_min": _CYCLE_TIME_MIN_PER_OP.get(op, _DEFAULT_CYCLE_TIME_MIN)},
                inputs=inputs,
                outputs=outputs,
                quality_gate=_quality_gate_for(op, material_id),
                inspection={"method_ref": _quality_gate_for(op, material_id)["inspection_method"]},
            )
        )

    # Scrap / yield sustainability estimate.
    scrap_rate = max((_SCRAP_PCT_PER_OP.get(op, _DEFAULT_SCRAP_PCT) for op in ordered), default=0.0)
    material_yield = max(0.0, 100.0 - scrap_rate)

    sustainability = {
        "material_yield_pct": material_yield,
        "scrap_rate_pct": scrap_rate,
        "basis": "nominal handbook scrap-rate per operation; verify with lot data",
    }

    # Scale-up note — distinguishes prototype / pilot / production.
    if quantity <= 10:
        scale_note = (
            "prototype/low-volume route; manual load/unload acceptable; "
            "consider soft tooling and first-article inspection"
        )
    elif quantity <= 1000:
        scale_note = "pilot/bridge production; harden tooling and validate process capability (Cpk >= 1.33) before ramp"
    else:
        scale_note = (
            "production volume; require automated material handling, SPC "
            "monitoring, and qualified operator training program"
        )

    notes = {
        "scale_up": scale_note,
        "zdd_stage": "BASELINE",
        "human_approval_required": True,
    }

    traceability = {
        "requirement_refs": list(requirement_refs) if requirement_refs else [],
        "material_source": mat.get("source", {}),
        "route_digest_seed": f"{material_id}:{':'.join(ordered)}",
    }

    return RouteCard(
        steps=steps,
        material_id=material_id,
        quantity=quantity,
        state="ok",
        sustainability=sustainability,
        traceability=traceability,
        notes=notes,
    )


def estimate_cost(route: RouteCard) -> dict[str, Any]:
    """Estimate total cost (USD) for a route: material + labor + energy + overhead.

    Material cost is based on nominal part mass * price per kg. Labor is based
    on total cycle time * labor rate. Energy comes from ``estimate_energy``.
    Overhead is applied as a multiplier on direct cost.
    """
    if route.state == INSUFFICIENT_DATA:
        return {
            "total_usd": 0.0,
            "state": INSUFFICIENT_DATA,
            "reason": "route has no valid operations",
        }

    _density, price_per_kg = _MATERIAL_COST.get(route.material_id, (0.0, 0.0))
    # Nominal part mass — conservative 0.5 kg default; real planning needs CAD.
    part_mass = _DEFAULT_PART_MASS_KG
    material_cost = part_mass * price_per_kg

    # Labor: sum of cycle times across steps.
    total_cycle_min = sum(s.parameters.get("nominal_cycle_time_min", _DEFAULT_CYCLE_TIME_MIN) for s in route.steps)
    labor_cost = (total_cycle_min / 60.0) * _LABOR_RATE_PER_HR

    # Energy.
    energy = estimate_energy(route)
    energy_cost = energy["total_kwh"] * _ENERGY_RATE_PER_KWH

    direct = material_cost + labor_cost + energy_cost
    overhead = direct * (_OVERHEAD_MULTIPLIER - 1.0)
    per_part = direct + overhead
    total = per_part * route.quantity

    return {
        "material_usd": round(material_cost * route.quantity, 4),
        "labor_usd": round(labor_cost * route.quantity, 4),
        "energy_usd": round(energy_cost * route.quantity, 4),
        "overhead_usd": round(overhead * route.quantity, 4),
        "per_part_usd": round(per_part, 4),
        "total_usd": round(total, 4),
        "quantity": route.quantity,
        "unit": "USD",
        "basis": "nominal handbook rates; verify with supplier quotes and ERP actuals",
        "state": "ok",
    }


def estimate_energy(route: RouteCard) -> dict[str, Any]:
    """Estimate total energy (kWh) for a route by summing per-step intensities."""
    if route.state == INSUFFICIENT_DATA:
        return {
            "total_kwh": 0.0,
            "per_step_kwh": [],
            "state": INSUFFICIENT_DATA,
        }

    per_step: list[float] = []
    for step in route.steps:
        kwh = _ENERGY_KWH_PER_OP.get(step.operation, _DEFAULT_ENERGY_KWH)
        per_step.append(kwh)

    return {
        "total_kwh": round(sum(per_step), 4),
        "per_step_kwh": per_step,
        "unit": "kWh_per_part",
        "basis": "ASM Energy Handbook / NIST nominal energy intensity per operation class",
        "uncertainty": "machine-size and material-lot dependent; +/-50% typical",
    }


def plan_inspection(route: RouteCard) -> dict[str, Any]:
    """Plan incoming / in-process / final measurements for a route.

    Per spec §3 ``inspection_plan``: defines incoming, in-process, and final
    measurements with acceptance criteria and traceability. Every measurement
    carries an acceptance criterion.
    """
    if route.state == INSUFFICIENT_DATA:
        return {
            "plan_id": route.route_id,
            "measurements": [],
            "state": INSUFFICIENT_DATA,
        }

    measurements: list[dict[str, Any]] = []

    # Incoming: material certification + chemistry check.
    measurements.append(
        {
            "stage": "incoming",
            "feature": "material_certification",
            "method": "review mill test report (EN 10204 3.1)",
            "acceptance": "chemistry + mechanical properties within material spec range",
            "traceability": f"material_id={route.material_id}",
        }
    )

    # In-process: per-step quality gates.
    for i, step in enumerate(route.steps):
        gate = step.quality_gate
        measurements.append(
            {
                "stage": "in_process",
                "feature": f"step_{i + 1}:{step.operation}",
                "method": gate.get("inspection_method", "visual"),
                "acceptance": gate.get("criterion", "per operation control plan"),
                "traceability": f"route={route.route_id} step={i + 1}",
            }
        )

    # Final: dimensional + functional acceptance.
    measurements.append(
        {
            "stage": "final",
            "feature": "final_dimensional_and_functional",
            "method": "CMM full-layout + functional gauge / leak / torque-to-spec",
            "acceptance": (
                "all dimensions within drawing tolerance; functional test passes; "
                "first-article inspection report signed"
            ),
            "traceability": f"route={route.route_id} qty={route.quantity}",
        }
    )

    return {
        "plan_id": route.route_id,
        "measurements": measurements,
        "state": "ok",
        "basis": "ISO 9001 / AS9100 inspection plan template; tailor to drawing requirements",
    }


__all__ = [
    "FINISHING_OPS",
    "FORMING_OPS",
    "JOINING_OPS",
    "MACHINING_OPS",
    "ProcessStep",
    "RouteCard",
    "estimate_cost",
    "estimate_energy",
    "plan_inspection",
    "plan_manufacturing",
]
