"""Additive manufacturing expert (spec MATE-001 section 4.5).

Implements the ``additive_plan`` role's domain logic: process selection
(FDM/SLA/SLS/DED), feedstock mapping, orientation-driven anisotropy,
support strategy from overhang angle, porosity estimation, residual-stress
flagging, and minimum-feature-size checks.

All estimates are handbook-grade nominal values carrying ``basis`` and
``uncertainty``; they are not a substitute for supplier/lot test data
(MATE-SAFE-003 no fabricated precision, MATE-DEC-003 data hierarchy).
"""

from __future__ import annotations

from typing import Any

from general_ludd.materials.core import INSUFFICIENT_DATA, lookup_material

# Recognized additive process families (spec section 2.2).
ADDITIVE_PROCESSES: tuple[str, ...] = ("FDM", "SLA", "SLS", "DED")

# Material class -> preferred additive process family. The advisor selects
# from this table based on the material's ``family``/``class`` and the
# stated requirements (detail, strength, quantity).
_PROCESS_BY_MATERIAL_CLASS: dict[str, str] = {
    "thermoplastic": "FDM",
    "thermoplastic_reinforced": "SLS",
    "thermoset": "SLA",
    "ferrous_carbon": "DED",
    "non_ferrous_aluminum": "DED",
}

# Nominal feedstock per process family.
_FEEDSTOCK: dict[str, str] = {
    "FDM": "filament (1.75 mm)",
    "SLA": "photopolymer resin",
    "SLS": "polymer powder (PA12 grade)",
    "DED": "metal wire or powder",
}

# Nominal porosity ranges (percent) per process family.
# Source basis: Gibson/Rosen/Stucker, Additive Manufacturing Technologies,
# 2nd ed.; wide variation by parameter set, material, and machine.
_POROSITY_PCT: dict[str, tuple[float, float]] = {
    "SLA": (0.1, 1.0),  # nearly fully dense photopolymer
    "FDM": (1.0, 5.0),  # air gaps between roads
    "SLS": (2.0, 8.0),  # unfused pockets between sintered grains
    "DED": (0.5, 4.0),  # remelted tracks; porosity varies with energy density
}

# Residual stress risk band per process family (MATE-001 section 4.5).
_RESIDUAL_STRESS_RISK: dict[str, str] = {
    "SLA": "low",  # low thermal gradients during cure
    "FDM": "medium",  # differential cooling between layers
    "SLS": "high",  # steep thermal gradients in powder bed
    "DED": "high",  # large thermal mass, repeated thermal cycles
}

# Minimum printable feature size (mm) per process family.
# Source basis: manufacturer spec sheets (typical nozzle/laser/voxel limits).
_MIN_FEATURE_MM: dict[str, float] = {
    "FDM": 1.0,
    "SLA": 0.5,
    "SLS": 0.7,
    "DED": 2.0,
}

# Z-axis (perpendicular-to-layer) strength as a fraction of in-plane
# strength. AM parts are weaker across the layer interface than within
# the plane (interlayer bonding < bulk strength). Source basis: ISO/ASTM
# 52900 series literature surveys.
_Z_STRENGTH_RATIO: dict[str, float] = {
    "FDM": 0.55,  # road-to-road bond is the weakest link
    "SLA": 0.85,  # nearly isotropic; thin interlayer cure gradient
    "SLS": 0.75,  # sinter bonds between layers slightly weaker
    "DED": 0.70,  # remelted track interfaces; process-dependent
}

# Overhang angle (degrees from horizontal) below which supports are required.
# 45 degrees is the conventional rule of thumb for FDM.
_SUPPORT_OVERHANG_THRESHOLD_DEG: float = 45.0


def _material_class(material_id: str) -> tuple[str | None, dict[str, Any] | None]:
    """Resolve the material class token from a material id.

    Returns ``(class_token, material_dict)`` where ``class_token`` is one of
    ``_PROCESS_BY_MATERIAL_CLASS``'s keys or None for unknown materials.
    """
    mat = lookup_material(material_id)
    if mat is None:
        return None, None
    # Prefer the explicit ``class`` field (covers "thermoplastic_reinforced"
    # and "ferrous_carbon"); fall back to the family.
    klass = mat.get("class") or ""
    if klass in _PROCESS_BY_MATERIAL_CLASS:
        return klass, mat
    # polymers without a reinforced/thermoset subclass -> generic thermoplastic
    if mat.get("family") == "polymer":
        pc = mat.get("polymer_class") or ""
        if pc == "thermoset":
            return "thermoset", mat
        return "thermoplastic", mat
    if mat.get("family") == "metal":
        return "ferrous_carbon", mat
    return None, mat


