"""Unit tests for ``general_ludd.chemistry.stoichiometry``.

Covers CHEM-007 stoichiometry functions:
* calculate_amounts — mass <-> moles via molar mass
* calculate_concentration — molarity solver
* calculate_yield — percent yield with uncertainty + limitations
* _propagate_relative — Gaussian uncertainty propagation
"""

from __future__ import annotations

import importlib.util
import math
import os

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
_STOI_PATH = os.path.join(_PROJECT_ROOT, "src", "general_ludd", "chemistry", "stoichiometry.py")


def _load_stoichiometry():
    spec = importlib.util.spec_from_file_location("stoichiometry_under_test", _STOI_PATH)
    assert spec is not None and spec.loader is not None, "stoichiometry spec failed"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


stoich = _load_stoichiometry()


# ---------------------------------------------------------------------------
# _propagate_relative
# ---------------------------------------------------------------------------


class TestPropagateRelative:
    def test_no_uncertainties_returns_zero(self):
        result = stoich._propagate_relative(100.0)
        assert result == 0.0

    def test_single_uncertainty(self):
        result = stoich._propagate_relative(100.0, 0.01)
        assert math.isclose(result, 1.0)

    def test_multiple_uncertainties(self):
        result = stoich._propagate_relative(100.0, 0.03, 0.04)
        expected = 100.0 * math.sqrt(0.03**2 + 0.04**2)
        assert math.isclose(result, expected)

    def test_empty_iterable(self):
        result = stoich._propagate_relative(50.0)
        assert result == 0.0

    def test_zero_values_in_uncertainties(self):
        result = stoich._propagate_relative(100.0, 0.0, 0.01, 0.0)
        expected = 100.0 * 0.01
        assert math.isclose(result, expected), f"{result} != {expected}"


# ---------------------------------------------------------------------------
# calculate_amounts
# ---------------------------------------------------------------------------


class TestCalculateAmounts:
    def test_mass_g_to_moles_water(self):
        rec = stoich.calculate_amounts(mass_g=18.015, formula="H2O")
        assert rec["name"] == "amount_substance"
        assert rec["unit"] == "mol"
        assert math.isclose(rec["value"], 1.0, rel_tol=0.01)

    def test_mass_g_to_moles_nacl(self):
        rec = stoich.calculate_amounts(mass_g=58.44, formula="NaCl")
        assert rec["unit"] == "mol"
        assert math.isclose(rec["value"], 1.0, rel_tol=0.01)

    def test_mass_g_to_moles_co2(self):
        rec = stoich.calculate_amounts(mass_g=44.009, formula="CO2")
        assert rec["unit"] == "mol"
        assert math.isclose(rec["value"], 1.0, rel_tol=0.01)

    def test_moles_to_mass_water(self):
        rec = stoich.calculate_amounts(moles=2.0, formula="H2O")
        assert rec["name"] == "mass"
        assert rec["unit"] == "g"
        assert math.isclose(rec["value"], 36.03, rel_tol=0.01)

    def test_moles_to_mass_glucose(self):
        rec = stoich.calculate_amounts(moles=1.0, formula="C6H12O6")
        assert rec["unit"] == "g"
        assert rec["value"] > 100.0

    def test_mass_g_with_uncertainty(self):
        rec = stoich.calculate_amounts(mass_g=18.015, formula="H2O", mass_uncertainty=0.01)
        assert rec["uncertainty"] > 0.0

    def test_moles_with_uncertainty(self):
        rec = stoich.calculate_amounts(moles=1.0, formula="H2O", moles_uncertainty=0.005)
        assert rec["uncertainty"] > 0.0

    def test_missing_formula_raises(self):
        try:
            stoich.calculate_amounts(mass_g=10.0)
            raise AssertionError("expected ValueError")
        except ValueError as e:
            assert "formula is required" in str(e)

    def test_both_mass_and_moles_raises(self):
        try:
            stoich.calculate_amounts(mass_g=10.0, moles=1.0, formula="H2O")
            raise AssertionError("expected ValueError")
        except ValueError:
            pass

    def test_neither_mass_nor_moles_raises(self):
        try:
            stoich.calculate_amounts(formula="H2O")
            raise AssertionError("expected ValueError")
        except ValueError:
            pass

    def test_value_record_structure(self):
        rec = stoich.calculate_amounts(mass_g=18.015, formula="H2O")
        for key in ("name", "value", "unit", "uncertainty", "method_id"):
            assert key in rec, f"missing key: {key}"

    def test_method_id_present(self):
        rec = stoich.calculate_amounts(mass_g=10.0, formula="H2O")
        assert isinstance(rec["method_id"], str)
        assert len(rec["method_id"]) > 0


