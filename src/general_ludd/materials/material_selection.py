"""Material selection: screening and ranking (spec MATE-001 section 7).

Implements the MATE-DEC-002 decision pipeline:

1. ``screen_candidates`` — reject candidates violating a hard constraint
   (insufficient capacity, missing property, insufficient_context data).
2. ``rank_candidates`` — normalize compatible units, compute traceable
   performance indices and margins under nominal / conservative / sensitivity
   cases, and expose trade-offs as a structured dict rather than a collapsed
   single score.

Data hierarchy (MATE-DEC-003): lot > supplier > handbook > estimated.
Lower-tier data is labeled via ``data_tier`` on each margin; it is never
silently substituted for higher-tier evidence. The ``overrides`` parameter
lets callers inject lot/supplier data that supersedes the handbook registry.
"""

from __future__ import annotations

from typing import Any

from general_ludd.materials.core import (
    INSUFFICIENT_CONTEXT,
    MATERIALS,
    SCHEMA_VERSION,
    lookup_material,
)

# Highest-precedence first (MATE-DEC-003).
DATA_TIERS: tuple[str, ...] = ("lot", "supplier", "handbook", "estimated")

_LOAD_TYPE_TO_PROPERTY: dict[str, str] = {
    "tensile": "yield_strength",
    "yield": "yield_strength",
    "ultimate": "ultimate_strength",
    "compression": "compressive_strength",
    "shear": "shear_strength",
}

_STRESS_UNIT_FACTOR_TO_MPA: dict[str, float] = {
    "MPa": 1.0,
    "GPa": 1000.0,
    "Pa": 1e-6,
    "ksi": 6.89476,
    "psi": 0.00689476,
}


def _find_property(material: dict[str, Any], name: str) -> dict[str, Any] | None:
    properties: list[dict[str, Any]] = material["properties"]
    for prop in properties:
        if prop["name"] == name:
            return prop
    return None


def _to_mpa(value: float, unit: str) -> tuple[float, str]:
    """Normalize a stress/modulus value to MPa.

    Returns the value unchanged if the unit is not a known stress unit
    (non-destructive).
    """
    factor = _STRESS_UNIT_FACTOR_TO_MPA.get(unit)
    if factor is not None:
        return value * factor, "MPa"
    return value, unit


# ---------------------------------------------------------------------------
# Data hierarchy resolution (MATE-DEC-003)
# ---------------------------------------------------------------------------


def resolve_property(
    material_id: str,
    prop_name: str,
    overrides: dict[str, dict[str, dict[str, Any]]] | None = None,
) -> tuple[dict[str, Any] | None, str | None]:
    """Resolve the effective property record using the lot/supplier/handbook hierarchy.

    Returns ``(record, tier)`` where *record* is normalized to always carry a
    ``value`` key (converting from the registry's ``value_or_range`` if
    needed) and *tier* is the data-source tier string.
    """
    mat_overrides = (overrides or {}).get(material_id, {})
    if prop_name in mat_overrides:
        rec = dict(mat_overrides[prop_name])
        rec.setdefault("tier", "supplier")
        return rec, rec["tier"]

    mat = lookup_material(material_id)
    if mat is None:
        return None, None
    prop = _find_property(mat, prop_name)
    if prop is None:
        return None, None
    rec = dict(prop)
    if "value_or_range" in rec and "value" not in rec:
        rec["value"] = rec["value_or_range"]
    rec["tier"] = "handbook"
    return rec, "handbook"


# ---------------------------------------------------------------------------
# Screening (MATE-DEC-002 step 1)
# ---------------------------------------------------------------------------


