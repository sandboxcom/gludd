"""Machining advisor (spec MATE-001 §4.5).

Produces a preliminary machining plan for a material + process pair covering:
  - datum scheme (primary / secondary / tertiary)
  - accessibility (status + notes)
  - fixturing requirements (clamp type / method)
  - cutting-tool material + coating class
  - chatter risk (low / medium / high)
  - tolerance capability (IT grade or band)
  - surface integrity (notes on residual stress, burns, work hardening)

Safety invariants (spec §8):
  - MATE-SAFE-006: unknown material or process returns ``insufficient_data``.
"""

from __future__ import annotations

from typing import Any

from general_ludd.materials.core import (
    INSUFFICIENT_DATA,
    SCHEMA_VERSION,
    lookup_material,
)

MACHINING_PROCESSES: frozenset[str] = frozenset(
    {
        "milling",
        "turning",
        "drilling",
        "boring",
        "reaming",
        "broaching",
        "grinding",
        "honing",
        "lapping",
        "edm",
        "wire_edm",
        "laser_machining",
        "plasma_machining",
        "waterjet",
        "chemical_machining",
    }
)

_DATUM_SCHEME_DEFAULT: dict[str, Any] = {
    "primary": "locating_face",
    "secondary": "bolt_pattern_axis",
    "tertiary": "anti_rotation_pin",
    "notes": "ISO 5459; pick largest stable faces first",
}

_DATUM_SCHEME_BY_PROCESS: dict[str, dict[str, Any]] = {
    "turning": {
        "primary": "spindle_axis_face",
        "secondary": "chuck_jaw_datum",
        "tertiary": "tailstock_center",
        "notes": "Turning: face + OD + center hole (DIN 332)",
    },
    "milling": _DATUM_SCHEME_DEFAULT,
    "drilling": {
        "primary": "perpendicular_face",
        "secondary": "drill_axis",
        "tertiary": "drill_pattern_origin",
        "notes": "Drilling: face normal to hole + 2 locating edges",
    },
    "grinding": {
        "primary": "ground_finish_face",
        "secondary": "wheel_axis",
        "tertiary": "fixture_dowel",
        "notes": "Grinding: heat-treated datum to avoid post-heat-treatment shift",
    },
}

_FIXTURING: dict[str, dict[str, Any]] = {
    "milling": {"clamp_type": "tombstone_or_vise", "method": "hydraulic_vise", "rigidity": "high"},
    "turning": {"clamp_type": "chuck", "method": "3_jaw_chuck", "rigidity": "high"},
    "drilling": {"clamp_type": "drill_jig", "method": "jig_bushing", "rigidity": "medium"},
    "boring": {"clamp_type": "boring_fixture", "method": "steady_rest", "rigidity": "high"},
    "grinding": {"clamp_type": "magnetic_chuck", "method": "electromagnetic", "rigidity": "high"},
}

# Tool class defaults keyed by material family.
_TOOL_BY_FAMILY: dict[str, str] = {
    "metal": "coated_carbide",
    "polymer": "high_speed_steel",
    "ceramic": "diamond",
    "composite": "polycrystalline_diamond",
    "textile": "high_speed_steel",
}

# Material-specific tool class overrides (by material id).
_TOOL_BY_MATERIAL: dict[str, str] = {
    "aisi_1045": "coated_carbide",
    "aa6061_t6": "polished_uncoated_carbide",
    "pa66_gf30": "polycrystalline_diamond",
    "abs": "high_speed_steel",
    "epoxy_cast": "tungsten_carbide",
}

# Chatter risk by material family (rough proxy for modulus + damping).
_CHATTER_BY_FAMILY: dict[str, str] = {
    "metal": "medium",
    "polymer": "low",
    "ceramic": "low",
    "composite": "medium",
    "textile": "low",
}

_CHATTER_BY_MATERIAL: dict[str, str] = {
    "aa6061_t6": "high",  # low modulus, low damping
    "aisi_1045": "low",
    "abs": "low",
    "pa66_gf30": "medium",  # abrasive glass fibers
    "epoxy_cast": "low",
}

_TOLERANCE_BY_PROCESS: dict[str, dict[str, Any]] = {
    "milling": {"it_grade": "IT8", "band_mm": 0.027},
    "turning": {"it_grade": "IT7", "band_mm": 0.016},
    "drilling": {"it_grade": "IT11", "band_mm": 0.13},
    "boring": {"it_grade": "IT7", "band_mm": 0.016},
    "reaming": {"it_grade": "IT7", "band_mm": 0.013},
    "broaching": {"it_grade": "IT8", "band_mm": 0.022},
    "grinding": {"it_grade": "IT5", "band_mm": 0.006},
    "honing": {"it_grade": "IT4", "band_mm": 0.003},
    "lapping": {"it_grade": "IT3", "band_mm": 0.001},
    "edm": {"it_grade": "IT6", "band_mm": 0.010},
    "wire_edm": {"it_grade": "IT6", "band_mm": 0.008},
    "laser_machining": {"it_grade": "IT9", "band_mm": 0.052},
    "waterjet": {"it_grade": "IT12", "band_mm": 0.21},
}

