"""CHEM-017 process and scale-up — heat/mass transfer, mixing, runaway, separation.

Implements CHEM-017 (process/scale-up) from
``docs/specs/FEATURE_CHEMISTRY_EXPERT.md`` §7.5:

    Scale-up results explicitly consider mixing, mass/heat transfer,
    surface/volume effects, pressure, gas evolution, accumulation,
    exotherm/runaway, relief, materials compatibility, separations,
    emissions, and waste. A lab-scale procedure cannot be linearly scaled
    into an executable plan.

Every report emitted by :class:`ProcessScaleUp` includes the
``lab_scale_not_linearly_scalable`` caveat per the spec — surface/volume
ratio, mixing, and heat removal scale non-linearly with vessel size.

This module is decision-support only. It does not authorize a plant campaign,
replace a HAZOP, or write an executable batch ticket; high-risk results MUST
flow through ``hazard_review`` and ``protocol_draft`` before any actionable
artifact is produced.
"""

from __future__ import annotations

from typing import Any

SCHEMA_VERSION = "1.0"
METHOD_ID = "chemistry-process@0.1.0"

LINEAR_SCALE_NOT_VALID = "lab_scale_not_linearly_scalable"


def _limit(code: str, detail: str) -> str:
    return f"{code}: {detail}"