# ---------------------------------------------------------------------------
# calculate_concentration
# ---------------------------------------------------------------------------


class TestCalculateConcentration:
    def test_solve_concentration_basic(self):
        rec = stoich.calculate_concentration(moles=1.0, volume_L=1.0)
        assert rec["unit"] == "mol/L"
        assert rec["name"] == "concentration"
        assert math.isclose(rec["value"], 1.0)

    def test_solve_concentration_half_molar(self):
        rec = stoich.calculate_concentration(moles=0.5, volume_L=1.0)
        assert rec["unit"] == "mol/L"
        assert math.isclose(rec["value"], 0.5)

    def test_solve_concentration_with_uncertainty(self):
        rec = stoich.calculate_concentration(moles=1.0, volume_L=1.0, moles_uncertainty=0.02, volume_uncertainty=0.01)
        assert rec["uncertainty"] > 0.0

    def test_solve_moles(self):
        rec = stoich.calculate_concentration(concentration=2.0, volume_L=0.5)
        assert rec["unit"] == "mol"
        assert rec["name"] == "amount_substance"
        assert math.isclose(rec["value"], 1.0)

    def test_solve_moles_with_uncertainty(self):
        rec = stoich.calculate_concentration(
            concentration=2.0,
            volume_L=0.5,
            concentration_uncertainty=0.01,
            volume_uncertainty=0.01,
        )
        assert rec["uncertainty"] > 0.0

    def test_solve_volume(self):
        rec = stoich.calculate_concentration(moles=0.5, concentration=1.0)
        assert rec["unit"] == "L"
        assert rec["name"] == "volume"
        assert math.isclose(rec["value"], 0.5)

    def test_solve_volume_with_uncertainty(self):
        rec = stoich.calculate_concentration(
            moles=1.0,
            concentration=2.0,
            moles_uncertainty=0.01,
            concentration_uncertainty=0.01,
        )
        assert rec["uncertainty"] > 0.0

    def test_round_trip_concentration_moles_volume(self):
        c = stoich.calculate_concentration(moles=2.0, volume_L=0.5)
        assert math.isclose(c["value"], 4.0)
        m = stoich.calculate_concentration(concentration=c["value"], volume_L=0.5)
        assert math.isclose(m["value"], 2.0)
        v = stoich.calculate_concentration(moles=m["value"], concentration=c["value"])
        assert math.isclose(v["value"], 0.5)

    def test_zero_volume_raises(self):
        try:
            stoich.calculate_concentration(moles=1.0, volume_L=0.0)
            raise AssertionError("expected ValueError")
        except ValueError as e:
            assert "non-zero" in str(e)

    def test_zero_concentration_solve_moles_raises(self):
        try:
            stoich.calculate_concentration(concentration=0.0, volume_L=1.0)
            raise AssertionError("expected ValueError")
        except ValueError as e:
            assert "non-zero" in str(e)

    def test_zero_concentration_solve_volume_raises(self):
        try:
            stoich.calculate_concentration(moles=1.0, concentration=0.0)
            raise AssertionError("expected ValueError")
        except ValueError as e:
            assert "non-zero" in str(e)

    def test_wrong_number_of_args_raises(self):
        try:
            stoich.calculate_concentration(moles=1.0)
            raise AssertionError("expected ValueError")
        except ValueError:
            pass

    def test_all_three_args_raises(self):
        try:
            stoich.calculate_concentration(moles=1.0, volume_L=1.0, concentration=1.0)
            raise AssertionError("expected ValueError")
        except ValueError:
            pass

    def test_no_args_raises(self):
        try:
            stoich.calculate_concentration()
            raise AssertionError("expected ValueError")
        except ValueError:
            pass

    def test_value_record_structure(self):
        rec = stoich.calculate_concentration(moles=1.0, volume_L=1.0)
        for key in ("name", "value", "unit", "uncertainty", "method_id"):
            assert key in rec, f"missing key: {key}"


# ---------------------------------------------------------------------------
# calculate_yield
# ---------------------------------------------------------------------------


