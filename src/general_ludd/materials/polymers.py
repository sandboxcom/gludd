"""Polymer process expert (spec MATE-001 section 4.2).

Implements the ``polymer_process_plan`` role's domain logic: distinguishing
thermoplastic vs thermoset behavior, estimating shrinkage/warpage, accounting
for fiber-orientation anisotropy, computing drying requirements, and producing
thermoset cure schedules. Incompatible process/material pairings are rejected
per MATE-AT-003 (e.g. a thermoset cannot be remelted for injection molding).

All estimates are handbook-grade nominal values carrying ``basis`` and
``uncertainty``; they are not a substitute for supplier/lot test data
(MATE-SAFE-003 no fabricated precision, MATE-DEC-003 data hierarchy).
"""

from __future__ import annotations

from typing import Any

from general_ludd.materials.core import (
    INSUFFICIENT_DATA,
    POLYMER_PROCESSES,
    THERMOPLASTIC_PROCESSES,
    lookup_material,
)

# Nominal mold shrinkage ranges (percent) for unfilled commodity polymers.
# Source basis: CAMPUS / ISO 294-4 typical ranges; wide uncertainty by grade.
_UNFILLED_SHRINKAGE_PCT: dict[str, tuple[float, float]] = {
    "abs": (0.004, 0.007),
    "pa66_gf30": (0.002, 0.005),  # GF-reinforced grades shrink less
    "epoxy_cast": (0.001, 0.004),
}

_DEFAULT_SHRINKAGE_PCT: tuple[float, float] = (0.003, 0.008)

# Hygroscopic polymers that require drying before processing.
_HYGROSCOPIC_POLYMERS: frozenset[str] = frozenset({"abs", "pa66_gf30", "pa66", "pet", "pc"})

# Drying parameters (temperature C, duration hours).
_DRYING_PARAMS: dict[str, tuple[float, float]] = {
    "abs": (80.0, 3.0),
    "pa66_gf30": (85.0, 4.0),
    "pa66": (85.0, 4.0),
    "pc": (120.0, 4.0),
    "pet": (120.0, 4.0),
}

# Cure schedules for thermosets (gel time min, cure temp C, post-cure?).
_CURE_SCHEDULES: dict[str, dict[str, Any]] = {
    "epoxy_cast": {
        "gel_time_min": 90.0,
        "cure_temperature_C": 80.0,
        "post_cure_recommended": True,
        "post_cure_temperature_C": 120.0,
        "post_cure_duration_hours": 2.0,
    },
}

# Reinforcement indicators found in material ids / classes.
_REINFORCED_TOKENS: frozenset[str] = frozenset({"gf", "glass", "carbon", "cf", "mineral"})


def _is_reinforced(material: dict[str, Any]) -> bool:
    """True if the material is fiber/mineral reinforced."""
    klass = (material.get("class") or "").lower()
    mid = (material.get("material_id") or "").lower()
    return any(tok in klass or tok in mid for tok in _REINFORCED_TOKENS)


