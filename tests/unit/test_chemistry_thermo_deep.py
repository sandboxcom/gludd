"""Deep edge-case tests for ``general_ludd.chemistry.thermo_kinetics`` — boundary
conditions, error paths, and physical invariants not exercised by the existing suite.
"""

from __future__ import annotations

import importlib.util
import math
import os

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
_CORE_PATH = os.path.join(_PROJECT_ROOT, "src", "general_ludd", "chemistry", "core.py")
_THERMO_PATH = os.path.join(_PROJECT_ROOT, "src", "general_ludd", "chemistry", "thermo_kinetics.py")


def _load(path: str, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


th = _load(_THERMO_PATH, "th_deep")


# — Equilibrium constant edge cases —


class TestEquilibriumConstantEdge:
    def test_rejects_invalid_basis(self):
        try:
            th.equilibrium_constant(delta_g_kJ_per_mol=-10.0, temperature_K=298.15, basis="molarity")
        except ValueError:
            pass
        else:
            raise AssertionError("expected ValueError for invalid basis")

    def test_rejects_negative_temperature(self):
        try:
            th.equilibrium_constant(delta_g_kJ_per_mol=-10.0, temperature_K=-1.0)
        except ValueError:
            pass
        else:
            raise AssertionError("expected ValueError for negative T")

    def test_at_equilibrium_delta_g_zero_k_is_one(self):
        r = th.equilibrium_constant(delta_g_kJ_per_mol=0.0, temperature_K=500.0)
        assert math.isclose(r["value"], 1.0, abs_tol=1e-12)

    def test_extremely_favorable_very_large_k(self):
        r = th.equilibrium_constant(delta_g_kJ_per_mol=-200.0, temperature_K=298.15)
        assert r["value"] > 1e20

    def test_extremely_unfavorable_very_small_k(self):
        r = th.equilibrium_constant(delta_g_kJ_per_mol=200.0, temperature_K=298.15)
        assert 0 < r["value"] < 1e-20

    def test_uncertainty_zero_when_not_provided(self):
        r = th.equilibrium_constant(delta_g_kJ_per_mol=-10.0, temperature_K=298.15)
        assert r["uncertainty"] == 0.0

    def test_temperature_recorded_in_output(self):
        r = th.equilibrium_constant(delta_g_kJ_per_mol=-10.0, temperature_K=350.0)
        assert r["temperature_K"] == 350.0


# — Arrhenius rate edge cases —


class TestArrheniusRateEdge:
    def test_zero_pre_exponential_zero_rate(self):
        r = th.arrhenius_rate(pre_exponential=0.0, activation_energy_kJ_per_mol=50.0, temperature_K=298.15)
        assert r["value"] == 0.0

    def test_zero_activation_energy(self):
        r = th.arrhenius_rate(pre_exponential=1e10, activation_energy_kJ_per_mol=0.0, temperature_K=298.15)
        assert math.isclose(r["value"], 1e10, rel_tol=1e-9)

    def test_rejects_negative_pre_exponential(self):
        try:
            th.arrhenius_rate(pre_exponential=-1.0, activation_energy_kJ_per_mol=50.0, temperature_K=298.15)
        except ValueError:
            pass
        else:
            raise AssertionError("expected ValueError for negative A")

    def test_rejects_zero_temperature(self):
        try:
            th.arrhenius_rate(pre_exponential=1e10, activation_energy_kJ_per_mol=50.0, temperature_K=0.0)
        except ValueError:
            pass
        else:
            raise AssertionError("expected ValueError for T=0")

    def test_extremely_high_temperature_makes_rate_approach_A(self):
        r = th.arrhenius_rate(pre_exponential=1e10, activation_energy_kJ_per_mol=50.0, temperature_K=1e6)
        assert r["value"] / 1e10 > 0.99

    def test_custom_unit_preserved(self):
        r = th.arrhenius_rate(
            pre_exponential=1e12, activation_energy_kJ_per_mol=40.0, temperature_K=298.15, unit="M-1s-1"
        )
        assert r["unit"] == "M-1s-1"

    def test_uncertainty_zero_when_not_provided(self):
        r = th.arrhenius_rate(pre_exponential=1e10, activation_energy_kJ_per_mol=50.0, temperature_K=298.15)
        assert r["uncertainty"] == 0.0


# — Phase stability edge cases —


class TestPhaseStabilityEdge:
    def test_subliming_substance_co2_gas_above_boil(self):
        r = th.check_phase_stability(substance="carbon dioxide", temperature_K=300.0)
        assert r["stable_phase"] == "gas"

    def test_subliming_substance_co2_solid_below_boil(self):
        r = th.check_phase_stability(substance="carbon dioxide", temperature_K=150.0)
        assert r["stable_phase"] == "solid"

    def test_case_insensitive_lookup(self):
        r = th.check_phase_stability(substance="Water", temperature_K=250.0)
        assert r["stable_phase"] == "solid"
        assert r["status"] == "succeeded"

    def test_ethanol_liquid_at_room_temperature(self):
        r = th.check_phase_stability(substance="ethanol", temperature_K=298.15)
        assert r["stable_phase"] == "liquid"

    def test_ethanol_gas_above_boiling(self):
        r = th.check_phase_stability(substance="ethanol", temperature_K=400.0)
        assert r["stable_phase"] == "gas"

    def test_hydrogen_solid_at_cryogenic(self):
        r = th.check_phase_stability(substance="hydrogen", temperature_K=10.0)
        assert r["stable_phase"] == "solid"

    def test_unknown_substance_has_degraded_status(self):
        r = th.check_phase_stability(substance="unobtainium", temperature_K=298.15)
        assert r["status"] == "degraded"
        assert r["stable_phase"] is None

    def test_conditions_list_contains_temp_and_pressure(self):
        r = th.check_phase_stability(substance="water", temperature_K=298.15)
        names = {c["name"] for c in r["conditions"]}
        assert "temperature" in names
        assert "pressure" in names

    def test_run_id_is_unique(self):
        r1 = th.check_phase_stability(substance="water", temperature_K=298.15)
        r2 = th.check_phase_stability(substance="water", temperature_K=298.15)
        assert r1["run_id"] != r2["run_id"]

    def test_verification_pass_for_known_substance(self):
        r = th.check_phase_stability(substance="water", temperature_K=298.15)
        statuses = {v["status"] for v in r["verification"]}
        assert statuses == {"pass"}


# — Mass balance edge cases —


class TestMassBalanceEdge:
    def test_empty_reactants_and_products_refused(self):
        r = th.mass_balance_check(reactants=[], products=[])
        assert r["status"] == "refused"

    def test_only_reactants_no_products_fails(self):
        r = th.mass_balance_check(
            reactants=[{"formula": "H2O", "moles": 1.0}],
            products=[],
        )
        assert r["status"] == "failed"

    def test_exact_conservation(self):
        r = th.mass_balance_check(
            reactants=[{"formula": "NaCl", "moles": 1.0}],
            products=[{"formula": "NaCl", "moles": 1.0}],
        )
        assert r["status"] == "succeeded"

    def test_delta_field_present_on_failure(self):
        r = th.mass_balance_check(
            reactants=[{"formula": "H2", "moles": 2.0}, {"formula": "O2", "moles": 1.0}],
            products=[{"formula": "H2O", "moles": 3.0}],
        )
        check = next(v for v in r["verification"] if v["check"] == "mass_balance")
        assert check["status"] == "fail"
        assert "delta_g" in check

    def test_run_id_present(self):
        r = th.mass_balance_check(
            reactants=[{"formula": "H2O", "moles": 1.0}],
            products=[{"formula": "H2O", "moles": 1.0}],
        )
        assert "run_id" in r


# — Energy balance edge cases —


class TestEnergyBalanceEdge:
    def test_empty_lists_zero_each_side_balanced(self):
        r = th.energy_balance_check(inputs=[], outputs=[])
        assert r["status"] == "succeeded"
        assert r["input_energy_kJ"] == 0.0
        assert r["output_energy_kJ"] == 0.0

    def test_missing_energy_kj_defaults_to_zero(self):
        r = th.energy_balance_check(
            inputs=[{"foo": "bar"}],
            outputs=[{"energy_kJ": 0.0}],
        )
        assert r["status"] == "succeeded"

    def test_imbalance_produces_delta(self):
        r = th.energy_balance_check(
            inputs=[{"energy_kJ": 100.0}],
            outputs=[],
        )
        check = next(v for v in r["verification"] if v["check"] == "energy_balance")
        assert check["status"] == "fail"
        assert check["delta_kJ"] == 100.0

    def test_imbalance_has_error_code(self):
        r = th.energy_balance_check(
            inputs=[{"energy_kJ": 100.0}],
            outputs=[],
        )
        assert any("energy_imbalance" in e["code"] for e in r["errors"])

    def test_schema_version_always_present(self):
        r = th.energy_balance_check(inputs=[], outputs=[])
        assert r["schema_version"] == th.SCHEMA_VERSION


# — Limiting reactant edge cases —


class TestLimitingReactantEdge:
    def test_rejects_zero_coefficient(self):
        try:
            th.limiting_reactant(
                reactants=[
                    {"formula": "H2", "moles": 5.0, "coefficient": 0},
                    {"formula": "O2", "moles": 3.0, "coefficient": 1},
                ]
            )
        except ValueError:
            pass
        else:
            raise AssertionError("expected ValueError for coefficient=0")

    def test_rejects_negative_coefficient(self):
        try:
            th.limiting_reactant(
                reactants=[
                    {"formula": "H2", "moles": 5.0, "coefficient": -2},
                ]
            )
        except ValueError:
            pass
        else:
            raise AssertionError("expected ValueError for negative coefficient")

    def test_single_reactant_is_limiting(self):
        r = th.limiting_reactant(
            reactants=[
                {"formula": "H2O", "moles": 5.0, "coefficient": 1},
            ]
        )
        assert r["limiting_reactant"] == "H2O"
        assert r["status"] == "succeeded"

    def test_default_coefficient_is_one(self):
        r = th.limiting_reactant(
            reactants=[
                {"formula": "H2", "moles": 2.0},
                {"formula": "O2", "moles": 5.0},
            ]
        )
        assert r["limiting_reactant"] == "H2"
        assert r["per_reactant_ratio"]["H2"] == 2.0
        assert r["per_reactant_ratio"]["O2"] == 5.0

    def test_tie_goes_to_first(self):
        r = th.limiting_reactant(
            reactants=[
                {"formula": "A", "moles": 1.0, "coefficient": 1},
                {"formula": "B", "moles": 1.0, "coefficient": 1},
            ]
        )
        assert r["limiting_reactant"] == "A"

    def test_empty_reactants_refused(self):
        r = th.limiting_reactant(reactants=[])
        assert r["status"] == "refused"
        assert r["limiting_reactant"] is None

    def test_extent_of_reaction_matches_minimum_ratio(self):
        r = th.limiting_reactant(
            reactants=[
                {"formula": "H2", "moles": 5.0, "coefficient": 2},
                {"formula": "O2", "moles": 3.0, "coefficient": 1},
            ]
        )
        assert math.isclose(r["extent_of_reaction_mol"], 2.5)


# — Ideal gas law edge cases —


class TestIdealGasLawEdge:
    def test_both_none_raises(self):
        try:
            th.ideal_gas_law(pressure_Pa=None, volume_m3=None, moles=1.0, temperature_K=298.15)
        except ValueError:
            pass
        else:
            raise AssertionError("expected ValueError for both None")

    def test_both_specified_raises(self):
        try:
            th.ideal_gas_law(pressure_Pa=101325.0, volume_m3=0.0224, moles=1.0, temperature_K=298.15)
        except ValueError:
            pass
        else:
            raise AssertionError("expected ValueError for both specified")

    def test_rejects_negative_moles(self):
        try:
            th.ideal_gas_law(pressure_Pa=None, volume_m3=1.0, moles=-1.0, temperature_K=298.15)
        except ValueError:
            pass
        else:
            raise AssertionError("expected ValueError for negative moles")

    def test_rejects_nonpositive_temperature(self):
        try:
            th.ideal_gas_law(pressure_Pa=None, volume_m3=1.0, moles=1.0, temperature_K=0.0)
        except ValueError:
            pass
        else:
            raise AssertionError("expected ValueError for T=0")

    def test_zero_volume_raises_when_solving_pressure(self):
        try:
            th.ideal_gas_law(pressure_Pa=None, volume_m3=0.0, moles=1.0, temperature_K=298.15)
        except ValueError:
            pass
        else:
            raise AssertionError("expected ValueError for V=0")

    def test_zero_pressure_raises_when_solving_volume(self):
        try:
            th.ideal_gas_law(pressure_Pa=0.0, volume_m3=None, moles=1.0, temperature_K=298.15)
        except ValueError:
            pass
        else:
            raise AssertionError("expected ValueError for P=0")

    def test_zero_moles_zero_pressure(self):
        r = th.ideal_gas_law(pressure_Pa=None, volume_m3=1.0, moles=0.0, temperature_K=298.15)
        assert r["value"] == 0.0

    def test_temperature_and_moles_recorded_in_output(self):
        r = th.ideal_gas_law(pressure_Pa=None, volume_m3=1.0, moles=2.5, temperature_K=350.0)
        assert r["temperature_K"] == 350.0
        assert r["moles"] == 2.5

    def test_solve_volume_at_stp(self):
        r = th.ideal_gas_law(pressure_Pa=101325.0, volume_m3=None, moles=1.0, temperature_K=273.15)
        assert math.isclose(r["value"], 0.022414, rel_tol=1e-3)
        assert r["unit"] == "m^3"

    def test_method_id_in_output(self):
        r = th.ideal_gas_law(pressure_Pa=None, volume_m3=1.0, moles=1.0, temperature_K=298.15)
        assert "method_id" in r


# — PHASE_BOUNDS registry —


class TestPhaseBoundsRegistry:
    def test_contains_expected_substances(self):
        assert "water" in th.PHASE_BOUNDS
        assert "ethanol" in th.PHASE_BOUNDS
        assert "ammonia" in th.PHASE_BOUNDS
        assert "oxygen" in th.PHASE_BOUNDS

    def test_entries_have_melt_and_boil(self):
        for bounds in th.PHASE_BOUNDS.values():
            assert "t_melt_K" in bounds
            assert "t_boil_K" in bounds

    def test_gas_constant_value(self):
        assert math.isclose(th.GAS_CONSTANT_J_PER_MOL_K, 8.314462618, rel_tol=1e-6)


# — Exports —


class TestExports:
    def test_all_accessible(self):
        for name in th.__all__:
            assert hasattr(th, name), f"__all__ entry missing: {name}"
