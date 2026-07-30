"""Textile / flexible materials expert (spec MATE-001 section 4.6).

Implements the ``textile_plan`` role's domain logic: fiber and yarn
properties, weave/knit/braid architecture classification, seam efficiency
estimation, drape assessment, and directional strength ratios per spec
section 4.6.

All estimates are handbook-grade nominal values carrying ``basis`` and
``uncertainty``; they are not a substitute for supplier/lot test data
(MATE-SAFE-003 no fabricated precision, MATE-DEC-003 data hierarchy).
"""

from __future__ import annotations

from typing import Any

# Recognized textile fiber families (spec section 4.6).
TEXTILE_FIBERS: tuple[str, ...] = (
    "cotton",
    "polyester",
    "nylon",
    "aramid",
    "glass",
    "carbon",
    "wool",
    "silk",
)

# Nominal fiber properties (tenacity cN/tex, density g/cm^3, moisture regain %).
# Source basis: Meredith & Hearle, "The Physical Properties of Textile Fibers",
# 3rd ed.; wide variation by grade and finish.
_FIBER_PROPERTIES: dict[str, dict[str, Any]] = {
    "cotton": {"tenacity_cntex": 35.0, "density_g_cm3": 1.52, "moisture_regain_pct": 8.5},
    "polyester": {"tenacity_cntex": 55.0, "density_g_cm3": 1.38, "moisture_regain_pct": 0.4},
    "nylon": {"tenacity_cntex": 60.0, "density_g_cm3": 1.14, "moisture_regain_pct": 4.5},
    "aramid": {"tenacity_cntex": 200.0, "density_g_cm3": 1.44, "moisture_regain_pct": 4.5},
    "glass": {"tenacity_cntex": 85.0, "density_g_cm3": 2.54, "moisture_regain_pct": 0.0},
    "carbon": {"tenacity_cntex": 180.0, "density_g_cm3": 1.78, "moisture_regain_pct": 0.0},
    "wool": {"tenacity_cntex": 12.0, "density_g_cm3": 1.30, "moisture_regain_pct": 16.0},
    "silk": {"tenacity_cntex": 40.0, "density_g_cm3": 1.34, "moisture_regain_pct": 11.0},
}

# Recognized weave architectures (spec section 4.6).
WEAVE_TYPES: tuple[str, ...] = ("plain", "twill", "satin")

# Directional strength ratio (warp / weft) for balanced vs unbalanced weaves.
# Plain weave is the most balanced; satin skews strength toward the warp due
# to longer floats. Source basis: "Woven Fabric Engineering" - Seyam.
_WEAVE_STRENGTH_RATIO: dict[str, float] = {
    "plain": 1.0,
    "twill": 1.1,
    "satin": 1.25,
}

# Relative drape (1.0 = excellent drape, 0.0 = stiff). Plain weave is stiff
# due to many interlacings; satin drapes well due to long floats.
_WEAVE_DRAPE_SCORE: dict[str, float] = {
    "plain": 0.4,
    "twill": 0.7,
    "satin": 0.9,
}

# Recognized knit families.
KNIT_TYPES: tuple[str, ...] = ("weft_knit", "warp_knit")

# Knit elongation / recovery (percent stretch at break, recovery fraction).
_KNIT_PROPERTIES: dict[str, dict[str, Any]] = {
    "weft_knit": {"elongation_pct": 100.0, "recovery_pct": 90.0, "run_resistant": True},
    "warp_knit": {"elongation_pct": 50.0, "recovery_pct": 95.0, "run_resistant": False},
}

# Seam efficiency (ratio of seam strength to fabric strength) by seam type.
# Source basis: " Sewn Joining of Textile Materials" - M. Carvalho.
_SEAM_EFFICIENCY: dict[str, float] = {
    "plain": 0.70,  # plain seam
    "felled": 0.85,  # felled seam (double-fold, higher strength)
    "overlock": 0.60,  # overlock (serger) seam
    "lap": 0.80,  # lap felled seam
}

# Crimp ratio (fraction of yarn length consumed by waviness in a woven fabric).
# Higher crimp = lower effective modulus along that direction.
_CRIMP_PCT: dict[str, float] = {
    "plain": 10.0,
    "twill": 5.0,
    "satin": 3.0,
}