def screen_candidates(
    reqs: dict[str, Any],
    candidates: list[str] | None = None,
) -> dict[str, Any]:
    """Reject candidates that violate any hard constraint.

    A candidate is rejected when:
    - the material id is unknown to the registry;
    - a required property (mapped from the load-case type) is absent;
    - a property carries ``insufficient_context`` state (no condition metadata);
    - the nominal capacity is below the applied load magnitude.

    Survivors carry their computed requirement margins.
    """
    candidate_ids = candidates if candidates is not None else list(MATERIALS.keys())
    load_cases = reqs.get("load_cases")
    if not isinstance(load_cases, list):
        load_cases = []

    results: list[dict[str, Any]] = []
    for cid in candidate_ids:
        mat = lookup_material(cid)
        if mat is None:
            results.append(
                {
                    "material_id": cid,
                    "state": "rejected",
                    "reason": "unknown_material",
                    "violations": ["unknown_material"],
                    "requirement_margins": [],
                    "source": {},
                    "unknowns": [],
                }
            )
            continue

        violations: list[str] = []
        margins: list[dict[str, Any]] = []
        for lc in load_cases:
            lc_type = lc.get("type", "")
            prop_name = _LOAD_TYPE_TO_PROPERTY.get(lc_type)
            if prop_name is None:
                continue
            prop = _find_property(mat, prop_name)
            req_id = lc.get("id", lc_type)

            if prop is None:
                violations.append(f"hard_constraint: no {prop_name} property")
                margins.append(
                    {
                        "requirement_id": req_id,
                        "margin": None,
                        "state": "insufficient_data",
                        "reason": f"no {prop_name} property",
                    }
                )
                continue

            if prop.get("state") == INSUFFICIENT_CONTEXT:
                violations.append(f"hard_constraint: {prop_name} insufficient_context")
                margins.append(
                    {
                        "requirement_id": req_id,
                        "margin": None,
                        "state": "insufficient_data",
                        "reason": "insufficient_context",
                    }
                )
                continue

            capacity = prop.get("value_or_range")
            applied = lc.get("magnitude", 0.0)
            if not isinstance(capacity, (int, float)) or capacity <= 0:
                violations.append(f"hard_constraint: {prop_name} value not usable")
                continue
            if not isinstance(applied, (int, float)) or applied == 0:
                margins.append(
                    {
                        "requirement_id": req_id,
                        "margin": None,
                        "state": "insufficient_data",
                        "reason": "applied load missing or zero",
                    }
                )
                continue

            margin = (capacity - applied) / applied
            margins.append(
                {
                    "requirement_id": req_id,
                    "margin": margin,
                    "state": "pass" if margin > 0 else "fail",
                    "capacity": capacity,
                    "applied": applied,
                    "unit": prop.get("unit", ""),
                }
            )
            if margin <= 0:
                violations.append(
                    f"hard_constraint: {prop_name} {capacity} "
                    f"{prop.get('unit', '')} < required {applied} "
                    f"{lc.get('unit', '')}"
                )

        state = "rejected" if violations else "survived"
        reason = "; ".join(violations) if violations else "ok"
        results.append(
            {
                "material_id": cid,
                "designation": mat.get("designation", ""),
                "family": mat.get("family", ""),
                "state": state,
                "reason": reason,
                "violations": violations,
                "requirement_margins": margins,
                "source": mat.get("source", {}),
                "unknowns": mat.get("unknowns", []),
            }
        )

    verdict = "candidate" if any(r["state"] == "survived" for r in results) else "infeasible"
    return {
        "schema_version": SCHEMA_VERSION,
        "candidates": results,
        "verdict": verdict,
    }


# ---------------------------------------------------------------------------
# Ranking (MATE-DEC-002 steps 2-5)
# ---------------------------------------------------------------------------


def _build_tradeoffs(
    material_id: str,
    overrides: dict[str, dict[str, dict[str, Any]]] | None,
) -> dict[str, dict[str, Any]]:
    """Collect all known properties for a candidate into a tradeoff profile.

    Stress/modulus values are normalized to MPa so candidates are directly
    comparable. Each entry records its data tier so the user can see which
    values come from lot data vs handbook estimates.
    """
    mat = lookup_material(material_id)
    prop_names: set[str] = set()
    if mat:
        for p in mat["properties"]:
            prop_names.add(p["name"])
    prop_names.update((overrides or {}).get(material_id, {}).keys())

    tradeoffs: dict[str, dict[str, Any]] = {}
    for name in sorted(prop_names):
        prop, tier = resolve_property(material_id, name, overrides)
        if prop is None:
            continue
        value = prop.get("value", prop.get("value_or_range"))
        unit = prop.get("unit", "")
        if isinstance(value, (int, float)):
            nv, nu = _to_mpa(value, unit)
            tradeoffs[name] = {"value": nv, "unit": nu, "tier": tier}
        else:
            tradeoffs[name] = {"value": value, "unit": unit, "tier": tier}
    return tradeoffs


def _compute_indices(
    tradeoffs: dict[str, dict[str, Any]],
) -> dict[str, float]:
    """Derive standard performance indices (specific strength/stiffness) when density is available."""
    indices: dict[str, float] = {}
    ys = tradeoffs.get("yield_strength", {})
    mod = tradeoffs.get("youngs_modulus", {})
    dens = tradeoffs.get("density", {})
    dens_val = dens.get("value") if isinstance(dens, dict) else None
    ys_val = ys.get("value") if isinstance(ys, dict) else None
    mod_val = mod.get("value") if isinstance(mod, dict) else None

    if isinstance(dens_val, (int, float)) and dens_val > 0 and isinstance(ys_val, (int, float)):
        indices["specific_strength"] = ys_val / dens_val
    if isinstance(dens_val, (int, float)) and dens_val > 0 and isinstance(mod_val, (int, float)):
        indices["specific_stiffness"] = mod_val / dens_val
    return indices


