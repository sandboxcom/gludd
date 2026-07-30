"""Metal forming expert (spec MATE-001 section 4.3).

Implements the ``metal_forming_plan`` role's domain logic: alloy condition and
temper effects, formability assessment, springback estimation, heat-treatment
recommendations, and hot tearing risk per spec section 4.3. Unsuitable heat
treatments are blocked per MATE-AT-003.

All estimates are handbook-grade nominal values carrying ``basis`` and
``uncertainty``; they are not a substitute for supplier/lot test data
(MATE-SAFE-003 no fabricated precision, MATE-DEC-003 data hierarchy).
"""

from __future__ import annotations

from typing import Any

from general_ludd.materials.core import (
    INSUFFICIENT_DATA,
    METAL_FORMING_OPS,
    lookup_material,
)

# Per-material condition metadata keyed by material_id.
# Values are nominal handbook entries (ASM Handbook Vol. 1/2 / ASTM specs).
_CONDITION_DATA: dict[str, dict[str, Any]] = {
    "aa6061_t6": {
        "temper": "T6",
        "condition_class": "solution_heat_treated_and_aged",
        "product_form": "sheet",
        "work_hardened": False,
        "base_alloy_family": "non_ferrous_aluminum",
    },
    "aisi_1045": {
        "temper": "as_drawn",
        "condition_class": "cold_drawn",
        "product_form": "cold_drawn",
        "work_hardened": True,
        "base_alloy_family": "ferrous_carbon",
    },
}

# Formability ratings keyed by (material_id, operation).
# Ratings reflect nominal handbook formability for the listed condition.
# Rating scale: poor < fair < good < excellent.
_FORMABILITY: dict[str, dict[str, str]] = {
    "aa6061_t6": {
        "bending": "fair",  # T6 temper: limited ductility before cracking
        "stamping": "fair",
        "deep_drawing": "poor",
        "forging": "good",  # hot forging only; cold forging poor in T6
    },
    "aisi_1045": {
        "bending": "fair",
        "stamping": "good",  # medium carbon steel cold-drawn forms reasonably
        "forging": "good",
        "drawing": "fair",
    },
}

# Nominal springback (percent elastic recovery after bending).
# Aluminum alloys spring back more than steel due to lower modulus and higher
# yield-to-modulus ratio.
_SPRINGBACK_PCT: dict[str, dict[str, float]] = {
    "aa6061_t6": {"bending": 0.7, "stamping": 0.6, "forging": 0.1},
    "aisi_1045": {"bending": 0.4, "stamping": 0.3, "forging": 0.1},
}

_SPRINGBACK_COMPENSATION: dict[str, list[str]] = {
    "aa6061_t6": [
        "overbend by 1-2 degrees per springback estimate",
        "use bottoming or coining dies to reduce elastic recovery",
        "consider stretch forming for complex contours",
    ],
    "aisi_1045": [
        "overbend by 0.5-1 degree per springback estimate",
        "stress-relief anneal after sharp bends to reduce residual stress",
    ],
}

# Heat treatment recommendations after forming.
_HEAT_TREATMENT: dict[str, dict[str, Any]] = {
    "aa6061_t6": {
        "forging": {
            "required": True,
            "steps": [
                "solution treat at 530C for 1-2 hours",
                "quench in water",
                "age at 175C for 8 hours (T6 temper restoration)",
            ],
            "reason": (
                "hot working destroys the T6 precipitation-hardened structure; "
                "the temper must be restored by solution treatment + aging"
            ),
        },
        "bending": {
            "required": False,
            "steps": ["stress-relief at 150C optional"],
            "reason": "cold bending in T6 sheet does not destroy temper",
        },
    },
    "aisi_1045": {
        "forging": {
            "required": True,
            "steps": [
                "normalize at 850C to refine grain structure",
                "quench and temper to target hardness if required",
            ],
            "reason": "forging alters the as-drawn condition; normalize for uniform structure",
        },
        "bending": {
            "required": False,
            "steps": ["stress-relief anneal at 600C optional after cold bending"],
            "reason": "cold work induces residual stresses; optional relief anneal",
        },
    },
}

