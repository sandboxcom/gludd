"""Unit tests for ``general_ludd.chemistry.thermo_kinetics`` (Phase C).

Covers every exported function with edge cases, invalid inputs, and
boundary conditions. Complements ``test_chemistry_thermo.py`` which
mixes thermo and spectroscopy tests.
"""

from __future__ import annotations

import importlib.util
import math
import os

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
_THERMO_PATH = os.path.join(_PROJECT_ROOT, "src", "general_ludd", "chemistry", "thermo_kinetics.py")


def _load_module(path: str, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None, f"{name} spec failed"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


thermo = _load_module(_THERMO_PATH, "chemistry_thermo_kinetics_under_test")


# ---------------------------------------------------------------------------
# equilibrium_constant
# ---------------------------------------------------------------------------


class TestEquilibriumConstant:
    def test_negative_delta_g_large_k(self):
        rec = thermo.equilibrium_constant(delta_g_kJ_per_mol=-50.0, temperature_K=298.15)
        assert rec["name"] == "equilibrium_constant"
        assert rec["unit"] == "dimensionless"
        expected = math.exp(50000.0 / (thermo.GAS_CONSTANT_J_PER_MOL_K * 298.15))
        assert math.isclose(rec["value"], expected, rel_tol=1e-3)

    def test_positive_delta_g_small_k(self):
        rec = thermo.equilibrium_constant(delta_g_kJ_per_mol=50.0, temperature_K=298.15)
        assert 0.0 < rec["value"] < 1.0e-8

    def test_zero_delta_g_unit_k(self):
        rec = thermo.equilibrium_constant(delta_g_kJ_per_mol=0.0, temperature_K=298.15)
        assert math.isclose(rec["value"], 1.0, abs_tol=1e-12)

    def test_kp_basis_label(self):
        rec = thermo.equilibrium_constant(delta_g_kJ_per_mol=-10.0, temperature_K=298.15, basis="pressure")
        assert rec.get("basis") == "pressure"

    def test_default_basis_concentration(self):
        rec = thermo.equilibrium_constant(delta_g_kJ_per_mol=-10.0, temperature_K=298.15)
        assert rec.get("basis") == "concentration"

    def test_uncertainty_propagated(self):
        rec = thermo.equilibrium_constant(
            delta_g_kJ_per_mol=-20.0, temperature_K=298.15, delta_g_uncertainty_kJ_per_mol=0.5
        )
        assert rec["uncertainty"] > 0.0

    def test_zero_uncertainty_no_propagation(self):
        rec = thermo.equilibrium_constant(
            delta_g_kJ_per_mol=-20.0, temperature_K=298.15, delta_g_uncertainty_kJ_per_mol=0.0
        )
        assert rec["uncertainty"] == 0.0

    def test_negative_uncertainty_no_propagation(self):
        rec = thermo.equilibrium_constant(
            delta_g_kJ_per_mol=-20.0, temperature_K=298.15, delta_g_uncertainty_kJ_per_mol=-0.5
        )
        assert rec["uncertainty"] == 0.0

    def test_rejects_zero_temperature(self):
        try:
            thermo.equilibrium_constant(delta_g_kJ_per_mol=-10.0, temperature_K=0.0)
            raise AssertionError("expected ValueError")
        except ValueError:
            pass

    def test_rejects_negative_temperature(self):
        try:
            thermo.equilibrium_constant(delta_g_kJ_per_mol=-10.0, temperature_K=-1.0)
            raise AssertionError("expected ValueError")
        except ValueError:
            pass

    def test_rejects_invalid_basis(self):
        try:
            thermo.equilibrium_constant(delta_g_kJ_per_mol=-10.0, temperature_K=298.15, basis="molarity")
            raise AssertionError("expected ValueError")
        except ValueError:
            pass

    def test_record_includes_temperature(self):
        rec = thermo.equilibrium_constant(delta_g_kJ_per_mol=-10.0, temperature_K=350.0)
        assert rec["temperature_K"] == 350.0


# ---------------------------------------------------------------------------
# arrhenius_rate
# ---------------------------------------------------------------------------


class TestArrheniusRate:
    def test_basic_value(self):
        rec = thermo.arrhenius_rate(pre_exponential=1.0e10, activation_energy_kJ_per_mol=50.0, temperature_K=298.15)
        assert rec["name"] == "rate_constant"
        expected = 1.0e10 * math.exp(-50000.0 / (thermo.GAS_CONSTANT_J_PER_MOL_K * 298.15))
        assert math.isclose(rec["value"], expected, rel_tol=1e-6)

    def test_higher_temp_higher_rate(self):
        cold = thermo.arrhenius_rate(pre_exponential=1.0e10, activation_energy_kJ_per_mol=80.0, temperature_K=300.0)
        hot = thermo.arrhenius_rate(pre_exponential=1.0e10, activation_energy_kJ_per_mol=80.0, temperature_K=400.0)
        assert hot["value"] > cold["value"]

    def test_unit_propagated(self):
        rec = thermo.arrhenius_rate(
            pre_exponential=1.0e12, activation_energy_kJ_per_mol=40.0, temperature_K=298.15, unit="L/(mol·s)"
        )
        assert rec["unit"] == "L/(mol·s)"

    def test_default_unit(self):
        rec = thermo.arrhenius_rate(pre_exponential=1.0e10, activation_energy_kJ_per_mol=50.0, temperature_K=298.15)
        assert rec["unit"] == "1/s"

    def test_uncertainty_propagated(self):
        rec = thermo.arrhenius_rate(
            pre_exponential=1.0e12,
            activation_energy_kJ_per_mol=60.0,
            temperature_K=298.15,
            activation_uncertainty_kJ_per_mol=2.0,
        )
        assert rec["uncertainty"] > 0.0

    def test_zero_uncertainty_no_propagation(self):
        rec = thermo.arrhenius_rate(
            pre_exponential=1.0e10,
            activation_energy_kJ_per_mol=50.0,
            temperature_K=298.15,
            activation_uncertainty_kJ_per_mol=0.0,
        )
        assert rec["uncertainty"] == 0.0

    def test_zero_pre_exponential(self):
        rec = thermo.arrhenius_rate(pre_exponential=0.0, activation_energy_kJ_per_mol=50.0, temperature_K=298.15)
        assert rec["value"] == 0.0

    def test_zero_activation_energy(self):
        rec = thermo.arrhenius_rate(pre_exponential=1.0e10, activation_energy_kJ_per_mol=0.0, temperature_K=298.15)
        assert math.isclose(rec["value"], 1.0e10, rel_tol=1e-6)

    def test_negative_uncertainty_no_propagation(self):
        rec = thermo.arrhenius_rate(
            pre_exponential=1.0e10,
            activation_energy_kJ_per_mol=50.0,
            temperature_K=298.15,
            activation_uncertainty_kJ_per_mol=-2.0,
        )
        assert rec["uncertainty"] == 0.0

    def test_rejects_negative_temperature(self):
        try:
            thermo.arrhenius_rate(pre_exponential=1.0e10, activation_energy_kJ_per_mol=50.0, temperature_K=-1.0)
            raise AssertionError("expected ValueError")
        except ValueError:
            pass

    def test_rejects_negative_pre_exponential(self):
        try:
            thermo.arrhenius_rate(pre_exponential=-1.0, activation_energy_kJ_per_mol=50.0, temperature_K=298.15)
            raise AssertionError("expected ValueError")
        except ValueError:
            pass

    def test_record_includes_temperature(self):
        rec = thermo.arrhenius_rate(pre_exponential=1.0e10, activation_energy_kJ_per_mol=50.0, temperature_K=350.0)
        assert rec["temperature_K"] == 350.0


# ---------------------------------------------------------------------------
# check_phase_stability
# ---------------------------------------------------------------------------


class TestPhaseStability:
    def test_water_liquid_at_room_temp(self):
        rec = thermo.check_phase_stability(substance="water", temperature_K=298.15, pressure_Pa=101325.0)
        assert rec["stable_phase"] == "liquid"
        assert rec["status"] == "succeeded"

    def test_water_gas_above_boiling(self):
        rec = thermo.check_phase_stability(substance="water", temperature_K=400.0, pressure_Pa=101325.0)
        assert rec["stable_phase"] == "gas"

    def test_water_solid_below_freezing(self):
        rec = thermo.check_phase_stability(substance="water", temperature_K=250.0, pressure_Pa=101325.0)
        assert rec["stable_phase"] == "solid"

    def test_ethanol_liquid_at_room_temp(self):
        rec = thermo.check_phase_stability(substance="ethanol", temperature_K=298.15)
        assert rec["stable_phase"] == "liquid"

    def test_case_insensitive_substance(self):
        rec = thermo.check_phase_stability(substance="WATER", temperature_K=298.15)
        assert rec["stable_phase"] == "liquid"

    def test_co2_solid_below_sublimation(self):
        rec = thermo.check_phase_stability(substance="carbon dioxide", temperature_K=180.0)
        assert rec["stable_phase"] == "solid"

    def test_co2_gas_above_sublimation(self):
        rec = thermo.check_phase_stability(substance="carbon dioxide", temperature_K=250.0)
        assert rec["stable_phase"] == "gas"

    def test_unknown_substance_degraded(self):
        rec = thermo.check_phase_stability(substance="unobtainium", temperature_K=298.15)
        assert rec["status"] == "degraded"
        assert rec["stable_phase"] is None
        assert len(rec.get("errors", [])) > 0

    def test_conditions_recorded(self):
        rec = thermo.check_phase_stability(substance="water", temperature_K=300.0, pressure_Pa=200000.0)
        temps = [c for c in rec["conditions"] if c["name"] == "temperature"]
        assert len(temps) == 1
        assert temps[0]["value"] == 300.0
        press = [c for c in rec["conditions"] if c["name"] == "pressure"]
        assert len(press) == 1
        assert press[0]["value"] == 200000.0

    def test_default_pressure(self):
        rec = thermo.check_phase_stability(substance="water", temperature_K=298.15)
        press = [c for c in rec["conditions"] if c["name"] == "pressure"]
        assert press[0]["value"] == 101325.0

    def test_exact_melting_point_solid(self):
        rec = thermo.check_phase_stability(substance="water", temperature_K=273.15)
        assert rec["stable_phase"] in ("solid", "liquid")

    def test_exact_boiling_point_gas(self):
        rec = thermo.check_phase_stability(substance="water", temperature_K=373.15)
        assert rec["stable_phase"] in ("liquid", "gas")

    def test_run_id_and_schema_present(self):
        rec = thermo.check_phase_stability(substance="water", temperature_K=298.15)
        assert "schema_version" in rec
        assert "run_id" in rec


# ---------------------------------------------------------------------------
# mass_balance_check
# ---------------------------------------------------------------------------


class TestMassBalance:
    def test_balanced_reaction_water_formation(self):
        rec = thermo.mass_balance_check(
            reactants=[{"formula": "H2", "moles": 2.0}, {"formula": "O2", "moles": 1.0}],
            products=[{"formula": "H2O", "moles": 2.0}],
        )
        check = next(v for v in rec["verification"] if v["check"] == "mass_balance")
        assert check["status"] == "pass"
        assert rec["status"] == "succeeded"

    def test_imbalanced_reaction_fails(self):
        rec = thermo.mass_balance_check(
            reactants=[{"formula": "H2", "moles": 2.0}, {"formula": "O2", "moles": 1.0}],
            products=[{"formula": "H2O", "moles": 3.0}],
        )
        check = next(v for v in rec["verification"] if v["check"] == "mass_balance")
        assert check["status"] == "fail"
        assert rec["status"] == "failed"
        assert len(rec.get("errors", [])) > 0

    def test_empty_reactants_and_products_refused(self):
        rec = thermo.mass_balance_check(reactants=[], products=[])
        assert rec["status"] == "refused"

    def test_single_species_each_side_balanced(self):
        rec = thermo.mass_balance_check(
            reactants=[{"formula": "H2O", "moles": 1.0}],
            products=[{"formula": "H2O", "moles": 1.0}],
        )
        check = next(v for v in rec["verification"] if v["check"] == "mass_balance")
        assert check["status"] == "pass"

    def test_delta_g_in_verification(self):
        rec = thermo.mass_balance_check(
            reactants=[{"formula": "H2O", "moles": 1.0}],
            products=[{"formula": "H2O", "moles": 1.0}],
        )
        check = next(v for v in rec["verification"] if v["check"] == "mass_balance")
        assert math.isclose(check["delta_g"], 0.0, abs_tol=1e-9)


# ---------------------------------------------------------------------------
# energy_balance_check
# ---------------------------------------------------------------------------


class TestEnergyBalance:
    def test_conserved_energy(self):
        rec = thermo.energy_balance_check(
            inputs=[{"energy_kJ": 100.0}],
            outputs=[{"energy_kJ": 60.0}, {"energy_kJ": 40.0}],
        )
        check = next(v for v in rec["verification"] if v["check"] == "energy_balance")
        assert check["status"] == "pass"

    def test_imbalanced_energy_fails(self):
        rec = thermo.energy_balance_check(
            inputs=[{"energy_kJ": 100.0}],
            outputs=[{"energy_kJ": 30.0}],
        )
        check = next(v for v in rec["verification"] if v["check"] == "energy_balance")
        assert check["status"] == "fail"
        assert rec["status"] == "failed"
        assert len(rec.get("errors", [])) > 0

    def test_empty_inputs_and_outputs(self):
        rec = thermo.energy_balance_check(inputs=[], outputs=[])
        check = next(v for v in rec["verification"] if v["check"] == "energy_balance")
        assert check["status"] == "pass"
        assert rec["input_energy_kJ"] == 0.0
        assert rec["output_energy_kJ"] == 0.0

    def test_missing_energy_key_defaults_zero(self):
        rec = thermo.energy_balance_check(
            inputs=[{}],
            outputs=[{}],
        )
        check = next(v for v in rec["verification"] if v["check"] == "energy_balance")
        assert check["status"] == "pass"
        assert rec["input_energy_kJ"] == 0.0

    def test_delta_kJ_in_verification(self):
        rec = thermo.energy_balance_check(
            inputs=[{"energy_kJ": 50.0}],
            outputs=[{"energy_kJ": 30.0}, {"energy_kJ": 20.0}],
        )
        check = next(v for v in rec["verification"] if v["check"] == "energy_balance")
        assert math.isclose(check["delta_kJ"], 0.0, abs_tol=1e-9)

    def test_schema_and_run_id_present(self):
        rec = thermo.energy_balance_check(inputs=[], outputs=[])
        assert "schema_version" in rec
        assert "run_id" in rec


# ---------------------------------------------------------------------------
# limiting_reactant
# ---------------------------------------------------------------------------


class TestLimitingReactant:
    def test_h2_limiting_in_water_formation(self):
        rec = thermo.limiting_reactant(
            reactants=[
                {"formula": "H2", "moles": 5.0, "coefficient": 2},
                {"formula": "O2", "moles": 3.0, "coefficient": 1},
            ],
        )
        assert rec["limiting_reactant"] == "H2"
        assert rec["status"] == "succeeded"
        assert math.isclose(rec["extent_of_reaction_mol"], 2.5)

    def test_default_coefficient_is_one(self):
        rec = thermo.limiting_reactant(
            reactants=[
                {"formula": "A", "moles": 2.0},
                {"formula": "B", "moles": 3.0},
            ],
        )
        assert rec["limiting_reactant"] == "A"
        assert math.isclose(rec["extent_of_reaction_mol"], 2.0)

    def test_single_reactant(self):
        rec = thermo.limiting_reactant(
            reactants=[{"formula": "A", "moles": 5.0, "coefficient": 2}],
        )
        assert rec["limiting_reactant"] == "A"

    def test_empty_reactants_refused(self):
        rec = thermo.limiting_reactant(reactants=[])
        assert rec["status"] == "refused"
        assert rec["limiting_reactant"] is None
        assert len(rec.get("errors", [])) > 0

    def test_per_reactant_ratio_included(self):
        rec = thermo.limiting_reactant(
            reactants=[
                {"formula": "H2", "moles": 5.0, "coefficient": 2},
                {"formula": "O2", "moles": 3.0, "coefficient": 1},
            ],
        )
        assert "per_reactant_ratio" in rec
        assert rec["per_reactant_ratio"]["H2"] == 2.5
        assert rec["per_reactant_ratio"]["O2"] == 3.0

    def test_rejects_zero_coefficient(self):
        try:
            thermo.limiting_reactant(
                reactants=[{"formula": "A", "moles": 1.0, "coefficient": 0}],
            )
            raise AssertionError("expected ValueError")
        except ValueError:
            pass

    def test_rejects_negative_coefficient(self):
        try:
            thermo.limiting_reactant(
                reactants=[{"formula": "A", "moles": 1.0, "coefficient": -1}],
            )
            raise AssertionError("expected ValueError")
        except ValueError:
            pass

    def test_tie_goes_to_first_listed(self):
        rec = thermo.limiting_reactant(
            reactants=[
                {"formula": "A", "moles": 2.0, "coefficient": 1},
                {"formula": "B", "moles": 2.0, "coefficient": 1},
            ],
        )
        assert rec["limiting_reactant"] == "A"

    def test_schema_and_run_id_present(self):
        rec = thermo.limiting_reactant(
            reactants=[{"formula": "A", "moles": 1.0}],
        )
        assert "schema_version" in rec
        assert "run_id" in rec


# ---------------------------------------------------------------------------
# ideal_gas_law
# ---------------------------------------------------------------------------


class TestIdealGasLaw:
    def test_solve_for_pressure(self):
        rec = thermo.ideal_gas_law(pressure_Pa=None, volume_m3=1.0, moles=1.0, temperature_K=273.15)
        assert rec["name"] == "pressure"
        assert rec["unit"] == "Pa"
        expected = thermo.GAS_CONSTANT_J_PER_MOL_K * 273.15
        assert math.isclose(rec["value"], expected, rel_tol=1e-4)

    def test_solve_for_volume(self):
        rec = thermo.ideal_gas_law(pressure_Pa=101325.0, volume_m3=None, moles=1.0, temperature_K=273.15)
        assert rec["name"] == "volume"
        assert rec["unit"] == "m^3"
        assert math.isclose(rec["value"], 0.022414, rel_tol=1e-3)

    def test_minimal_volume_high_pressure(self):
        rec = thermo.ideal_gas_law(pressure_Pa=1.0e7, volume_m3=None, moles=0.01, temperature_K=300.0)
        assert rec["name"] == "volume"
        assert rec["value"] > 0.0
        assert rec["value"] < 1.0

    def test_record_includes_temperature_and_moles(self):
        rec = thermo.ideal_gas_law(pressure_Pa=101325.0, volume_m3=None, moles=1.0, temperature_K=300.0)
        assert rec["temperature_K"] == 300.0
        assert rec["moles"] == 1.0

    def test_rejects_zero_temperature(self):
        try:
            thermo.ideal_gas_law(pressure_Pa=None, volume_m3=1.0, moles=1.0, temperature_K=0.0)
            raise AssertionError("expected ValueError")
        except ValueError:
            pass

    def test_rejects_negative_temperature(self):
        try:
            thermo.ideal_gas_law(pressure_Pa=None, volume_m3=1.0, moles=1.0, temperature_K=-1.0)
            raise AssertionError("expected ValueError")
        except ValueError:
            pass

    def test_rejects_negative_moles(self):
        try:
            thermo.ideal_gas_law(pressure_Pa=None, volume_m3=1.0, moles=-1.0, temperature_K=300.0)
            raise AssertionError("expected ValueError")
        except ValueError:
            pass

    def test_rejects_both_none(self):
        try:
            thermo.ideal_gas_law(pressure_Pa=None, volume_m3=None, moles=1.0, temperature_K=300.0)
            raise AssertionError("expected ValueError")
        except ValueError:
            pass

    def test_rejects_both_specified(self):
        try:
            thermo.ideal_gas_law(pressure_Pa=101325.0, volume_m3=1.0, moles=1.0, temperature_K=300.0)
            raise AssertionError("expected ValueError")
        except ValueError:
            pass

    def test_rejects_zero_volume_for_pressure(self):
        try:
            thermo.ideal_gas_law(pressure_Pa=None, volume_m3=0.0, moles=1.0, temperature_K=300.0)
            raise AssertionError("expected ValueError")
        except ValueError:
            pass

    def test_rejects_zero_pressure_for_volume(self):
        try:
            thermo.ideal_gas_law(pressure_Pa=0.0, volume_m3=None, moles=1.0, temperature_K=300.0)
            raise AssertionError("expected ValueError")
        except ValueError:
            pass

    def test_zero_moles_zero_result_for_pressure(self):
        rec = thermo.ideal_gas_law(pressure_Pa=None, volume_m3=1.0, moles=0.0, temperature_K=300.0)
        assert rec["value"] == 0.0

    def test_zero_moles_zero_result_for_volume(self):
        rec = thermo.ideal_gas_law(pressure_Pa=101325.0, volume_m3=None, moles=0.0, temperature_K=300.0)
        assert rec["value"] == 0.0


# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------


class TestModuleConstants:
    def test_gas_constant_is_8_314(self):
        assert math.isclose(thermo.GAS_CONSTANT_J_PER_MOL_K, 8.314462618)

    def test_schema_version_is_set(self):
        assert thermo.SCHEMA_VERSION == "1.0"

    def test_method_id_is_set(self):
        assert isinstance(thermo.METHOD_ID, str)
        assert "thermo" in thermo.METHOD_ID

    def test_phase_bounds_includes_water(self):
        assert "water" in thermo.PHASE_BOUNDS
        assert thermo.PHASE_BOUNDS["water"]["t_melt_K"] == 273.15
        assert thermo.PHASE_BOUNDS["water"]["t_boil_K"] == 373.15

    def test_phase_bounds_has_ten_substances(self):
        assert len(thermo.PHASE_BOUNDS) == 10


# ---------------------------------------------------------------------------
# Float edge cases
# ---------------------------------------------------------------------------


class TestFloatEdgeCases:
    def test_equilibrium_constant_very_small_temperature_returns_large_k(self):
        rec = thermo.equilibrium_constant(delta_g_kJ_per_mol=0.0, temperature_K=1e-10)
        assert math.isclose(rec["value"], 1.0, abs_tol=1e-12)

    def test_equilibrium_constant_overflow_raises(self):
        try:
            thermo.equilibrium_constant(delta_g_kJ_per_mol=-1.0, temperature_K=1e-10)
            raise AssertionError("expected OverflowError")
        except OverflowError:
            pass

    def test_arrhenius_very_large_activation_energy(self):
        rec = thermo.arrhenius_rate(pre_exponential=1.0e10, activation_energy_kJ_per_mol=1.0e6, temperature_K=1.0)
        assert rec["value"] == 0.0

    def test_arrhenius_very_high_temperature(self):
        rec = thermo.arrhenius_rate(pre_exponential=1.0e10, activation_energy_kJ_per_mol=50.0, temperature_K=1.0e10)
        assert math.isclose(rec["value"], 1.0e10, rel_tol=1e-6)
