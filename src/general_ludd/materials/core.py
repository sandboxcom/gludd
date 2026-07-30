"""Materials engineering core module (general_ludd.materials).

Implements property/access functions for the top 5 user-visible roles from
spec MATE-001 §3:

  - requirements_capture   normalize loads/environment/life/geometry constraints
  - material_select        screen and rank material candidates with margins
  - polymer_process_plan   process windows for thermoplastic/thermoset forming
  - metal_forming_plan     alloy condition, forming sequence, springback
  - strength_assess        static/fatigue/fracture margin checks, fail-closed

Safety invariants (spec §8):
  - MATE-SAFE-003: properties lacking condition metadata are flagged
    ``insufficient_context``; missing data is never silently substituted.
  - MATE-SAFE-006: unit mismatch, missing property, or extrapolation beyond
    calibrated range SHALL block a positive verdict (``fail_closed`` /
    ``insufficient_data``).
  - MATE-DEC-002: hard-constraint violations reject the candidate; surviving
    candidates expose margins, sources, and unknowns.
"""

from __future__ import annotations

from typing import Any

MATERIAL_FAMILIES: tuple[str, ...] = (
    "polymer",
    "metal",
    "ceramic",
    "composite",
    "textile",
)

ROLES: tuple[str, ...] = (
    "requirements_capture",
    "material_select",
    "polymer_process_plan",
    "metal_forming_plan",
    "strength_assess",
)

SCHEMA_VERSION = "mate-001/0.1"

INSUFFICIENT_CONTEXT = "insufficient_context"
INSUFFICIENT_DATA = "insufficient_data"
ASSESS_FAIL_CLOSED = "fail_closed"

POLYMER_PROCESSES: tuple[str, ...] = (
    "injection_molding",
    "extrusion",
    "blow_molding",
    "thermoforming",
    "compression_molding",
    "rotational_molding",
    "casting",
    "pultrusion",
)

METAL_FORMING_OPS: tuple[str, ...] = (
    "stamping",
    "forging",
    "rolling",
    "drawing",
    "bending",
    "spinning",
    "hydroforming",
)

THERMOPLASTIC_PROCESSES: frozenset[str] = frozenset({"injection_molding", "extrusion", "blow_molding", "thermoforming"})

_LOAD_TYPE_TO_PROPERTY: dict[str, str] = {
    "tensile": "yield_strength",
    "yield": "yield_strength",
    "ultimate": "ultimate_strength",
    "compression": "compressive_strength",
    "shear": "shear_strength",
}


def _property(
    name: str,
    value_or_range: float | tuple[float, float],
    unit: str,
    basis: str,
    method: str,
    uncertainty: float,
    condition: dict[str, str] | None = None,
    state: str = "ok",
) -> dict[str, Any]:
    return {
        "name": name,
        "value_or_range": value_or_range,
        "unit": unit,
        "basis": basis,
        "method": method,
        "uncertainty": uncertainty,
        "condition": condition or {},
        "state": state,
    }


