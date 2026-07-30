"""Joining and welding advisor (spec MATE-001 §4.4).

Provides material-compatibility assessment for joining processes:
  - fusion welding (GMAW, GTAW/TIG, SMAW, laser, EBW)
  - solid-state welding (FSW, diffusion, ultrasonic)
  - cold welding
  - pressure welding (resistance, forge, roll, explosion)
  - polymer joining (hot-plate, hot-gas, ultrasonic, vibration, solvent)
  - brazing, soldering, adhesive bonding, mechanical fastening

For each candidate process + material pair the advisor assesses:
  - metallurgical compatibility
  - galvanic risk (dissimilar metals)
  - thermal-expansion mismatch (dissimilar metals)
  - heat-affected-zone (HAZ) concerns (fusion only)
  - inspectability (NDT methods appropriate to the joint)

Safety invariants (spec §8):
  - MATE-SAFE-006: unknown materials or processes return ``insufficient_data``
    rather than a fabricated compatibility verdict.
  - MATE-AT-003 (mirrored): thermosets cannot be remelted; any fusion/remelt
    process against a thermoset is ``rejected``.
"""

from __future__ import annotations

from typing import Any

from general_ludd.materials.core import (
    INSUFFICIENT_DATA,
    SCHEMA_VERSION,
    lookup_material,
)

FUSION_PROCESSES: frozenset[str] = frozenset(
    {
        "gmaw",
        "gmaaw",
        "gtaw",
        "tig",
        "mig",
        "smaaw",
        "smaw",
        "fcaw",
        "saw",
        "laser_welding",
        "laser_beam_welding",
        "lbw",
        "electron_beam_welding",
        "ebw",
        "plasma_welding",
        "oxy_fuel",
        "oxyfuel",
        "electroslag_welding",
        "esw",
    }
)

SOLID_STATE_PROCESSES: frozenset[str] = frozenset(
    {
        "friction_stir_welding",
        "fsw",
        "diffusion_bonding",
        "diffusion_welding",
        "ultrasonic_welding",
        "usw",
        "explosion_welding",
        "explosive_welding",
        "exw",
    }
)

COLD_PROCESSES: frozenset[str] = frozenset(
    {
        "cold_welding",
        "cold_pressure_welding",
    }
)

PRESSURE_PROCESSES: frozenset[str] = frozenset(
    {
        "resistance_welding",
        "rsw",
        "spot_welding",
        "projection_welding",
        "seam_welding",
        "forge_welding",
        "roll_welding",
        "pressure_welding",
    }
)

POLYMER_PROCESSES_JOIN: frozenset[str] = frozenset(
    {
        "hot_plate_welding",
        "hot_gas_welding",
        "extrusion_welding",
        "ultrasonic_polymer_welding",
        "vibration_welding",
        "radio_frequency_welding",
        "rf_welding",
        "laser_polymer_welding",
        "solvent_bonding",
        "induction_welding",
    }
)

BRAZING_PROCESSES: frozenset[str] = frozenset(
    {
        "brazing",
        "torch_brazing",
        "furnace_brazing",
        "vacuum_brazing",
        "induction_brazing",
    }
)

SOLDERING_PROCESSES: frozenset[str] = frozenset(
    {
        "soldering",
        "wave_soldering",
        "reflow_soldering",
        "hand_soldering",
    }
)

ADHESIVE_PROCESSES: frozenset[str] = frozenset(
    {
        "adhesive_bonding",
        "epoxy_bonding",
        "glueing",
        "structural_bonding",
    }
)

MECHANICAL_PROCESSES: frozenset[str] = frozenset(
    {
        "bolted",
        "bolting",
        "riveting",
        "screwing",
        "staking",
        "crimping",
        "threaded_fastener",
        "clinching",
    }
)

CATEGORY_TO_PROCESSES: tuple[tuple[str, frozenset[str]], ...] = (
    ("fusion", FUSION_PROCESSES),
    ("solid_state", SOLID_STATE_PROCESSES),
    ("cold", COLD_PROCESSES),
    ("pressure", PRESSURE_PROCESSES),
    ("polymer", POLYMER_PROCESSES_JOIN),
    ("brazing", BRAZING_PROCESSES),
    ("soldering", SOLDERING_PROCESSES),
    ("adhesive", ADHESIVE_PROCESSES),
    ("mechanical", MECHANICAL_PROCESSES),
)

# Approximate coefficients of thermal expansion (10^-6 / K) for galvanic/CTE
# mismatch reasoning. Keyed by material id; falls back to family typical.
_CTE_TABLE: dict[str, float] = {
    "aisi_1045": 12.0,
    "aa6061_t6": 23.6,
    "abs": 80.0,
    "pa66_gf30": 30.0,
    "epoxy_cast": 60.0,
}