class PolymerProcessAdvisor:
    """Domain expert for polymer forming decisions (role
    ``polymer_process_plan``).

    The advisor reasons about the thermoplastic vs thermoset distinction,
    shrinkage/warpage, fiber orientation, drying, and cure kinetics per spec
    section 4.2. It SHALL NOT recommend an incompatible forming or rework
    process (MATE-AT-003).
    """

    # ------------------------------------------------------------------
    # Thermoplastic vs thermoset distinction
    # ------------------------------------------------------------------

    def classify(self, material_id: str) -> dict[str, Any]:
        """Return the polymer class (thermoplastic/thermoset) and remelt flag.

        Thermoplastics soften and remelt on heating; thermosets cross-link
        during cure and cannot be remelted. Unknown materials return
        ``insufficient_data``.
        """
        mat = lookup_material(material_id)
        if mat is None or mat.get("family") != "polymer":
            return {
                "material_id": material_id,
                "polymer_class": None,
                "remeltable": None,
                "state": INSUFFICIENT_DATA,
                "reason": "unknown or non-polymer material",
            }
        klass = mat.get("polymer_class", "")
        remeltable = klass == "thermoplastic"
        return {
            "material_id": material_id,
            "designation": mat.get("designation", ""),
            "polymer_class": klass,
            "remeltable": remeltable,
            "state": "ok",
        }

    def check_regrind(self, material_id: str) -> dict[str, Any]:
        """Whether regrind (recycling of runners/sprues/defects) is allowed.

        Thermosets are cross-linked and cannot be reground + remelted; regrind
        would only yield filler-grade powder, not a usable resin. Thermoplastics
        can be reground subject to property degradation limits (typically
        <=15-20% regrind ratio).
        """
        info = self.classify(material_id)
        if info.get("state") == INSUFFICIENT_DATA:
            return {
                "material_id": material_id,
                "allowed": None,
                "state": INSUFFICIENT_DATA,
                "reason": info.get("reason", "unknown material"),
            }
        if info["polymer_class"] == "thermoset":
            return {
                "material_id": material_id,
                "allowed": False,
                "state": "rejected",
                "reason": (
                    "thermoset is cross-linked and cannot be remelted; regrind "
                    "yields only filler-grade powder, not usable resin"
                ),
            }
        return {
            "material_id": material_id,
            "allowed": True,
            "state": "ok",
            "reason": "thermoplastic can be regrond; limit to 15-20% regrind ratio",
            "max_regrind_ratio_pct": 20.0,
        }

    # ------------------------------------------------------------------
    # Shrinkage / warpage
    # ------------------------------------------------------------------

    def estimate_shrinkage(self, material_id: str) -> dict[str, Any]:
        """Estimate mold shrinkage (percent) from material data.

        Reinforced grades shrink less than unfilled grades because the fiber
        reinforcement constrains volumetric contraction. Values are nominal
        handbook ranges with wide grade-to-grade variation.
        """
        mat = lookup_material(material_id)
        if mat is None or mat.get("family") != "polymer":
            return {
                "material_id": material_id,
                "mold_shrinkage_pct": None,
                "unit": "percent",
                "state": INSUFFICIENT_DATA,
                "reason": "unknown or non-polymer material",
            }
        lo, hi = _UNFILLED_SHRINKAGE_PCT.get(material_id, _DEFAULT_SHRINKAGE_PCT)
        # Values stored as decimal-fraction of percent (0.004 = 0.4%).
        # Report nominal midpoint as the point estimate.
        nominal = (lo + hi) / 2.0
        return {
            "material_id": material_id,
            "mold_shrinkage_pct": nominal,
            "unit": "percent",
            "range_pct": [lo, hi],
            "reinforced": _is_reinforced(mat),
            "basis": "CAMPUS / ISO 294-4 nominal handbook range",
            "uncertainty": "grade-dependent; verify with supplier datasheet",
        }

    def estimate_warpage(self, material_id: str) -> dict[str, Any]:
        """Assess warpage risk.

        Warpage arises from differential shrinkage — in reinforced grades,
        fiber orientation induces anisotropic shrinkage (parallel vs
        perpendicular to flow), which is a primary driver of warpage.
        """
        mat = lookup_material(material_id)
        if mat is None or mat.get("family") != "polymer":
            return {
                "material_id": material_id,
                "warpage_risk": None,
                "state": INSUFFICIENT_DATA,
                "reason": "unknown or non-polymer material",
            }
        reinforced = _is_reinforced(mat)
        if reinforced:
            risk = "high"
            reason = (
                "fiber reinforcement causes anisotropic shrinkage (parallel vs "
                "perpendicular to flow) - primary warpage driver; requires "
                "flow analysis and gate placement optimization"
            )
        else:
            # Amorphous polymers warp less than semi-crystalline.
            risk = "medium"
            reason = "isotropic shrinkage; moderate warpage risk"
        return {
            "material_id": material_id,
            "warpage_risk": risk,
            "reinforced": reinforced,
            "reason": reason,
            "mitigation": [
                "uniform wall thickness",
                "balanced gate placement",
                "mold temperature control",
                "pack/hold pressure optimization",
            ]
            if reinforced
            else ["uniform wall thickness", "pack/hold pressure optimization"],
        }

    # ------------------------------------------------------------------
    # Fiber orientation
    # ------------------------------------------------------------------

    def fiber_orientation_effect(self, material_id: str) -> dict[str, Any]:
        """Report fiber orientation anisotropy for reinforced grades.

        For a 30% glass-fiber reinforced PA66, strength parallel to flow is
        typically ~1.6x the strength perpendicular to flow. Here we report the
        normalized strength ratio (perp/parallel) — less than 1.0 indicates
        anisotropy that must be accounted for in structural analysis.
        """
        mat = lookup_material(material_id)
        if mat is None or mat.get("family") != "polymer":
            return {
                "material_id": material_id,
                "anisotropic": None,
                "state": INSUFFICIENT_DATA,
                "reason": "unknown or non-polymer material",
            }
        reinforced = _is_reinforced(mat)
        if not reinforced:
            return {
                "material_id": material_id,
                "anisotropic": False,
                "strength_ratio_parallel_to_flow": 1.0,
                "reason": "unfilled polymer; isotropic assumption reasonable",
            }
        # PA66-GF30 typical: perp/parallel strength ratio ~0.6.
        ratio = 0.6
        return {
            "material_id": material_id,
            "anisotropic": True,
            "strength_ratio_parallel_to_flow": ratio,
            "reason": (
                "glass fiber aligns with flow direction during injection; "
                "strength perpendicular to flow drops to ~60% of parallel"
            ),
            "design_note": (
                "structural analysis must consider worst-case orientation; do not use single isotropic property value"
            ),
        }

    # ------------------------------------------------------------------
    # Drying
    # ------------------------------------------------------------------

    def drying_requirement(self, material_id: str) -> dict[str, Any]:
        """Drying requirement (temperature, duration) for hygroscopic polymers.

        Polyamides, ABS, PC, and PET absorb atmospheric moisture; processing
        wet resin causes hydrolysis, splay marks, and loss of mechanical
        properties.
        """
        mat = lookup_material(material_id)
        if mat is None or mat.get("family") != "polymer":
            return {
                "material_id": material_id,
                "drying_required": None,
                "state": INSUFFICIENT_DATA,
                "reason": "unknown or non-polymer material",
            }
        if material_id not in _HYGROSCOPIC_POLYMERS:
            return {
                "material_id": material_id,
                "drying_required": False,
                "reason": "non-hygroscopic polymer; drying not mandatory",
            }
        temp, dur = _DRYING_PARAMS[material_id]
        return {
            "material_id": material_id,
            "drying_required": True,
            "temperature_C": temp,
            "duration_hours": dur,
            "target_moisture_pct": 0.2 if "pa" in material_id else 0.1,
            "reason": (
                "hygroscopic polymer; surface moisture causes hydrolysis, "
                "splay, and molecular weight degradation during processing"
            ),
        }

    # ------------------------------------------------------------------
    # Cure kinetics (thermoset only)
    # ------------------------------------------------------------------

    def cure_schedule(self, material_id: str) -> dict[str, Any]:
        """Return the cure schedule for a thermoset.

        Thermoplastics do not cure (they remelt); requesting a cure schedule
        for a thermoplastic returns ``insufficient_data``.
        """
        info = self.classify(material_id)
        if info.get("state") == INSUFFICIENT_DATA:
            return {
                "material_id": material_id,
                "state": INSUFFICIENT_DATA,
                "reason": info.get("reason", "unknown material"),
            }
        if info["polymer_class"] != "thermoset":
            return {
                "material_id": material_id,
                "state": INSUFFICIENT_DATA,
                "reason": "thermoplastic does not cure; it remelts on heating",
            }
        sched = _CURE_SCHEDULES.get(material_id)
        if sched is None:
            return {
                "material_id": material_id,
                "state": INSUFFICIENT_DATA,
                "reason": "no cure schedule on file for this thermoset grade",
            }
        return {
            "material_id": material_id,
            "gel_time_min": sched["gel_time_min"],
            "cure_temperature_C": sched["cure_temperature_C"],
            "post_cure_recommended": sched["post_cure_recommended"],
            "post_cure_temperature_C": sched.get("post_cure_temperature_C"),
            "post_cure_duration_hours": sched.get("post_cure_duration_hours"),
            "basis": "supplier technical bulletin; verify for specific grade",
        }

    # ------------------------------------------------------------------
    # Process compatibility (MATE-AT-003 negative fixtures)
    # ------------------------------------------------------------------

    def check_process_compatibility(self, material_id: str, process_family: str) -> dict[str, Any]:
        """Check whether a process family is compatible with the material.

        Thermosets cannot be used in remelt-based processes (injection molding,
        extrusion, blow molding, thermoforming). They require cure-based
        processes (compression molding, transfer molding, casting).
        """
        if process_family not in POLYMER_PROCESSES:
            return {
                "material_id": material_id,
                "process_family": process_family,
                "compatible": None,
                "state": INSUFFICIENT_DATA,
                "reason": f"unrecognized polymer process: {process_family}",
            }
        info = self.classify(material_id)
        if info.get("state") == INSUFFICIENT_DATA:
            return {
                "material_id": material_id,
                "process_family": process_family,
                "compatible": None,
                "state": INSUFFICIENT_DATA,
                "reason": info.get("reason", "unknown material"),
            }
        if info["polymer_class"] == "thermoset" and process_family in THERMOPLASTIC_PROCESSES:
            return {
                "material_id": material_id,
                "process_family": process_family,
                "compatible": False,
                "state": "rejected",
                "reason": (
                    f"thermoset is cross-linked and cannot be remelted for "
                    f"{process_family}; use a cure-based process "
                    f"(compression_molding, transfer_molding, casting)"
                ),
            }
        return {
            "material_id": material_id,
            "process_family": process_family,
            "compatible": True,
            "state": "candidate",
            "reason": "compatible",
        }


__all__ = ["PolymerProcessAdvisor"]