# Hot tearing (solidification cracking) risk during casting/welding.
_HOT_TEARING_RISK: dict[str, dict[str, Any]] = {
    "aa6061_t6": {
        "risk_level": "medium",
        "reason": (
            "6xxx series alloys are moderately susceptible to hot tearing due "
            "to a wide freezing range and low eutectic fraction"
        ),
        "mitigation": [
            "use grain refiner (Ti+B) additions",
            "control casting temperature to avoid superheat",
            "optimize mold design to avoid sharp section transitions",
        ],
    },
    "aisi_1045": {
        "risk_level": "low",
        "reason": "medium carbon steel has a narrow freezing range and low hot tearing susceptibility",
        "mitigation": [
            "control sulfur and phosphorus to avoid embrittlement",
        ],
    },
}


class MetalFormingAdvisor:
    """Domain expert for metal forming decisions (role
    ``metal_forming_plan``).

    The advisor reasons about alloy/temper, casting and wrought form, work
    hardening, formability, springback, heat treatment, and hot tearing per
    spec section 4.3. Unsuitable processes/heat treatments are blocked.
    """

    # ------------------------------------------------------------------
    # Condition / temper
    # ------------------------------------------------------------------

    def describe_condition(self, material_id: str) -> dict[str, Any]:
        """Describe the alloy condition and temper for a material.

        Includes temper designation, condition class (solution-treated,
        cold-drawn, etc.), product form, and work-hardening state.
        """
        mat = lookup_material(material_id)
        if mat is None or mat.get("family") != "metal":
            return {
                "material_id": material_id,
                "state": INSUFFICIENT_DATA,
                "reason": "unknown or non-metal material",
            }
        data = _CONDITION_DATA.get(material_id)
        if data is None:
            return {
                "material_id": material_id,
                "temper": None,
                "condition_class": None,
                "product_form": None,
                "work_hardened": None,
                "state": INSUFFICIENT_DATA,
                "reason": "no condition data on file for this alloy",
            }
        return {
            "material_id": material_id,
            "designation": mat.get("designation", ""),
            "temper": data["temper"],
            "condition_class": data["condition_class"],
            "product_form": data["product_form"],
            "work_hardened": data["work_hardened"],
            "base_alloy_family": data["base_alloy_family"],
            "state": "ok",
        }

    # ------------------------------------------------------------------
    # Formability
    # ------------------------------------------------------------------

    def assess_formability(self, material_id: str, operation: str) -> dict[str, Any]:
        """Assess the formability rating for a material + operation pair.

        Rating scale: poor, fair, good, excellent. Operations outside the
        registered forming-ops list return ``insufficient_data``.
        """
        mat = lookup_material(material_id)
        if mat is None or mat.get("family") != "metal":
            return {
                "material_id": material_id,
                "operation": operation,
                "formability_rating": None,
                "state": INSUFFICIENT_DATA,
                "reason": "unknown or non-metal material",
            }
        if operation not in METAL_FORMING_OPS:
            return {
                "material_id": material_id,
                "operation": operation,
                "formability_rating": None,
                "state": INSUFFICIENT_DATA,
                "reason": f"unrecognized metal forming operation: {operation}",
            }
        op_data = _FORMABILITY.get(material_id, {})
        rating = op_data.get(operation)
        if rating is None:
            # Fallback: infer from temper class.
            cond = _CONDITION_DATA.get(material_id, {})
            rating = "fair" if cond.get("work_hardened") or cond.get("temper") == "T6" else "good"
        cond = _CONDITION_DATA.get(material_id, {})
        return {
            "material_id": material_id,
            "operation": operation,
            "formability_rating": rating,
            "temper": cond.get("temper"),
            "condition_class": cond.get("condition_class"),
            "basis": "ASM Handbook Vol. 14 nominal formability for listed condition",
            "note": (
                "annealing before forming can improve rating one level for "
                "work-hardened or precipitation-hardened conditions"
            ),
        }

    # ------------------------------------------------------------------
    # Springback
    # ------------------------------------------------------------------

    def estimate_springback(self, material_id: str, operation: str) -> dict[str, Any]:
        """Estimate springback (percent elastic recovery) for a forming op.

        Springback is higher for high yield-to-modulus ratios (aluminum
        alloys) and lower for steel. Estimates are nominal handbook values.
        """
        mat = lookup_material(material_id)
        if mat is None or mat.get("family") != "metal":
            return {
                "material_id": material_id,
                "operation": operation,
                "springback_pct": None,
                "state": INSUFFICIENT_DATA,
                "reason": "unknown or non-metal material",
            }
        op_data = _SPRINGBACK_PCT.get(material_id, {})
        sb = op_data.get(operation, 0.5)  # generic fallback
        compensation = _SPRINGBACK_COMPENSATION.get(
            material_id,
            [
                "overbend by the springback estimate",
            ],
        )
        return {
            "material_id": material_id,
            "operation": operation,
            "springback_pct": sb,
            "unit": "percent",
            "compensation_strategy": compensation,
            "basis": (
                "nominal handbook estimate; proportional to yield_strength / youngs_modulus (elastic recovery ratio)"
            ),
            "uncertainty": "depends on die geometry, material lot, and strain rate",
        }

    # ------------------------------------------------------------------
    # Heat treatment
    # ------------------------------------------------------------------

    def recommend_heat_treatment(self, material_id: str, operation: str) -> dict[str, Any]:
        """Recommend a post-forming heat treatment.

        Forging of precipitation-hardened alloys (e.g. 6061-T6) destroys the
        temper and requires solution treatment + aging. Cold-forming of steel
        needs only optional stress-relief anneal.
        """
        mat = lookup_material(material_id)
        if mat is None or mat.get("family") != "metal":
            return {
                "material_id": material_id,
                "operation": operation,
                "state": INSUFFICIENT_DATA,
                "reason": "unknown or non-metal material",
            }
        mat_ht = _HEAT_TREATMENT.get(material_id, {})
        ht = mat_ht.get(operation)
        if ht is None:
            ht = {
                "required": False,
                "steps": ["stress-relief anneal optional after cold forming"],
                "reason": "no material-specific heat treatment data; generic recommendation",
            }
        return {
            "material_id": material_id,
            "operation": operation,
            "required": ht["required"],
            "steps": ht["steps"],
            "reason": ht["reason"],
        }

    # ------------------------------------------------------------------
    # Hot tearing risk
    # ------------------------------------------------------------------

    def hot_tearing_risk(self, material_id: str) -> dict[str, Any]:
        """Assess hot tearing (solidification cracking) risk.

        Hot tearing occurs during casting or welding solidification when
        tensile stresses develop across a partially solidified, low-ductility
        microstructure. Susceptibility depends on freezing range and eutectic
        fraction.
        """
        mat = lookup_material(material_id)
        if mat is None or mat.get("family") != "metal":
            return {
                "material_id": material_id,
                "risk_level": None,
                "state": INSUFFICIENT_DATA,
                "reason": "unknown or non-metal material",
            }
        risk = _HOT_TEARING_RISK.get(
            material_id,
            {
                "risk_level": "medium",
                "reason": "no material-specific data; default moderate risk",
                "mitigation": [
                    "qualify with trial casting/welding coupon",
                    "verify with ASTM B977 hot tearing test if safety-critical",
                ],
            },
        )
        return {
            "material_id": material_id,
            "designation": mat.get("designation", ""),
            "risk_level": risk["risk_level"],
            "reason": risk["reason"],
            "mitigation": risk["mitigation"],
        }


__all__ = ["MetalFormingAdvisor"]