class AdditiveManufacturingAdvisor:
    """Domain expert for additive manufacturing planning (role
    ``additive_plan``).

    Selects process/orientation/supports and flags porosity, residual
    stress, and minimum-feature limits per spec section 4.5. Incompatible
    pairings (e.g. SLA on a metal) are rejected (MATE-AT-003).
    """

    def select_process(
        self,
        material_id: str,
        requirements: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Select an AM process from material + requirements.

        ``requirements`` may carry ``detail`` ("low"/"medium"/"high"),
        ``strength`` ("low"/"medium"/"high"), and ``quantity`` tokens.
        Returns the selected process, feedstock, compatibility verdict,
        and a reason code.
        """
        reqs = requirements or {}
        klass, mat = _material_class(material_id)
        if mat is None or klass is None:
            return {
                "material_id": material_id,
                "process": None,
                "compatible": False,
                "state": INSUFFICIENT_DATA,
                "reason": "unknown material or no AM-compatible class on file",
            }

        family = mat.get("family", "")
        default_proc = _PROCESS_BY_MATERIAL_CLASS[klass]
        detail = (reqs.get("detail") or "").lower()
        strength = (reqs.get("strength") or "").lower()
        process = default_proc

        # Override: high detail on a thermoplastic/thermoset routes to SLA
        # (finer feature resolution than FDM).
        if detail == "high" and family == "polymer" and klass != "thermoplastic_reinforced":
            process = "SLA"
        # High strength on an unreinforced thermoplastic routes to SLS
        # (better mechanical properties than FDM).
        elif strength == "high" and klass == "thermoplastic":
            process = "SLS"

        # SLA cannot build metal parts; guard against the override above.
        if family == "metal" and process == "SLA":
            process = "DED"

        return {
            "material_id": material_id,
            "process": process,
            "material_class": family,
            "feedstock": _FEEDSTOCK[process],
            "compatible": True,
            "state": "candidate",
            "reason": f"{process} is the default process for {klass}",
            "basis": "handbook-grade selection rule; verify with machine/material datasheet",
        }

    def orientation_recommendation(
        self,
        material_id: str,
        load_direction: str = "in_plane",
    ) -> dict[str, Any]:
        """Recommend a build orientation accounting for strength anisotropy.

        AM parts exhibit lower strength perpendicular to the build direction
        (across layer interfaces). For a z-axis-dominated load case, the
        advisor flags the anisotropy and recommends rotating the part so the
        primary load axis lies in the build plane.
        """
        klass, mat = _material_class(material_id)
        if mat is None or klass is None:
            return {
                "material_id": material_id,
                "state": INSUFFICIENT_DATA,
                "reason": "unknown material",
            }
        proc = _PROCESS_BY_MATERIAL_CLASS.get(klass, "FDM")
        z_ratio = _Z_STRENGTH_RATIO.get(proc, 0.6)
        # The recommended orientation minimizes the interlayer-bond load.
        if load_direction.lower() in ("z_axis", "z-axis", "build_direction"):
            recommended = "rotate_part_so_load_lies_in_plane"
            anisotropy = "high"
        else:
            recommended = "load_in_plane_default"
            anisotropy = "low"
        return {
            "material_id": material_id,
            "recommended_orientation": recommended,
            "anisotropy_ratio": anisotropy,
            "z_strength_ratio": z_ratio,
            "process": proc,
            "reason": (
                f"z-axis strength is {z_ratio:.0%} of in-plane for {proc}; "
                f"orient so primary load avoids the interlayer bond"
            ),
            "basis": "ISO/ASTM 52900 anisotropy survey; verify per build",
        }

    def support_strategy(self, process: str, overhang_deg: float) -> dict[str, Any]:
        """Determine support requirements from overhang angle.

        Overhangs shallower than the threshold (45 deg from horizontal for
        FDM/SLA) cannot self-support and require generated supports. SLS and
        DED are powder/wire processes where the bed/track provides support,
        so the threshold is relaxed.
        """
        if process not in ADDITIVE_PROCESSES:
            return {
                "process": process,
                "supports_required": None,
                "state": INSUFFICIENT_DATA,
                "reason": f"unrecognized process: {process}",
            }
        threshold = _SUPPORT_OVERHANG_THRESHOLD_DEG
        # SLS / DED: the surrounding powder bed or substrate supports the
        # overhang, so supports are generally not required.
        if process in ("SLS", "DED"):
            return {
                "process": process,
                "supports_required": False,
                "reason": f"{process} bed/substrate provides support; no generated supports needed",
                "overhang_deg": overhang_deg,
            }
        required = overhang_deg < threshold
        return {
            "process": process,
            "supports_required": required,
            "overhang_deg": overhang_deg,
            "threshold_deg": threshold,
            "reason": (f"overhang {overhang_deg}deg {'< ' if required else '>= '}{threshold}deg threshold"),
            "strategy": "tree/linear support generation" if required else "self-supporting",
            "post_step": "support removal + finish machining allowance" if required else None,
        }

    def estimate_porosity(self, process: str) -> dict[str, Any]:
        """Estimate nominal porosity (percent) by process family.

        Returns a nominal midpoint and range with wide grade/machine
        uncertainty per MATE-SAFE-003 (no fabricated precision).
        """
        if process not in _POROSITY_PCT:
            return {
                "process": process,
                "porosity_pct": None,
                "state": INSUFFICIENT_DATA,
                "reason": f"no porosity data for process: {process}",
            }
        lo, hi = _POROSITY_PCT[process]
        nominal = (lo + hi) / 2.0
        return {
            "process": process,
            "porosity_pct": nominal,
            "range_pct": [lo, hi],
            "unit": "percent",
            "basis": "Gibson/Rosen/Stucker nominal range; verify per build",
            "uncertainty": "parameter-set and material dependent",
        }

    def check_residual_stress(self, process: str) -> dict[str, Any]:
        """Flag residual stress risk band by process family.

        Steep thermal gradients (SLS, DED) drive high residual stress and
        distortion; low-gradient processes (SLA) are low risk.
        """
        if process not in _RESIDUAL_STRESS_RISK:
            return {
                "process": process,
                "residual_stress_risk": None,
                "state": INSUFFICIENT_DATA,
                "reason": f"no residual stress data for process: {process}",
            }
        risk = _RESIDUAL_STRESS_RISK[process]
        mitigation: list[str]
        if risk == "high":
            mitigation = [
                "stress-relief anneal after build",
                "thermal isolation from substrate (wire EDM cutoff)",
                "preheat build chamber",
            ]
        elif risk == "medium":
            mitigation = [
                "uniform chamber temperature",
                "anneal if dimensional tolerance is tight",
            ]
        else:
            mitigation = ["post-cure per resin specification"]
        return {
            "process": process,
            "residual_stress_risk": risk,
            "mitigation": mitigation,
            "basis": "thermal-gradient heuristic; verify with simulation per MATE-P4",
        }

    def check_minimum_feature(self, process: str, feature_size_mm: float) -> dict[str, Any]:
        """Check whether a feature is printable at the stated size.

        Features below the process minimum cannot be resolved and must be
        either scaled up, post-machined, or omitted.
        """
        if process not in _MIN_FEATURE_MM:
            return {
                "process": process,
                "printable": None,
                "state": INSUFFICIENT_DATA,
                "reason": f"no minimum-feature data for process: {process}",
            }
        minimum = _MIN_FEATURE_MM[process]
        return {
            "process": process,
            "feature_size_mm": feature_size_mm,
            "minimum_feature_mm": minimum,
            "printable": feature_size_mm >= minimum,
            "reason": (
                f"feature {feature_size_mm}mm {'>=' if feature_size_mm >= minimum else '<'} "
                f"minimum {minimum}mm for {process}"
            ),
            "basis": "manufacturer spec-sheet typical; verify per machine",
        }


__all__ = ["AdditiveManufacturingAdvisor", "ADDITIVE_PROCESSES"]