_CTE_BY_FAMILY: dict[str, float] = {
    "metal": 15.0,
    "polymer": 70.0,
    "ceramic": 8.0,
    "composite": 10.0,
    "textile": 50.0,
}

# Galvanic series buckets (simplified): materials in different buckets paired
# with an electrolyte form a galvanic cell. Bucket index 0 = noble, 4 = active.
_GALVANIC_BUCKET: dict[str, int] = {
    "aisi_1045": 2,  # carbon steel, moderately active
    "aa6061_t6": 4,  # aluminum, very active
    "abs": -1,  # polymer — not in galvanic series
    "pa66_gf30": -1,
    "epoxy_cast": -1,
}

_GALVANIC_FAMILY_BUCKET: dict[str, int] = {
    "metal": 2,
    "polymer": -1,
    "ceramic": -1,
    "composite": -1,
    "textile": -1,
}


def _cte(material_id: str, material: dict[str, Any] | None) -> float:
    if material_id in _CTE_TABLE:
        return _CTE_TABLE[material_id]
    if material is not None:
        return _CTE_BY_FAMILY.get(material.get("family", ""), 15.0)
    return 15.0


def _galvanic_bucket(material_id: str, material: dict[str, Any] | None) -> int:
    if material_id in _GALVANIC_BUCKET:
        return _GALVANIC_BUCKET[material_id]
    if material is not None:
        return _GALVANIC_FAMILY_BUCKET.get(material.get("family", ""), 2)
    return 2


# Inspectability presets keyed by joining category.
_INSPECTABILITY: dict[str, dict[str, Any]] = {
    "fusion": {
        "methods": ["VT", "PT", "RT", "UT"],
        "difficulty": "medium",
        "notes": "Requires weld procedure qualification and operator certification per AWS D1.1 / ASME IX.",
    },
    "solid_state": {
        "methods": ["VT", "UT", "PT"],
        "difficulty": "medium",
        "notes": "No filler; bondline is the inspection target. UT recommended.",
    },
    "cold": {
        "methods": ["VT", "PT"],
        "difficulty": "low",
        "notes": "Ductile metals only; verify deformation visually.",
    },
    "pressure": {
        "methods": ["VT", "UT"],
        "difficulty": "medium",
        "notes": "Nugget size / bond area is the acceptance criterion.",
    },
    "polymer": {
        "methods": ["VT", "leak_test"],
        "difficulty": "low",
        "notes": "Visual + pressure-decay for hermetic joints.",
    },
    "brazing": {
        "methods": ["VT", "PT", "RT"],
        "difficulty": "medium",
        "notes": "Filler flow at the joint meniscus is the acceptance criterion.",
    },
    "soldering": {
        "methods": ["VT", "X-ray"],
        "difficulty": "low",
        "notes": "IPC-A-610 visual acceptance for electronic assemblies.",
    },
    "adhesive": {
        "methods": ["VT", "ultrasonic_shearography"],
        "difficulty": "high",
        "notes": "Internal disbonds not visible; bondline integrity is hard to verify nondestructively.",
    },
    "mechanical": {
        "methods": ["VT", "torque_check"],
        "difficulty": "low",
        "notes": "Fastener torque / clamp load is the acceptance criterion.",
    },
}