class TextileAdvisor:
    """Domain expert for textile and flexible-material planning (role
    ``textile_plan``).

    Reasons about fiber, yarn, weave, knit, braid, seam, and drape per spec
    section 4.6. Estimates directional strength ratios, seam efficiency,
    and drape; flags insufficient data rather than fabricating precision
    (MATE-SAFE-003).
    """

    def fiber_properties(self, fiber: str) -> dict[str, Any]:
        """Return nominal tenacity, density, and moisture regain for a fiber."""
        f = fiber.lower()
        if f not in _FIBER_PROPERTIES:
            return {
                "fiber": fiber,
                "state": "insufficient_data",
                "reason": f"unknown fiber: {fiber}",
            }
        props = _FIBER_PROPERTIES[f]
        return {
            "fiber": f,
            "tenacity_cntex": props["tenacity_cntex"],
            "density_g_cm3": props["density_g_cm3"],
            "moisture_regain_pct": props["moisture_regain_pct"],
            "basis": "Meredith & Hearle nominal; verify per grade and finish",
        }

    def yarn_linear_density(self, fiber: str, yarn_count_tex: float) -> dict[str, Any]:
        """Estimate yarn breaking load from fiber tenacity and yarn count.

        Breaking load (N) = tenacity (cN/tex) * yarn_count (tex) / 100.
        Real yarn efficiency is 60-90% of theoretical due to twist and
        friction losses; reported here at nominal 75%.
        """
        fp = self.fiber_properties(fiber)
        if fp.get("state") == "insufficient_data":
            return fp
        tenacity = fp["tenacity_cntex"]
        theoretical_n = tenacity * yarn_count_tex / 100.0
        efficiency = 0.75
        return {
            "fiber": fiber,
            "yarn_count_tex": yarn_count_tex,
            "theoretical_breaking_load_N": theoretical_n,
            "estimated_breaking_load_N": theoretical_n * efficiency,
            "yarn_efficiency": efficiency,
            "basis": "theoretical * 0.75 twist/friction efficiency (handbook)",
        }

    def weave_properties(self, weave: str) -> dict[str, Any]:
        """Return directional strength ratio, drape score, and crimp for a weave."""
        w = weave.lower()
        if w not in _WEAVE_STRENGTH_RATIO:
            return {
                "weave": weave,
                "state": "insufficient_data",
                "reason": f"unknown weave: {weave}; recognized: {WEAVE_TYPES}",
            }
        return {
            "weave": w,
            "directional_strength_ratio_warp_to_weft": _WEAVE_STRENGTH_RATIO[w],
            "drape_score": _WEAVE_DRAPE_SCORE[w],
            "crimp_pct": _CRIMP_PCT[w],
            "basis": "Seyam, Woven Fabric Engineering; nominal handbook values",
        }

    def classify_knit(self, knit: str) -> dict[str, Any]:
        """Classify knit architecture and return elongation/recovery."""
        k = knit.lower()
        if k not in _KNIT_PROPERTIES:
            return {
                "knit": knit,
                "state": "insufficient_data",
                "reason": f"unknown knit: {knit}; recognized: {KNIT_TYPES}",
            }
        props = _KNIT_PROPERTIES[k]
        return {
            "knit": k,
            "elongation_pct": props["elongation_pct"],
            "recovery_pct": props["recovery_pct"],
            "run_resistant": props["run_resistant"],
            "basis": "Spencer, Knitting Technology nominal handbook values",
        }

    def seam_efficiency(self, seam_type: str) -> dict[str, Any]:
        """Estimate seam efficiency (seam strength / fabric strength).

        Lower efficiency means the seam fails before the surrounding fabric.
        """
        s = seam_type.lower()
        if s not in _SEAM_EFFICIENCY:
            return {
                "seam_type": seam_type,
                "state": "insufficient_data",
                "reason": f"unknown seam type: {seam_type}",
            }
        efficiency = _SEAM_EFFICIENCY[s]
        return {
            "seam_type": s,
            "seam_efficiency": efficiency,
            "interpretation": ("seam fails before fabric" if efficiency < 0.75 else "seam near fabric strength"),
            "basis": "Carvalho nominal handbook values",
        }

    def assess_drape(self, weave: str) -> dict[str, Any]:
        """Assess drape (conformability to curved surfaces) from weave type."""
        wp = self.weave_properties(weave)
        if wp.get("state") == "insufficient_data":
            return wp
        score = wp["drape_score"]
        if score >= 0.8:
            rating = "excellent"
        elif score >= 0.6:
            rating = "good"
        elif score >= 0.4:
            rating = "moderate"
        else:
            rating = "poor"
        return {
            "weave": weave,
            "drape_score": score,
            "drape_rating": rating,
            "reason": (
                f"{weave} weave has {rating} drape due to "
                f"{'long floats' if 'satin' in weave else 'interlacing frequency'}"
            ),
        }

    def directional_strength_ratio(self, weave: str) -> dict[str, Any]:
        """Compute warp/weft strength ratio for a woven architecture.

        Unbalanced weaves (satin) concentrate strength in the warp direction;
        balanced weaves (plain) are nearly isotropic in-plane.
        """
        wp = self.weave_properties(weave)
        if wp.get("state") == "insufficient_data":
            return wp
        ratio = wp["directional_strength_ratio_warp_to_weft"]
        balanced = 0.95 <= ratio <= 1.05
        return {
            "weave": weave,
            "warp_to_weft_ratio": ratio,
            "balanced": balanced,
            "reason": (f"{weave}: {'balanced in-plane' if balanced else 'strength concentrated in warp'}"),
            "basis": "Seyam, Woven Fabric Engineering nominal",
        }


__all__ = [
    "KNIT_TYPES",
    "TEXTILE_FIBERS",
    "TextileAdvisor",
    "WEAVE_TYPES",
]