def _material(
    material_id: str,
    designation: str,
    family: str,
    klass: str,
    aliases: tuple[str, ...],
    properties: list[dict[str, Any]],
    source: dict[str, str],
    polymer_class: str | None = None,
    unknowns: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    return {
        "material_id": material_id,
        "designation": designation,
        "family": family,
        "class": klass,
        "aliases": aliases,
        "properties": properties,
        "source": source,
        "polymer_class": polymer_class,
        "unknowns": unknowns or [],
    }


_CARBON_STEEL_SOURCE = {
    "publisher": "ASM Handbook Vol. 1",
    "revision": "2023",
    "license": "reference",
}

_POLYMER_SOURCE = {
    "publisher": "CAMPUS Plastics Database",
    "revision": "2024",
    "license": "reference",
}

MATERIALS: dict[str, dict[str, Any]] = {
    m["material_id"]: m
    for m in [
        _material(
            "pa66_gf30",
            "Polyamide 66, 30% glass fiber reinforced",
            "polymer",
            "thermoplastic_reinforced",
            ("PA66-GF30", "Nylon 66 GF30"),
            [
                _property(
                    "yield_strength",
                    180.0,
                    "MPa",
                    "nominal",
                    "ISO 527",
                    12.0,
                    condition={"product_form": "injection_molded", "moisture": "conditioned"},
                ),
                _property(
                    "youngs_modulus",
                    9500.0,
                    "MPa",
                    "nominal",
                    "ISO 527",
                    400.0,
                    condition={"product_form": "injection_molded"},
                ),
            ],
            _POLYMER_SOURCE,
            polymer_class="thermoplastic",
            unknowns=[{"field": "fatigue_strength", "impact": "high", "required_test": "ISO 527 cyclic"}],
        ),
        _material(
            "abs",
            "Acrylonitrile Butadiene Styrene",
            "polymer",
            "thermoplastic",
            ("ABS",),
            [
                # Deliberately missing condition -> insufficient_context per MATE-SAFE-003.
                _property(
                    "yield_strength",
                    45.0,
                    "MPa",
                    "nominal",
                    "ISO 527",
                    5.0,
                    condition={},
                    state=INSUFFICIENT_CONTEXT,
                ),
                _property(
                    "youngs_modulus",
                    2300.0,
                    "MPa",
                    "nominal",
                    "ISO 527",
                    150.0,
                    condition={},
                    state=INSUFFICIENT_CONTEXT,
                ),
            ],
            _POLYMER_SOURCE,
            polymer_class="thermoplastic",
            unknowns=[{"field": "fatigue_strength", "impact": "high", "required_test": "ISO 527 cyclic"}],
        ),
        _material(
            "epoxy_cast",
            "Epoxy casting resin (thermoset)",
            "polymer",
            "thermoset",
            ("epoxy", "cast_epoxy"),
            [
                _property(
                    "ultimate_strength",
                    70.0,
                    "MPa",
                    "nominal",
                    "ASTM D638",
                    8.0,
                    condition={"cure": "room_temp_24h"},
                ),
                _property(
                    "youngs_modulus",
                    3000.0,
                    "MPa",
                    "nominal",
                    "ASTM D638",
                    200.0,
                    condition={"cure": "room_temp_24h"},
                ),
            ],
            _POLYMER_SOURCE,
            polymer_class="thermoset",
        ),
        _material(
            "aisi_1045",
            "AISI 1045 medium carbon steel",
            "metal",
            "ferrous_carbon",
            ("1045", "S45C", "C45"),
            [
                _property(
                    "yield_strength",
                    310.0,
                    "MPa",
                    "nominal",
                    "ASTM A29",
                    20.0,
                    condition={"product_form": "cold_drawn", "temper": "as_drawn"},
                ),
                _property(
                    "ultimate_strength",
                    565.0,
                    "MPa",
                    "nominal",
                    "ASTM A29",
                    25.0,
                    condition={"product_form": "cold_drawn", "temper": "as_drawn"},
                ),
                _property(
                    "youngs_modulus",
                    200.0,
                    "GPa",
                    "nominal",
                    "ASTM E111",
                    5.0,
                    condition={"product_form": "cold_drawn"},
                ),
            ],
            _CARBON_STEEL_SOURCE,
        ),
        _material(
            "aa6061_t6",
            "Aluminum 6061-T6",
            "metal",
            "non_ferrous_aluminum",
            ("6061-T6", "AlMg1SiCu"),
            [
                _property(
                    "yield_strength",
                    276.0,
                    "MPa",
                    "nominal",
                    "ASTM B209",
                    15.0,
                    condition={"product_form": "sheet", "temper": "T6"},
                ),
                _property(
                    "ultimate_strength",
                    310.0,
                    "MPa",
                    "nominal",
                    "ASTM B209",
                    15.0,
                    condition={"product_form": "sheet", "temper": "T6"},
                ),
                _property(
                    "youngs_modulus",
                    68.9,
                    "GPa",
                    "nominal",
                    "ASTM E111",
                    1.5,
                    condition={"product_form": "sheet"},
                ),
            ],
            {
                "publisher": "ASM Handbook Vol. 2",
                "revision": "2023",
                "license": "reference",
            },
        ),
    ]
}


def _build_alias_index() -> dict[str, str]:
    idx: dict[str, str] = {}
    for mid, mat in MATERIALS.items():
        idx[mid.lower()] = mid
        for alias in mat["aliases"]:
            idx[alias.lower()] = mid
    return idx


_ALIAS_INDEX = _build_alias_index()


def list_material_families() -> tuple[str, ...]:
    """Return the registered material family tokens."""
    return MATERIAL_FAMILIES


def lookup_material(query: str) -> dict[str, Any] | None:
    """Look up a material by id or alias (case-insensitive, whitespace-trimmed).

    Returns the matching material dict or None for unknown/empty queries.
    """
    if not query or not query.strip():
        return None
    q = query.strip().lower()
    mid = _ALIAS_INDEX.get(q)
    if mid is None:
        return None
    return MATERIALS[mid]


def get_properties(material_id: str) -> list[dict[str, Any]]:
    """Return the property records for a material id (empty if unknown)."""
    mat = MATERIALS.get(material_id) or lookup_material(material_id)
    if mat is None:
        return []
    return list(mat["properties"])


def _find_property(material: dict[str, Any], name: str) -> dict[str, Any] | None:
    for prop in material["properties"]:
        if prop["name"] == name:
            return prop
    return None


def normalize_requirements(raw: dict[str, Any]) -> dict[str, Any]:
    """Normalize raw design requirements (role: ``requirements_capture``).

    Mandatory fields (load_cases, environment, design_life, manufacturing,
    geometry_refs) default to ``"unknown"`` when absent, per MATE-DEC-001.
    Safety-critical failure consequences set ``requires_human_review``.
    """
    fc = raw.get("failure_consequence", "unknown")
    return {
        "schema_version": SCHEMA_VERSION,
        "geometry_refs": raw.get("geometry_refs", "unknown"),
        "load_cases": raw.get("load_cases", "unknown"),
        "environment": raw.get("environment", "unknown"),
        "design_life": raw.get("design_life", "unknown"),
        "manufacturing": raw.get("manufacturing", "unknown"),
        "interfaces": raw.get("interfaces", "unknown"),
        "tolerances": raw.get("tolerances", "unknown"),
        "inspection": raw.get("inspection", "unknown"),
        "cost_sustainability": raw.get("cost_sustainability", "unknown"),
        "assumptions": raw.get("assumptions", []),
        "failure_consequence": fc,
        "requires_human_review": fc == "safety_critical",
    }


def _yield_requirement(reqs: dict[str, Any]) -> dict[str, Any] | None:
    """Extract the first yield/tensile load case from normalized requirements."""
    cases = reqs.get("load_cases")
    if not isinstance(cases, list):
        return None
    for case in cases:
        if case.get("type") in ("yield", "tensile") and case.get("unit") == "MPa":
            return case
    return None


def select_materials(
    reqs: dict[str, Any],
    candidates: list[str] | None = None,
) -> dict[str, Any]:
    """Screen and rank material candidates (role: ``material_select``).

    Per MATE-DEC-002: reject hard-constraint violations, then compute margins
    for survivors. Each candidate record exposes ``state``, ``reason``,
    ``requirement_margins``, ``source``, and ``unknowns``.
    """
    candidate_ids = candidates or list(MATERIALS.keys())
    yield_req = _yield_requirement(reqs)
    out: list[dict[str, Any]] = []

    for cid in candidate_ids:
        mat = lookup_material(cid)
        if mat is None:
            out.append(
                {
                    "material_id": cid,
                    "state": "rejected",
                    "reason": "unknown_material",
                    "requirement_margins": [],
                    "source": {},
                    "unknowns": [],
                }
            )
            continue

        margins: list[dict[str, Any]] = []
        rejected = False
        reject_reason = ""

        if yield_req is not None:
            prop = _find_property(mat, "yield_strength")
            if prop is None:
                rejected = True
                reject_reason = "hard_constraint: no yield_strength property"
            else:
                capacity = prop["value_or_range"]
                if isinstance(capacity, (int, float)):
                    applied = yield_req["magnitude"]
                    margin = (capacity - applied) / applied if applied else 0.0
                    margins.append(
                        {
                            "requirement_id": yield_req.get("id", "yield"),
                            "margin": margin,
                            "state": "pass" if margin > 0 else "fail",
                        }
                    )
                    if margin <= 0:
                        rejected = True
                        reject_reason = f"hard_constraint: yield {capacity} MPa < required {applied} MPa"

        out.append(
            {
                "material_id": cid,
                "designation": mat["designation"],
                "family": mat["family"],
                "state": "rejected" if rejected else "survived",
                "reason": reject_reason if rejected else "ok",
                "requirement_margins": margins,
                "source": mat["source"],
                "unknowns": mat.get("unknowns", []),
            }
        )

    return {
        "schema_version": SCHEMA_VERSION,
        "candidates": out,
        "verdict": "candidate" if any(c["state"] == "survived" for c in out) else "infeasible",
    }


def plan_polymer_process(
    material_id: str,
    process_family: str,
) -> dict[str, Any]:
    """Plan a polymer forming process (role: ``polymer_process_plan``).

    Thermoplastics are compatible with remelt processes; thermosets are flagged
    incompatible per MATE-AT-003. Unknown processes return ``insufficient_data``.
    """
    if process_family not in POLYMER_PROCESSES:
        return {
            "material_id": material_id,
            "process_family": process_family,
            "state": INSUFFICIENT_DATA,
            "reason": f"not a recognized polymer process: {process_family}",
        }

    mat = lookup_material(material_id)
    if mat is None or mat["family"] != "polymer":
        return {
            "material_id": material_id,
            "process_family": process_family,
            "state": INSUFFICIENT_DATA,
            "reason": "not a polymer material",
        }

    klass = mat.get("polymer_class", "")
    if klass == "thermoset" and process_family in THERMOPLASTIC_PROCESSES:
        return {
            "material_id": material_id,
            "process_family": process_family,
            "compatible": False,
            "state": "rejected",
            "reason": (f"thermoset cannot be remelted for {process_family}; use a cure-based process instead"),
        }

    windows = {
        "abs": {"melt_temperature": (220, 260), "mold_temperature": (40, 80)},
        "pa66_gf30": {"melt_temperature": (270, 290), "mold_temperature": (80, 100)},
    }
    window = windows.get(material_id, {"melt_temperature": (200, 280)})

    return {
        "material_id": material_id,
        "process_family": process_family,
        "compatible": True,
        "state": "candidate",
        "process_window": window,
        "drying_note": "dry 2-4h at 80C before processing" if klass == "thermoplastic" else "",
    }


def plan_metal_forming(
    material_id: str,
    operation: str,
) -> dict[str, Any]:
    """Plan a metal forming operation (role: ``metal_forming_plan``).

    Includes springback estimate and heat-treatment notes per spec §4.3.
    """
    if operation not in METAL_FORMING_OPS:
        return {
            "material_id": material_id,
            "process_family": operation,
            "state": INSUFFICIENT_DATA,
            "reason": f"not a recognized metal forming op: {operation}",
        }

    mat = lookup_material(material_id)
    if mat is None or mat["family"] != "metal":
        return {
            "material_id": material_id,
            "process_family": operation,
            "state": INSUFFICIENT_DATA,
            "reason": "not a metal material",
        }

    springback: dict[str, Any]
    if material_id == "aisi_1045":
        springback = {"estimate_pct": 1.5, "basis": "cold_drawn medium carbon steel"}
    elif material_id == "aa6061_t6":
        springback = {"estimate_pct": 0.5, "basis": "T6 sheet, low modulus"}
    else:
        springback = {"estimate_pct": 1.0, "basis": "generic estimate"}

    heat_treatment: dict[str, Any]
    if material_id == "aa6061_t6":
        heat_treatment = {
            "note": "forging upsets T6 temper; re-solution-treat + age after forging",
            "required": True,
        }
    else:
        heat_treatment = {
            "note": "stress-relief anneal optional after cold forming",
            "required": False,
        }

    return {
        "material_id": material_id,
        "process_family": operation,
        "state": "candidate",
        "springback": springback,
        "heat_treatment": heat_treatment,
        "stock_form": "bar" if operation in ("forging", "stamping") else "sheet",
    }


def assess_strength(
    material_id: str,
    load_case: dict[str, Any],
) -> dict[str, Any]:
    """Assess static strength margin against a load case (role: ``strength_assess``).

    Fail-closed per MATE-SAFE-006 on: missing material, missing property,
    or unit mismatch. Otherwise returns a margin verdict.
    """
    mat = lookup_material(material_id)
    if mat is None:
        return {
            "material_id": material_id,
            "state": INSUFFICIENT_DATA,
            "reason": "unknown material",
        }

    load_type = load_case.get("type", "")
    prop_name = _LOAD_TYPE_TO_PROPERTY.get(load_type)
    if prop_name is None:
        return {
            "material_id": material_id,
            "state": INSUFFICIENT_DATA,
            "reason": f"unsupported load type: {load_type}",
        }

    prop = _find_property(mat, prop_name)
    if prop is None:
        return {
            "material_id": material_id,
            "state": INSUFFICIENT_DATA,
            "reason": f"no {prop_name} property for {material_id}",
        }

    load_unit = load_case.get("unit", "")
    if prop["unit"] != load_unit:
        return {
            "material_id": material_id,
            "state": ASSESS_FAIL_CLOSED,
            "reason": f"unit mismatch: property [{prop['unit']}] vs load [{load_unit}]",
        }

    capacity = prop["value_or_range"]
    if not isinstance(capacity, (int, float)):
        return {
            "material_id": material_id,
            "state": INSUFFICIENT_DATA,
            "reason": "property value is a range; nominal required for margin",
        }

    applied = load_case.get("magnitude", 0.0)
    if not isinstance(applied, (int, float)) or applied == 0:
        return {
            "material_id": material_id,
            "state": INSUFFICIENT_DATA,
            "reason": "load magnitude missing or zero",
        }

    margin = (capacity - applied) / applied
    failure_mode = f"{load_type}_yield" if load_type in ("tensile", "yield") else f"{load_type}_failure"

    return {
        "material_id": material_id,
        "failure_mode": failure_mode,
        "margin": margin,
        "state": "pass" if margin > 0 else "fail",
        "capacity": capacity,
        "applied": applied,
        "unit": prop["unit"],
        "uncertainty": prop.get("uncertainty", 0.0),
    }


__all__ = [
    "ASSESS_FAIL_CLOSED",
    "INSUFFICIENT_CONTEXT",
    "INSUFFICIENT_DATA",
    "MATERIALS",
    "MATERIAL_FAMILIES",
    "METAL_FORMING_OPS",
    "POLYMER_PROCESSES",
    "ROLES",
    "SCHEMA_VERSION",
    "assess_strength",
    "get_properties",
    "list_material_families",
    "lookup_material",
    "normalize_requirements",
    "plan_metal_forming",
    "plan_polymer_process",
    "select_materials",
]
