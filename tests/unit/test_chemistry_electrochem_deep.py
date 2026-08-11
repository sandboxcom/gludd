"""Deep edge-case tests for ``general_ludd.chemistry.electrochemistry`` — boundary
conditions, error paths, and physical invariants not exercised by the existing suite.
"""

from __future__ import annotations

import importlib.util
import math
import os

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
_ELEC_PATH = os.path.join(_PROJECT_ROOT, "src", "general_ludd", "chemistry", "electrochemistry.py")


def _load(path: str, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ec = _load(_ELEC_PATH, "ec_deep")


# — Nernst equation edge cases —


class TestNernstEdgeCases:
    def test_q_greater_than_one_decreases_potential(self):
        r = ec.nernst_equation(standard_potential_v=1.23, electron_count=2, q=10.0, temperature_k=298.15)
        assert r["value"] < 1.23

    def test_q_less_than_one_increases_potential(self):
        r = ec.nernst_equation(standard_potential_v=1.23, electron_count=2, q=0.01, temperature_k=298.15)
        assert r["value"] > 1.23

    def test_high_temperature_amplifies_deviation(self):
        r_high = ec.nernst_equation(standard_potential_v=1.23, electron_count=2, q=0.01, temperature_k=1000.0)
        r_low = ec.nernst_equation(standard_potential_v=1.23, electron_count=2, q=0.01, temperature_k=298.15)
        assert r_high["value"] > r_low["value"]

    def test_uncertainty_propagated_in_output(self):
        r = ec.nernst_equation(
            standard_potential_v=1.23,
            electron_count=2,
            q=1.0,
            temperature_k=298.15,
            standard_potential_uncertainty_v=0.05,
        )
        assert r["uncertainty"] == 0.05

    def test_method_id_in_result(self):
        r = ec.nernst_equation(standard_potential_v=0.5, electron_count=1, q=1.0, temperature_k=300.0)
        assert r["method_id"] == ec.METHOD_ID

    def test_rejects_negative_electron_count(self):
        try:
            ec.nernst_equation(standard_potential_v=0.5, electron_count=-1, q=1.0, temperature_k=298.15)
        except ValueError as e:
            assert "electron" in str(e).lower()
        else:
            raise AssertionError("expected ValueError")

    def test_rejects_zero_q(self):
        try:
            ec.nernst_equation(standard_potential_v=0.5, electron_count=1, q=0.0, temperature_k=298.15)
        except ValueError:
            pass
        else:
            raise AssertionError("expected ValueError for q=0")


# — Cell potential edge cases —


class TestCellPotentialEdgeCases:
    def test_electrolytic_negative_potential(self):
        r = ec.cell_potential(cathode_potential_v=0.40, anode_potential_v=1.00)
        assert r["value"] < 0

    def test_identical_half_reactions_zero(self):
        r = ec.cell_potential(cathode_potential_v=0.80, anode_potential_v=0.80)
        assert math.isclose(r["value"], 0.0, abs_tol=1e-12)

    def test_zero_uncertainties(self):
        r = ec.cell_potential(
            cathode_potential_v=1.00, anode_potential_v=0.40, cathode_uncertainty_v=0.0, anode_uncertainty_v=0.0
        )
        assert r["uncertainty"] == 0.0

    def test_name_is_cell_potential(self):
        r = ec.cell_potential(cathode_potential_v=0.80, anode_potential_v=0.40)
        assert r["name"] == "cell_potential"


# — Electrolysis energy edge cases —


class TestElectrolysisEnergyEdgeCases:
    def test_zero_current_yields_zero_energy(self):
        r = ec.electrolysis_energy(cell_voltage_v=2.0, current_a=0.0, duration_s=60.0)
        assert r["value"] == 0.0

    def test_zero_duration_yields_zero_energy(self):
        r = ec.electrolysis_energy(cell_voltage_v=2.0, current_a=1.0, duration_s=0.0)
        assert r["value"] == 0.0

    def test_zero_voltage_yields_zero_energy(self):
        r = ec.electrolysis_energy(cell_voltage_v=0.0, current_a=1.0, duration_s=60.0)
        assert r["value"] == 0.0

    def test_negative_voltage_ok_energy_negative(self):
        r = ec.electrolysis_energy(cell_voltage_v=-2.0, current_a=1.0, duration_s=10.0)
        assert r["value"] == -20.0

    def test_rejects_negative_duration(self):
        try:
            ec.electrolysis_energy(cell_voltage_v=2.0, current_a=1.0, duration_s=-1.0)
        except ValueError:
            pass
        else:
            raise AssertionError("expected ValueError for negative duration")

    def test_unit_is_joules(self):
        r = ec.electrolysis_energy(cell_voltage_v=1.0, current_a=1.0, duration_s=1.0)
        assert r["unit"] == "J"


# — Corrosion rate edge cases —


class TestCorrosionRateEdgeCases:
    def test_zero_current_density_yields_zero_rate(self):
        r = ec.corrosion_rate(current_density_a_m2=0.0, molar_mass_g_mol=55.845, valence=2, density_kg_m3=7874.0)
        assert r["value"] == 0.0

    def test_rejects_negative_current_density(self):
        try:
            ec.corrosion_rate(current_density_a_m2=-0.1, molar_mass_g_mol=55.845, valence=2, density_kg_m3=7874.0)
        except ValueError:
            pass
        else:
            raise AssertionError("expected ValueError")

    def test_rejects_zero_density(self):
        try:
            ec.corrosion_rate(current_density_a_m2=1.0, molar_mass_g_mol=55.845, valence=2, density_kg_m3=0.0)
        except ValueError:
            pass
        else:
            raise AssertionError("expected ValueError for density=0")

    def test_rejects_zero_molar_mass(self):
        try:
            ec.corrosion_rate(current_density_a_m2=1.0, molar_mass_g_mol=0.0, valence=2, density_kg_m3=7874.0)
        except ValueError:
            pass
        else:
            raise AssertionError("expected ValueError for molar_mass=0")

    def test_corrosion_rate_scales_linearly_with_current(self):
        r1 = ec.corrosion_rate(current_density_a_m2=1.0, molar_mass_g_mol=55.845, valence=2, density_kg_m3=7874.0)
        r2 = ec.corrosion_rate(current_density_a_m2=2.0, molar_mass_g_mol=55.845, valence=2, density_kg_m3=7874.0)
        assert math.isclose(r2["value"], 2.0 * r1["value"], rel_tol=1e-9)

    def test_unit_is_mm_per_yr(self):
        r = ec.corrosion_rate(current_density_a_m2=1.0, molar_mass_g_mol=55.845, valence=2, density_kg_m3=7874.0)
        assert r["unit"] == "mm/yr"


# — Cycling degradation edge cases —


class TestCyclingDegradationEdgeCases:
    def test_exactly_two_points_ok(self):
        r = ec.cycling_degradation(cycles=[100.0, 80.0], capacity_fade_threshold_pct=15.0)
        assert r["degraded"] is True
        assert r["fade_pct"] == 20.0

    def test_cycles_examined_field(self):
        r = ec.cycling_degradation(cycles=[100.0, 99.0, 98.0], capacity_fade_threshold_pct=10.0)
        assert r["cycles_examined"] == 3

    def test_no_fade_equal_capacity(self):
        r = ec.cycling_degradation(cycles=[100.0, 100.0, 100.0], capacity_fade_threshold_pct=5.0)
        assert r["degraded"] is False
        assert r["fade_pct"] == 0.0

    def test_increasing_capacity_zero_fade(self):
        r = ec.cycling_degradation(cycles=[90.0, 95.0, 100.0], capacity_fade_threshold_pct=5.0)
        assert r["fade_pct"] == 0.0
        assert r["degraded"] is False

    def test_rejects_single_point(self):
        try:
            ec.cycling_degradation(cycles=[100.0], capacity_fade_threshold_pct=10.0)
        except ValueError:
            pass
        else:
            raise AssertionError("expected ValueError for single point")

    def test_rejects_empty_cycles(self):
        try:
            ec.cycling_degradation(cycles=[], capacity_fade_threshold_pct=10.0)
        except ValueError:
            pass
        else:
            raise AssertionError("expected ValueError for empty cycles")

    def test_rejects_nonpositive_threshold(self):
        try:
            ec.cycling_degradation(cycles=[100.0, 90.0], capacity_fade_threshold_pct=0.0)
        except ValueError:
            pass
        else:
            raise AssertionError("expected ValueError for threshold=0")

    def test_degradation_limitations_when_flagged(self):
        r = ec.cycling_degradation(cycles=[100.0, 80.0], capacity_fade_threshold_pct=10.0)
        assert len(r["limitations"]) > 0
        assert "degradation_mode" in r["limitations"][0].lower()

    def test_no_limitations_when_not_flagged(self):
        r = ec.cycling_degradation(cycles=[100.0, 99.0], capacity_fade_threshold_pct=10.0)
        assert r["limitations"] == []

    def test_schema_version_present(self):
        r = ec.cycling_degradation(cycles=[100.0, 99.0], capacity_fade_threshold_pct=10.0)
        assert r["schema_version"] == ec.SCHEMA_VERSION


# — Impedance edge cases —


class TestImpedanceEdgeCases:
    def test_dc_frequency_zero_capacitor_open(self):
        r = ec.impedance_basic(r_ohm=2.0, r_ct=50.0, frequency_hz=0.0, cdl_f=1e-6)
        assert math.isclose(r["magnitude_ohm"], 52.0)

    def test_zero_capacitance_dc_impedance(self):
        r = ec.impedance_basic(r_ohm=2.0, r_ct=50.0, frequency_hz=1000.0, cdl_f=0.0)
        assert math.isclose(r["magnitude_ohm"], 52.0)

    def test_high_frequency_approaches_r_ohm(self):
        r = ec.impedance_basic(r_ohm=2.0, r_ct=50.0, frequency_hz=1e6, cdl_f=1e-6)
        assert r["magnitude_ohm"] < 52.0
        assert r["magnitude_ohm"] >= 2.0

    def test_with_capacitance_reduces_magnitude(self):
        r_dc = ec.impedance_basic(r_ohm=2.0, r_ct=50.0, frequency_hz=0.0, cdl_f=1e-6)
        r_ac = ec.impedance_basic(r_ohm=2.0, r_ct=50.0, frequency_hz=1000.0, cdl_f=1e-6)
        assert r_ac["magnitude_ohm"] < r_dc["magnitude_ohm"]

    def test_rejects_negative_r_ohm(self):
        try:
            ec.impedance_basic(r_ohm=-1.0, r_ct=50.0, frequency_hz=0.0)
        except ValueError:
            pass
        else:
            raise AssertionError("expected ValueError for negative r_ohm")

    def test_rejects_negative_r_ct(self):
        try:
            ec.impedance_basic(r_ohm=2.0, r_ct=-50.0, frequency_hz=0.0)
        except ValueError:
            pass
        else:
            raise AssertionError("expected ValueError for negative r_ct")

    def test_rejects_negative_frequency(self):
        try:
            ec.impedance_basic(r_ohm=2.0, r_ct=50.0, frequency_hz=-1.0)
        except ValueError:
            pass
        else:
            raise AssertionError("expected ValueError for negative frequency")

    def test_model_field_is_randles(self):
        r = ec.impedance_basic(r_ohm=2.0, r_ct=50.0, frequency_hz=0.0)
        assert r["model"] == "randles_simplified"

    def test_limitations_present(self):
        r = ec.impedance_basic(r_ohm=2.0, r_ct=50.0, frequency_hz=1000.0, cdl_f=1e-6)
        assert len(r["limitations"]) >= 1
        assert "Warburg" in r["limitations"][0] or "CPE" in r["limitations"][0]


# — Physical constants —


class TestPhysicalConstants:
    def test_faraday_constant_value(self):
        assert math.isclose(ec.FARADAY_CONSTANT_C_MOL, 96485.33212, rel_tol=1e-6)

    def test_gas_constant_value(self):
        assert math.isclose(ec.GAS_CONSTANT_R_J_MOL_K, 8.314462618, rel_tol=1e-6)


# — Exports —


class TestExports:
    def test_public_symbols(self):
        for name in ec.__all__:
            assert hasattr(ec, name), f"__all__ entry missing: {name}"