class ProcessScaleUp:
    """Evaluate scale-dependent hazards for a chemistry process.

    Each method returns a dict record carrying ``schema_version``, a numeric
    value, a regime/risk classification, and a ``limitations`` list. The class
    is stateless; instances exist so callers can hang configuration (facility
    profile, unit ops) off a single object in future phases.
    """

    schema_version: str = SCHEMA_VERSION
    method_id: str = METHOD_ID

    def heat_transfer_check(
        self,
        lab_volume_l: float,
        plant_volume_l: float,
        lab_surface_area_m2: float,
    ) -> dict[str, Any]:
        """Surface-area / volume scaling check.

        For geometrically similar vessels, surface area scales as
        ``V^(2/3)`` — so the surface/volume ratio (which governs heat-removal
        capacity per unit reactor volume) decreases as ``V^(-1/3)``. A 1000x
        volume increase therefore cuts S/V by a factor of 10.

        ``lab_surface_area_m2`` is taken as the measured jacket/wall area of
        the lab vessel. The plant area is projected from geometric similarity,
        not from a linear ``A_lab * (V_plant/V_lab)`` extrapolation.
        """
        if lab_volume_l <= 0.0:
            raise ValueError("lab_volume_l must be > 0")
        if plant_volume_l <= 0.0:
            raise ValueError("plant_volume_l must be > 0")
        if lab_surface_area_m2 <= 0.0:
            raise ValueError("lab_surface_area_m2 must be > 0")

        volume_ratio = plant_volume_l / lab_volume_l
        plant_surface_area = lab_surface_area_m2 * (volume_ratio ** (2.0 / 3.0))
        lab_sv = lab_surface_area_m2 / (lab_volume_l / 1000.0)
        plant_sv = plant_surface_area / (plant_volume_l / 1000.0)
        sv_ratio_loss = lab_sv / plant_sv if plant_sv > 0 else float("inf")

        return {
            "schema_version": self.schema_version,
            "name": "heat_transfer_scale_check",
            "lab_surface_area_m2": lab_surface_area_m2,
            "plant_surface_area_m2": plant_surface_area,
            "lab_sv_ratio": lab_sv,
            "plant_sv_ratio": plant_sv,
            "sv_ratio_loss_factor": sv_ratio_loss,
            "unit_area": "m^2",
            "unit_sv": "m^2/m^3",
            "limitations": [
                LINEAR_SCALE_NOT_VALID,
                _limit(
                    "heat_removal_per_volume_decreases",
                    f"S/V falls by {sv_ratio_loss:.2f}x; cooling capacity per "
                    "unit reactor volume is the rate-limiting constraint at scale",
                ),
            ],
        }

    def mixing_assessment(
        self,
        impeller_diameter_m: float,
        rotational_speed_rpm: float,
        fluid_density_kg_m3: float,
        fluid_viscosity_pa_s: float,
    ) -> dict[str, Any]:
        """Reynolds-number based mixing regime classification.

        ``Re = (rho * N * D^2) / mu`` for a stirred vessel. Regime boundaries
        follow the standard chemical-engineering convention: ``Re < 10``
        laminar, ``10 <= Re < 10000`` transitional, ``Re >= 10000`` turbulent.

        Turbulent regime does NOT mean adequate mixing at plant scale —
        blend time, power number, and heat/mass transfer coefficients all
        shift with impeller geometry and vessel geometry.
        """
        if impeller_diameter_m <= 0.0:
            raise ValueError("impeller_diameter_m must be > 0")
        if rotational_speed_rpm < 0.0:
            raise ValueError("rotational_speed_rpm must be >= 0")
        if fluid_density_kg_m3 <= 0.0:
            raise ValueError("fluid_density_kg_m3 must be > 0")
        if fluid_viscosity_pa_s <= 0.0:
            raise ValueError("fluid_viscosity_pa_s must be > 0")

        n_hz = rotational_speed_rpm / 60.0
        re = (fluid_density_kg_m3 * n_hz * impeller_diameter_m**2) / fluid_viscosity_pa_s

        if re < 10.0:
            regime = "laminar"
        elif re < 10000.0:
            regime = "transitional"
        else:
            regime = "turbulent"

        return {
            "schema_version": self.schema_version,
            "name": "mixing_assessment",
            "reynolds_number": re,
            "regime": regime,
            "impeller_diameter_m": impeller_diameter_m,
            "rotational_speed_rpm": rotational_speed_rpm,
            "limitations": [
                LINEAR_SCALE_NOT_VALID,
                _limit(
                    "blend_time_scales_with_geometry",
                    "blend time and power input must be re-evaluated at plant "
                    "geometry; do not assume constant tip speed",
                ),
            ],
        }

    def runaway_risk(
        self,
        reaction_enthalpy_kj_mol: float,
        adiabatic_temp_rise_k: float,
        heat_removal_capacity_kw: float,
        process_temp_k: float,
    ) -> dict[str, Any]:
        """Exotherm / thermal-runaway risk screen.

        Risk tier is driven primarily by adiabatic temperature rise and
        reaction enthalpy:

        * ``low`` — benign reaction (small ``ΔT_ad``, low exotherm).
        * ``moderate`` — moderate exotherm; cooling adequate if well-mixed.
        * ``high`` — large adiabatic rise; runaway plausible if cooling lost.
        * ``severe`` — runaway expected without active, redundant cooling.

        This is a screening tool, not a replacement for adiabatic calorimetry
        (DSC, ARC, RC1e). A high/severe result MUST trigger a HAZOP and
        emergency-relief design (DIERS) before scale-up.
        """
        if process_temp_k <= 0.0:
            raise ValueError("process_temp_k must be > 0 K")
        if heat_removal_capacity_kw < 0.0:
            raise ValueError("heat_removal_capacity_kw must be >= 0")

        exotherm = abs(reaction_enthalpy_kj_mol)

        if adiabatic_temp_rise_k <= 10.0 and exotherm <= 50.0:
            risk = "low"
        elif adiabatic_temp_rise_k <= 50.0 and exotherm <= 150.0:
            risk = "moderate" if heat_removal_capacity_kw > 0.0 else "high"
        elif adiabatic_temp_rise_k <= 200.0 or exotherm <= 300.0:
            risk = "high"
        else:
            risk = "severe"

        limitations: list[str] = [LINEAR_SCALE_NOT_VALID]
        if risk in {"high", "severe"}:
            limitations.append(
                _limit(
                    "exotherm_runaway_risk",
                    f"adiabatic_temp_rise={adiabatic_temp_rise_k:.1f} K; require "
                    "HAZOP + DIERS emergency relief design before scale-up",
                )
            )
            limitations.append(
                _limit(
                    "heat_removal_scales_sublinearly",
                    "cooling per unit volume drops as vessel grows; plant heat "
                    "removal may be inadequate even when lab cooling was fine",
                )
            )
        if heat_removal_capacity_kw == 0.0:
            limitations.append(
                _limit(
                    "no_heat_removal_specified",
                    "plant cooling capacity not provided; risk tier assumes worst-case adiabatic behavior",
                )
            )

        return {
            "schema_version": self.schema_version,
            "name": "runaway_risk",
            "runaway_risk": risk,
            "reaction_enthalpy_kj_mol": reaction_enthalpy_kj_mol,
            "adiabatic_temp_rise_k": adiabatic_temp_rise_k,
            "heat_removal_capacity_kw": heat_removal_capacity_kw,
            "process_temp_k": process_temp_k,
            "limitations": limitations,
        }

    def separation_feasibility(
        self,
        method: str,
        relative_volatility: float | None = None,
        feed_composition: float | None = None,
        product_purity: float | None = None,
    ) -> dict[str, Any]:
        """Assess feasibility of a downstream separation.

        Currently supports ``distillation``: when relative volatility is close
        to unity (``α < 1.05``), ordinary distillation is uneconomic and an
        azeotrope / entrainer / pressure-swing alternative is required. Higher
        purities demand exponentially more stages as ``α -> 1``.

        Other methods (extraction, crystallization, membranes, chromatography)
        return ``feasible=True`` with a ``method_not_characterized`` limitation
        — the caller MUST supply a method-specific feasibility model.
        """
        limitations: list[str] = [LINEAR_SCALE_NOT_VALID]
        feasible: bool
        detail: str

        if method == "distillation":
            if relative_volatility is None or relative_volatility <= 0.0:
                raise ValueError("distillation requires relative_volatility > 0")
            if feed_composition is None or not 0.0 < feed_composition < 1.0:
                raise ValueError("feed_composition must be in (0, 1)")
            if product_purity is None or not 0.0 < product_purity < 1.0:
                raise ValueError("product_purity must be in (0, 1)")
            if relative_volatility < 1.05:
                feasible = False
                detail = (
                    "relative_volatility<1.05: ordinary distillation infeasible; "
                    "consider azeotropic, extractive, or pressure-swing distillation"
                )
            elif relative_volatility < 1.5 and product_purity > 0.99:
                feasible = False
                detail = (
                    "low relative_volatility + high purity target: stage count explodes; consider hybrid separation"
                )
            else:
                feasible = True
                detail = (
                    f"relative_volatility={relative_volatility:.2f} supports "
                    "ordinary distillation; verify energy and tray efficiency at scale"
                )
            limitations.append(_limit("distillation_assessment", detail))
        else:
            feasible = True
            limitations.append(
                _limit(
                    "method_not_characterized",
                    f"no feasibility model registered for '{method}'; caller must supply a method-specific assessment",
                )
            )

        return {
            "schema_version": self.schema_version,
            "name": "separation_feasibility",
            "method": method,
            "feasible": feasible,
            "relative_volatility": relative_volatility,
            "feed_composition": feed_composition,
            "product_purity": product_purity,
            "limitations": limitations,
        }


__all__ = ["LINEAR_SCALE_NOT_VALID", "ProcessScaleUp"]