def _compute_margins(
    reqs: dict[str, Any],
    material_id: str,
    overrides: dict[str, dict[str, dict[str, Any]]] | None,
    case: str,
) -> list[dict[str, Any]]:
    """Compute requirement margins for one candidate under a given case.

    Cases:
    - ``nominal``: capacity = property value as-is, applied = load magnitude.
    - ``conservative``: capacity = value - uncertainty (lower bound).
    - ``sensitivity``: applied = magnitude * 1.1 (10% overload scenario).
    """
    load_cases = reqs.get("load_cases")
    if not isinstance(load_cases, list):
        return []

    margins: list[dict[str, Any]] = []
    for lc in load_cases:
        lc_type = lc.get("type", "")
        prop_name = _LOAD_TYPE_TO_PROPERTY.get(lc_type)
        if prop_name is None:
            continue

        prop, tier = resolve_property(material_id, prop_name, overrides)
        req_id = lc.get("id", lc_type)

        if prop is None:
            margins.append(
                {
                    "requirement_id": req_id,
                    "margin": None,
                    "state": "insufficient_data",
                    "reason": f"no {prop_name} property",
                    "data_tier": None,
                }
            )
            continue

        if prop.get("state") == INSUFFICIENT_CONTEXT:
            margins.append(
                {
                    "requirement_id": req_id,
                    "margin": None,
                    "state": "insufficient_data",
                    "reason": "insufficient_context: condition metadata missing",
                    "data_tier": tier,
                }
            )
            continue

        capacity = prop.get("value", prop.get("value_or_range"))
        if not isinstance(capacity, (int, float)):
            margins.append(
                {
                    "requirement_id": req_id,
                    "margin": None,
                    "state": "insufficient_data",
                    "reason": "capacity not numeric",
                    "data_tier": tier,
                }
            )
            continue

        applied = lc.get("magnitude", 0.0)
        if not isinstance(applied, (int, float)) or applied == 0:
            margins.append(
                {
                    "requirement_id": req_id,
                    "margin": None,
                    "state": "insufficient_data",
                    "reason": "applied load missing or zero",
                    "data_tier": tier,
                }
            )
            continue

        uncertainty = prop.get("uncertainty", 0.0)
        if not isinstance(uncertainty, (int, float)):
            uncertainty = 0.0

        eff_capacity = capacity - uncertainty if case == "conservative" else capacity
        eff_applied = applied * 1.1 if case == "sensitivity" else applied

        margin = (eff_capacity - eff_applied) / eff_applied
        margins.append(
            {
                "requirement_id": req_id,
                "margin": margin,
                "state": "pass" if margin > 0 else "fail",
                "capacity": eff_capacity,
                "applied": eff_applied,
                "unit": prop.get("unit", ""),
                "uncertainty": uncertainty,
                "data_tier": tier,
            }
        )

    return margins


def rank_candidates(
    reqs: dict[str, Any],
    candidates: list[str] | None = None,
    overrides: dict[str, dict[str, dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    """Rank material candidates under nominal, conservative, and sensitivity cases.

    Returns a structured result with three parallel lists (``nominal``,
    ``conservative``, ``sensitivity``). Each candidate entry carries:
    - ``margins``: traceable per-requirement margins with data tier and units;
    - ``performance_indices``: specific strength/stiffness when density is known;
    - ``tradeoffs``: full property profile (stress units normalized to MPa),
      exposed as a dict rather than collapsed into a single score.

    Per MATE-DEC-001, returns verdict ``insufficient_data`` when no load cases
    are defined (nothing to rank against).
    """
    # MATE-DEC-001: mandatory load constraints must be present.
    load_cases = reqs.get("load_cases")
    if load_cases == "unknown" or not isinstance(load_cases, list) or len(load_cases) == 0:
        return {
            "schema_version": SCHEMA_VERSION,
            "verdict": "insufficient_data",
            "reason": "mandatory constraint unknown: load_cases not defined",
            "nominal": [],
            "conservative": [],
            "sensitivity": [],
        }

    req_candidates = reqs.get("candidates")
    if candidates is not None:
        candidate_ids = candidates
    elif isinstance(req_candidates, list) and req_candidates:
        candidate_ids = req_candidates
    else:
        candidate_ids = list(MATERIALS.keys())

    def build_case(case: str) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        for cid in candidate_ids:
            mat = lookup_material(cid)
            tradeoffs = _build_tradeoffs(cid, overrides)
            indices = _compute_indices(tradeoffs)
            margins = _compute_margins(reqs, cid, overrides, case)
            entries.append(
                {
                    "material_id": cid,
                    "designation": mat["designation"] if mat else "",
                    "family": mat["family"] if mat else "",
                    "margins": margins,
                    "performance_indices": indices,
                    "tradeoffs": tradeoffs,
                    "unknowns": mat.get("unknowns", []) if mat else [],
                    "source": mat.get("source", {}) if mat else {},
                }
            )
        return entries

    return {
        "schema_version": SCHEMA_VERSION,
        "verdict": "candidate",
        "nominal": build_case("nominal"),
        "conservative": build_case("conservative"),
        "sensitivity": build_case("sensitivity"),
    }


__all__ = [
    "DATA_TIERS",
    "rank_candidates",
    "resolve_property",
    "screen_candidates",
]
