"""Unit tests for ``general_ludd.chemistry.electrochemistry`` and ``process`` (Phase D).

Covers CHEM-016 (electrochemistry) and CHEM-017 (process/scale-up) from
``docs/specs/FEATURE_CHEMISTRY_EXPERT.md`` §7.5:

* Nernst equation, cell potential, electrolysis energy, impedance basics,
  corrosion rate estimation, cycling degradation flag, unit consistency.
* ProcessScaleUp: heat_transfer_check, mixing_assessment, runaway_risk,
  separation_feasibility. Lab-scale procedures MUST NOT linearly scale.

Modules are loaded by file path (mirroring ``test_chemistry_reactions.py``) so
the suite is robust to ``sys.path`` variations inside worktrees.
"""

from __future__ import annotations

import importlib.util
import math
import os

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
_CORE_PATH = os.path.join(_PROJECT_ROOT, "src", "general_ludd", "chemistry", "core.py")
_ELEC_PATH = os.path.join(_PROJECT_ROOT, "src", "general_ludd", "chemistry", "electrochemistry.py")
_PROC_PATH = os.path.join(_PROJECT_ROOT, "src", "general_ludd", "chemistry", "process.py")


def _load_module(path: str, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None, f"{name} spec failed"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


core = _load_module(_CORE_PATH, "chemistry_core_d")
electrochemistry = _load_module(_ELEC_PATH, "chemistry_electrochem_under_test")
process = _load_module(_PROC_PATH, "chemistry_process_under_test")


# ---------------------------------------------------------------------------
# CHEM-016 electrochemistry — nernst_equation
# ---------------------------------------------------------------------------


class TestNernstEquation:
    def test_standard_conditions_returns_e_standard(self):
        # At Q=1 (unit activity), ln(Q)=0 so E == E°.
        result = electrochemistry.nernst_equation(
            standard_potential_v=1.23, electron_count=2, q=1.0, temperature_k=298.15
        )
        assert result["value"] == 1.23
        assert result["unit"] == "V"

    def test_known_daniell_cell_value(self):
        # Daniell-like cell at 298 K with E°=1.10 V, 2e-, Q=1 → E=1.10.
        # With Q=0.01 (product-favored), E should exceed E°.
        result = electrochemistry.nernst_equation(
            standard_potential_v=1.10, electron_count=2, q=0.01, temperature_k=298.15
        )
        assert result["value"] > 1.10
        # Closed-form check: E = 1.10 - (RT/2F) ln(0.01)
        r, f = 8.314462618, 96485.33212
        expected = 1.10 - (r * 298.15 / (2 * f)) * math.log(0.01)
        assert math.isclose(result["value"], expected, rel_tol=1e-6)

    def test_rejects_zero_electron_count(self):
        try:
            electrochemistry.nernst_equation(standard_potential_v=0.5, electron_count=0, q=1.0, temperature_k=298.15)
        except ValueError as exc:
            assert "electron" in str(exc).lower()
        else:
            raise AssertionError("expected ValueError for electron_count=0")

    def test_rejects_nonpositive_temperature(self):
        try:
            electrochemistry.nernst_equation(standard_potential_v=0.5, electron_count=1, q=1.0, temperature_k=0.0)
        except ValueError:
            pass
        else:
            raise AssertionError("expected ValueError for T<=0")


class TestCellPotential:
    def test_two_half_reaction_cell(self):
        # Cathode (reduction) +1.00 V, anode (reduction) +0.40 V → E°cell = 0.60 V.
        result = electrochemistry.cell_potential(cathode_potential_v=1.00, anode_potential_v=0.40)
        assert math.isclose(result["value"], 0.60, abs_tol=1e-12)
        assert result["unit"] == "V"

    def test_cell_potential_carries_uncertainty(self):
        result = electrochemistry.cell_potential(
            cathode_potential_v=0.80,
            anode_potential_v=0.24,
            cathode_uncertainty_v=0.01,
            anode_uncertainty_v=0.02,
        )
        assert result["uncertainty"] > 0
        # Quadrature sum of 0.01 and 0.02.
        assert math.isclose(result["uncertainty"], math.hypot(0.01, 0.02), rel_tol=1e-9)


# ---------------------------------------------------------------------------
# CHEM-016 electrochemistry — electrolysis_energy
# ---------------------------------------------------------------------------


class TestElectrolysisEnergy:
    def test_known_energy_joules(self):
        # 2 V × 1 A × 60 s = 120 J.
        result = electrochemistry.electrolysis_energy(cell_voltage_v=2.0, current_a=1.0, duration_s=60.0)
        assert result["value"] == 120.0
        assert result["unit"] == "J"

    def test_rejects_negative_current(self):
        try:
            electrochemistry.electrolysis_energy(cell_voltage_v=2.0, current_a=-1.0, duration_s=60.0)
        except ValueError:
            pass
        else:
            raise AssertionError("expected ValueError for negative current")

    def test_energy_propagates_uncertainty(self):
        result = electrochemistry.electrolysis_energy(
            cell_voltage_v=2.0,
            current_a=1.0,
            duration_s=60.0,
            voltage_uncertainty_v=0.05,
            current_uncertainty_a=0.01,
        )
        assert result["uncertainty"] > 0


# ---------------------------------------------------------------------------
# CHEM-016 electrochemistry — corrosion_rate + cycling + impedance basics
# ---------------------------------------------------------------------------


class TestCorrosionRate:
    def test_known_faradaic_corrosion_rate(self):
        # 1 A/m^2 of corrosion current density on Fe (n=2, M=55.845, rho=7874 kg/m^3).
        # Rate (mm/yr) per the Faraday relation; just sanity-check positivity + unit.
        result = electrochemistry.corrosion_rate(
            current_density_a_m2=1.0, molar_mass_g_mol=55.845, valence=2, density_kg_m3=7874.0
        )
        assert result["value"] > 0
        assert result["unit"] == "mm/yr"

    def test_corrosion_rate_rejects_zero_valence(self):
        try:
            electrochemistry.corrosion_rate(
                current_density_a_m2=1.0, molar_mass_g_mol=55.845, valence=0, density_kg_m3=7874.0
            )
        except ValueError:
            pass
        else:
            raise AssertionError("expected ValueError for valence=0")


class TestCyclingDegradation:
    def test_flags_degradation_when_capacity_fade_exceeds_threshold(self):
        result = electrochemistry.cycling_degradation(
            cycles=[100.0, 99.0, 96.0, 90.0, 80.0], capacity_fade_threshold_pct=15.0
        )
        assert result["degraded"] is True
        assert result["fade_pct"] >= 15.0

    def test_no_degradation_under_stable_cycling(self):
        result = electrochemistry.cycling_degradation(
            cycles=[100.0, 99.8, 99.5, 99.1], capacity_fade_threshold_pct=15.0
        )
        assert result["degraded"] is False


class TestImpedanceBasics:
    def test_warburg_and_charge_transfer(self):
        # Simple series Randles sanity check: |Z| at ω=0 dominated by R_ct.
        result = electrochemistry.impedance_basic(r_ohm=2.0, r_ct=50.0, frequency_hz=0.0)
        assert result["magnitude_ohm"] >= 50.0
        assert result["unit"] == "ohm"


# ---------------------------------------------------------------------------
# CHEM-017 process — ProcessScaleUp
# ---------------------------------------------------------------------------


class TestProcessScaleUpHeatTransfer:
    def test_surface_to_volume_ratio_decreases_with_scale(self):
        psu = process.ProcessScaleUp()
        lab = psu.heat_transfer_check(lab_volume_l=1.0, plant_volume_l=1000.0, lab_surface_area_m2=0.05)
        assert lab["plant_surface_area_m2"] > 0
        # Surface/volume ratio MUST be smaller at plant scale.
        assert lab["plant_sv_ratio"] < lab["lab_sv_ratio"]
        assert "lab_scale_not_linearly_scalable" in lab["limitations"]

    def test_heat_transfer_rejects_zero_lab_volume(self):
        psu = process.ProcessScaleUp()
        try:
            psu.heat_transfer_check(lab_volume_l=0.0, plant_volume_l=100.0, lab_surface_area_m2=0.1)
        except ValueError:
            pass
        else:
            raise AssertionError("expected ValueError for lab_volume_l=0")


class TestProcessScaleUpMixing:
    def test_reynolds_number_computed_and_classified(self):
        psu = process.ProcessScaleUp()
        result = psu.mixing_assessment(
            impeller_diameter_m=0.5,
            rotational_speed_rpm=120.0,
            fluid_density_kg_m3=1000.0,
            fluid_viscosity_pa_s=0.001,
        )
        assert result["reynolds_number"] > 0
        assert result["regime"] in {"laminar", "transitional", "turbulent"}

    def test_reynolds_number_rejects_zero_viscosity(self):
        psu = process.ProcessScaleUp()
        try:
            psu.mixing_assessment(
                impeller_diameter_m=0.5,
                rotational_speed_rpm=120.0,
                fluid_density_kg_m3=1000.0,
                fluid_viscosity_pa_s=0.0,
            )
        except ValueError:
            pass
        else:
            raise AssertionError("expected ValueError for zero viscosity")


class TestProcessScaleUpRunawayRisk:
    def test_high_exotherm_flags_runaway_risk(self):
        psu = process.ProcessScaleUp()
        result = psu.runaway_risk(
            reaction_enthalpy_kj_mol=-250.0,
            adiabatic_temp_rise_k=200.0,
            heat_removal_capacity_kw=1.0,
            process_temp_k=350.0,
        )
        assert result["runaway_risk"] in {"low", "moderate", "high", "severe"}
        assert result["runaway_risk"] in {"high", "severe"}
        assert any("exotherm" in lim or "runaway" in lim or "heat removal" in lim for lim in result["limitations"])

    def test_benign_reaction_low_risk(self):
        psu = process.ProcessScaleUp()
        result = psu.runaway_risk(
            reaction_enthalpy_kj_mol=-5.0,
            adiabatic_temp_rise_k=2.0,
            heat_removal_capacity_kw=10.0,
            process_temp_k=298.15,
        )
        assert result["runaway_risk"] == "low"


class TestProcessScaleUpSeparationFeasibility:
    def test_distillation_feasible_for_volatile_gap(self):
        psu = process.ProcessScaleUp()
        result = psu.separation_feasibility(
            method="distillation",
            relative_volatility=2.5,
            feed_composition=0.5,
            product_purity=0.95,
        )
        assert result["feasible"] is True
        assert "limitations" in result

    def test_distillation_infeasible_for_azeotrope(self):
        psu = process.ProcessScaleUp()
        result = psu.separation_feasibility(
            method="distillation",
            relative_volatility=1.02,
            feed_composition=0.5,
            product_purity=0.99,
        )
        assert result["feasible"] is False
        assert any("relative_volatility" in lim or "low" in lim or "azeotrope" in lim for lim in result["limitations"])


class TestProcessScaleUpLinearFlag:
    def test_class_carries_non_linear_scale_up_warning(self):
        psu = process.ProcessScaleUp()
        # The class MUST surface the non-linear scale-up caveat on every report.
        report = psu.runaway_risk(
            reaction_enthalpy_kj_mol=-10.0,
            adiabatic_temp_rise_k=5.0,
            heat_removal_capacity_kw=5.0,
            process_temp_k=298.15,
        )
        assert "lab_scale_not_linearly_scalable" in report["limitations"]