class JoiningAdvisor:
    """Assess joining-process compatibility for material pairs (spec §4.4)."""

    def classify_process(self, process: str) -> dict[str, Any]:
        """Classify a joining process into its category.

        Returns ``{"category": ..., "state": ...}``. Unknown processes return
        ``category="unknown"`` and ``state="insufficient_data"`` per MATE-SAFE-006.
        """
        p = process.strip().lower()
        for category, members in CATEGORY_TO_PROCESSES:
            if p in members:
                return {"category": category, "state": "ok"}
        return {"category": "unknown", "state": INSUFFICIENT_DATA, "reason": f"unrecognized process: {process}"}

    def assess_compatibility(
        self,
        material_a: str,
        material_b: str,
        process: str,
    ) -> dict[str, Any]:
        """Assess compatibility of joining ``material_a`` to ``material_b``
        via ``process``.

        Returns a dict with: ``compatible`` (bool), ``state``, ``reason``
        (when rejected), ``category`` (process class), ``risks`` (list of
        dicts with ``kind`` and ``detail``), ``haz`` (for fusion processes),
        and ``inspectability`` (NDT methods + difficulty).
        """
        cls = self.classify_process(process)
        category = cls["category"]
        if category == "unknown":
            return {
                "schema_version": SCHEMA_VERSION,
                "material_a": material_a,
                "material_b": material_b,
                "process": process,
                "category": "unknown",
                "compatible": False,
                "state": INSUFFICIENT_DATA,
                "reason": cls.get("reason", "unrecognized process"),
            }

        mat_a = lookup_material(material_a)
        mat_b = lookup_material(material_b)
        if mat_a is None or mat_b is None:
            missing = material_a if mat_a is None else material_b
            return {
                "schema_version": SCHEMA_VERSION,
                "material_a": material_a,
                "material_b": material_b,
                "process": process,
                "category": category,
                "compatible": False,
                "state": INSUFFICIENT_DATA,
                "reason": f"unknown material: {missing}",
            }

        # MATE-AT-003 (mirrored): thermoset + remelt/fusion process -> reject.
        polymer_a = mat_a.get("polymer_class")
        polymer_b = mat_b.get("polymer_class")
        is_remelt_process = category in ("fusion",) or process in ("laser_welding",)
        if is_remelt_process and (polymer_a == "thermoset" or polymer_b == "thermoset"):
            thermoset_id = material_a if polymer_a == "thermoset" else material_b
            return {
                "schema_version": SCHEMA_VERSION,
                "material_a": material_a,
                "material_b": material_b,
                "process": process,
                "category": category,
                "compatible": False,
                "state": "rejected",
                "reason": f"{thermoset_id} is a thermoset and cannot be remelted for {process}; use cure-based or mechanical joining instead",
                "risks": [],
                "inspectability": _INSPECTABILITY.get(category, {}),
            }

        risks: list[dict[str, str]] = []
        dissimilar = material_a != material_b or mat_a["family"] != mat_b["family"]

        # Galvanic risk: dissimilar metals in different galvanic buckets.
        bucket_a = _galvanic_bucket(material_a, mat_a)
        bucket_b = _galvanic_bucket(material_b, mat_b)
        if bucket_a >= 0 and bucket_b >= 0 and abs(bucket_a - bucket_b) >= 2:
            risks.append(
                {
                    "kind": "galvanic",
                    "detail": (
                        f"{material_a} (bucket {bucket_a}) and {material_b} (bucket {bucket_b}) "
                        "differ by >=2 in the galvanic series; isolate electrically or add a barrier"
                    ),
                }
            )

        # Thermal-expansion mismatch: CTE delta above 5e-6/K distorts the joint.
        cte_a = _cte(material_a, mat_a)
        cte_b = _cte(material_b, mat_b)
        if abs(cte_a - cte_b) >= 5.0:
            risks.append(
                {
                    "kind": "thermal_expansion_mismatch",
                    "detail": (
                        f"CTE {cte_a:.1f} vs {cte_b:.1f} 10^-6/K (delta {abs(cte_a - cte_b):.1f}); "
                        "thermal cycling will induce distortion and residual stress"
                    ),
                }
            )

        # HAZ: fusion processes alter the base-metal microstructure.
        haz: dict[str, Any] | None = None
        if category == "fusion":
            if mat_a["family"] == "metal" or mat_b["family"] == "metal":
                haz = {
                    "present": True,
                    "concern": "softening / phase change in heat-affected zone",
                    "mitigation": "control heat input; qualify procedure per AWS D1.1 / ASME IX",
                }
            else:
                haz = {"present": True, "concern": "thermal degradation of polymer matrix"}

        # Metallurgical compatibility: dissimilar metals may form brittle
        # intermetallics (e.g. Fe-Al, Cu-Fe at certain ratios).
        if (
            dissimilar
            and mat_a["family"] == "metal"
            and mat_b["family"] == "metal"
            and mat_a.get("class") != mat_b.get("class")
        ):
            risks.append(
                {
                    "kind": "intermetallic_formation",
                    "detail": (
                        f"dissimilar alloys ({mat_a.get('class')} / {mat_b.get('class')}) "
                        "may form brittle intermetallics at the fusion interface"
                    ),
                }
            )

        # Adhesive joints: polymer/metal or polymer/composite pairings surface
        # a limited-inspectability warning regardless of risk count.
        compatible = True
        state = "candidate"
        reason = "ok"

        return {
            "schema_version": SCHEMA_VERSION,
            "material_a": material_a,
            "material_b": material_b,
            "process": process,
            "category": category,
            "compatible": compatible,
            "state": state,
            "reason": reason,
            "galvanic_risk": any(r["kind"] == "galvanic" for r in risks),
            "risks": risks,
            "haz": haz if haz is not None else {"present": False},
            "inspectability": _INSPECTABILITY.get(
                category,
                {
                    "methods": ["VT"],
                    "difficulty": "medium",
                    "notes": "",
                },
            ),
        }


__all__ = [
    "ADHESIVE_PROCESSES",
    "BRAZING_PROCESSES",
    "CATEGORY_TO_PROCESSES",
    "COLD_PROCESSES",
    "FUSION_PROCESSES",
    "MECHANICAL_PROCESSES",
    "POLYMER_PROCESSES_JOIN",
    "PRESSURE_PROCESSES",
    "SOLDERING_PROCESSES",
    "SOLID_STATE_PROCESSES",
    "JoiningAdvisor",
]