class TestCalculateYield:
    def test_50_percent_yield(self):
        rec = stoich.calculate_yield(actual_g=5.0, theoretical_g=10.0)
        assert rec["name"] == "yield"
        assert rec["unit"] == "percent"
        assert math.isclose(rec["value"], 50.0)

    def test_80_percent_yield(self):
        rec = stoich.calculate_yield(actual_g=8.0, theoretical_g=10.0)
        assert math.isclose(rec["value"], 80.0)

    def test_100_percent_yield_no_limitations(self):
        rec = stoich.calculate_yield(actual_g=10.0, theoretical_g=10.0)
        assert math.isclose(rec["value"], 100.0)
        assert "limitations" not in rec

    def test_over_100_percent_yield_flagged(self):
        rec = stoich.calculate_yield(actual_g=12.0, theoretical_g=10.0)
        assert rec["value"] > 100.0
        assert "limitations" in rec
        assert len(rec["limitations"]) == 1
        assert "yield>100" in rec["limitations"][0]

    def test_yield_exactly_at_100_no_flag(self):
        rec = stoich.calculate_yield(actual_g=10.0, theoretical_g=10.0)
        assert "limitations" not in rec

    def test_yield_slightly_above_100_flag(self):
        rec = stoich.calculate_yield(actual_g=10.01, theoretical_g=10.0)
        assert "limitations" in rec

    def test_yield_with_uncertainty(self):
        rec = stoich.calculate_yield(
            actual_g=8.0,
            theoretical_g=10.0,
            actual_unc=0.1,
            theoretical_unc=0.2,
        )
        assert rec["uncertainty"] > 0.0

    def test_yield_no_uncertainty_is_zero(self):
        rec = stoich.calculate_yield(actual_g=8.0, theoretical_g=10.0)
        assert rec["uncertainty"] == 0.0

    def test_zero_theoretical_raises(self):
        try:
            stoich.calculate_yield(actual_g=1.0, theoretical_g=0.0)
            raise AssertionError("expected ValueError")
        except ValueError as e:
            assert "positive" in str(e)

    def test_negative_theoretical_raises(self):
        try:
            stoich.calculate_yield(actual_g=1.0, theoretical_g=-10.0)
            raise AssertionError("expected ValueError")
        except ValueError as e:
            assert "positive" in str(e)

    def test_zero_actual_with_positive_theoretical(self):
        rec = stoich.calculate_yield(actual_g=0.0, theoretical_g=10.0)
        assert math.isclose(rec["value"], 0.0)
        assert rec["uncertainty"] == 0.0

    def test_value_record_structure(self):
        rec = stoich.calculate_yield(actual_g=5.0, theoretical_g=10.0)
        for key in ("name", "value", "unit", "uncertainty", "method_id"):
            assert key in rec, f"missing key: {key}"


# ---------------------------------------------------------------------------
# CHEM-AT-007 unit round-trip and uncertainty
# ---------------------------------------------------------------------------


class TestStoichiometryRoundTrip:
    def test_mass_moles_mass_water(self):
        rec_mol = stoich.calculate_amounts(mass_g=18.015, formula="H2O")
        assert math.isclose(rec_mol["value"], 1.0, rel_tol=0.01)
        rec_mass = stoich.calculate_amounts(moles=rec_mol["value"], formula="H2O")
        assert math.isclose(rec_mass["value"], 18.015, rel_tol=0.01)

    def test_mass_moles_mass_nacl(self):
        rec_mol = stoich.calculate_amounts(mass_g=58.44, formula="NaCl")
        assert math.isclose(rec_mol["value"], 1.0, rel_tol=0.01)
        rec_mass = stoich.calculate_amounts(moles=rec_mol["value"], formula="NaCl")
        assert math.isclose(rec_mass["value"], 58.44, rel_tol=0.01)

    def test_concentration_round_trip_moles(self):
        c = stoich.calculate_concentration(moles=3.0, volume_L=2.0)
        assert math.isclose(c["value"], 1.5)
        m = stoich.calculate_concentration(concentration=c["value"], volume_L=2.0)
        assert math.isclose(m["value"], 3.0)

    def test_concentration_round_trip_volume(self):
        c = stoich.calculate_concentration(moles=3.0, volume_L=2.0)
        v = stoich.calculate_concentration(moles=3.0, concentration=c["value"])
        assert math.isclose(v["value"], 2.0)

    def test_moles_unit_is_mol(self):
        rec = stoich.calculate_amounts(mass_g=10.0, formula="H2O")
        assert rec["unit"] == "mol"

    def test_mass_unit_is_g(self):
        rec = stoich.calculate_amounts(moles=1.0, formula="H2O")
        assert rec["unit"] == "g"

    def test_concentration_unit_is_molar(self):
        rec = stoich.calculate_concentration(moles=1.0, volume_L=1.0)
        assert rec["unit"] == "mol/L"

    def test_yield_unit_is_percent(self):
        rec = stoich.calculate_yield(actual_g=1.0, theoretical_g=2.0)
        assert rec["unit"] == "percent"