_SURFACE_INTEGRITY: dict[str, dict[str, Any]] = {
    "milling": {
        "ra_um": 1.6,
        "concerns": ["work_hardening_layer", "residual_stress"],
        "notes": "Climb milling reduces work hardening; control depth of cut.",
    },
    "turning": {
        "ra_um": 1.6,
        "concerns": ["built_up_edge", "residual_stress"],
        "notes": "Use sharp insert geometry; flooded coolant for steels.",
    },
    "drilling": {
        "ra_um": 3.2,
        "concerns": ["burr_formation", "work_hardening_at_exit"],
        "notes": "Chamfer exit face or use peck-drilling cycle.",
    },
    "grinding": {
        "ra_um": 0.4,
        "concerns": ["grinding_burns", "tensile_residual_stress", "micro_cracking"],
        "notes": "Keep wheel sharp; use generous coolant flow to prevent burns.",
    },
    "edm": {
        "ra_um": 1.6,
        "concerns": ["recast_layer", "heat_affected_zone", "micro_cracking"],
        "notes": "Recast layer is brittle; remove via honing for fatigue-critical surfaces.",
    },
    "waterjet": {
        "ra_um": 3.2,
        "concerns": ["burr_at_exit", "taper"],
        "notes": "No HAZ; abrasive wear at edges may require secondary finishing.",
    },
    "laser_machining": {
        "ra_um": 3.2,
        "concerns": ["recast_layer", "heat_affected_zone", "striations"],
        "notes": "Pulse parameters control HAZ depth; review for fatigue-critical use.",
    },
}

_ACCESSIBILITY_DEFAULT: dict[str, Any] = {
    "status": "ok",
    "notes": "Open tool access on at least 3 faces; verify with CAM simulation.",
}

_ACCESSIBILITY_BY_PROCESS: dict[str, dict[str, Any]] = {
    "boring": {
        "status": "limited",
        "notes": "Internal bore access; boring bar overhang must be <4xD to avoid chatter.",
    },
    "drilling": {"status": "ok", "notes": "Verify perpendicular access; cross-holes need jigs."},
    "edm": {"status": "limited", "notes": "Electrode access + dielectric flushing required."},
    "wire_edm": {"status": "ok", "notes": "Through-cut only; start hole required for internal profiles."},
    "honing": {"status": "limited", "notes": "Requires through-bore access for honing stone."},
    "lapping": {"status": "ok", "notes": "Flat / cylindrical external access."},
}


class MachiningAdvisor:
    """Produce a preliminary machining plan for a material + process (spec §4.5)."""

    def plan(self, material_id: str, process: str) -> dict[str, Any]:
        """Return a machining plan dict for ``material_id`` and ``process``.

        Returns ``state="insufficient_data"`` for unknown materials or
        unrecognized processes per MATE-SAFE-006.
        """
        p = process.strip().lower()
        if p not in MACHINING_PROCESSES:
            return {
                "schema_version": SCHEMA_VERSION,
                "material_id": material_id,
                "process": process,
                "state": INSUFFICIENT_DATA,
                "reason": f"unrecognized machining process: {process}",
            }

        mat = lookup_material(material_id)
        if mat is None:
            return {
                "schema_version": SCHEMA_VERSION,
                "material_id": material_id,
                "process": p,
                "state": INSUFFICIENT_DATA,
                "reason": f"unknown material: {material_id}",
            }

        datum = dict(_DATUM_SCHEME_BY_PROCESS.get(p, _DATUM_SCHEME_DEFAULT))
        fixturing = dict(
            _FIXTURING.get(
                p,
                {
                    "clamp_type": "generic_vise",
                    "method": "manual",
                    "rigidity": "medium",
                },
            )
        )
        tool_class = _TOOL_BY_MATERIAL.get(
            material_id,
            _TOOL_BY_FAMILY.get(mat["family"], "coated_carbide"),
        )
        chatter = _CHATTER_BY_MATERIAL.get(
            material_id,
            _CHATTER_BY_FAMILY.get(mat["family"], "medium"),
        )
        tolerance = dict(_TOLERANCE_BY_PROCESS.get(p, {"it_grade": "IT9", "band_mm": 0.052}))
        surface_integrity = dict(
            _SURFACE_INTEGRITY.get(
                p,
                {
                    "ra_um": 3.2,
                    "concerns": [],
                    "notes": "",
                },
            )
        )
        accessibility = dict(_ACCESSIBILITY_BY_PROCESS.get(p, _ACCESSIBILITY_DEFAULT))

        # Aluminum on grinding carries burn risk; bumps chatter concerns.
        if material_id == "aa6061_t6" and p == "grinding":
            surface_integrity["concerns"] = [*surface_integrity.get("concerns", []), "loading_of_wheel"]
            surface_integrity["notes"] = (
                (surface_integrity.get("notes") or "") + " Aluminum loads the wheel; use silicon-carbide or CBN wheels."
            ).strip()

        return {
            "schema_version": SCHEMA_VERSION,
            "material_id": material_id,
            "process": p,
            "state": "candidate",
            "datum_scheme": datum,
            "accessibility": accessibility,
            "fixturing": fixturing,
            "tool_class": tool_class,
            "chatter_risk": chatter,
            "tolerance_capability": tolerance,
            "surface_integrity": surface_integrity,
        }


__all__ = [
    "MACHINING_PROCESSES",
    "MachiningAdvisor",
]
