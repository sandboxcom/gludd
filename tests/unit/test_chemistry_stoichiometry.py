"""Unit tests for ``general_ludd.chemistry.stoichiometry`` (CHEM-007).

Covers calculate_amounts, calculate_concentration, calculate_yield, and
uncertainty propagation from ``docs/specs/FEATURE_CHEMISTRY_EXPERT.md`` §2/§10.
Maps to acceptance criteria CHEM-AT-007 (round-trip units + propagated uncertainty
within suite-pinned tolerance).

Module loaded by file path so the suite is robust to ``sys.path`` variations
inside worktrees.
"""

from __future__ import annotations

import importlib.util
import math
import os

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
_STOICH_PATH = os.path.join(_PROJECT_ROOT, "src", "general_ludd", "chemistry", "stoichiometry.py")


def _load_module(path: str, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None, f"{name} spec failed"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


stoich = _load_module(_STOICH_PATH, "chemistry_stoichiometry_under_test")

# Known values: H2O molar mass ~ 18.015 g/mol
_H2O = "H2O"
_H2O_MM = 15.999 + 2 * 1.008  # 18.015


# ---------------------------------------------------------------------------
# calculate_amounts — mass <-> moles
# ---------------------------------------------------------------------------


class TestCalculateAmountsMassToMoles:
    def test_h2o_mass_g_to_moles(self):
        rec = stoich.calculate_amounts(mass_g=18.015, formula=_H2O)
        assert rec["name"] == "amount_substance"
        assert rec["unit"] == "mol"
        assert math.isclose(rec["value"], 1.0, rel_tol=1e-4)

    def test_h2o_mass_g_to_moles_fractional(self):
        rec = stoich.calculate_amounts(mass_g=9.0075, formula=_H2O)
        assert math.isclose(rec["value"], 0.5, rel_tol=1e-4)

    def test_nacl_mass_g_to_moles(self):
        rec = stoich.calculate_amounts(mass_g=58.44, formula="NaCl")
        assert math.isclose(rec["value"], 1.0, rel_tol=1e-4)

    def test_mass_uncertainty_included(self):
        rec = stoich.calculate_amounts(mass_g=18.015, formula=_H2O, mass_uncertainty=0.002)
        assert "uncertainty" in rec
        assert rec["uncertainty"] > 0.0

    def test_missing_formula_raises(self):
        try:
            stoich.calculate_amounts(mass_g=10.0)
            raise AssertionError("expected ValueError for missing formula")
        except ValueError:
            pass


class TestCalculateAmountsMolesToMass:
    def test_h2o_moles_to_mass(self):
        rec = stoich.calculate_amounts(moles=1.0, formula=_H2O)
        assert rec["name"] == "mass"
        assert rec["unit"] == "g"
        assert math.isclose(rec["value"], _H2O_MM, rel_tol=1e-4)

    def test_h2o_moles_to_mass_fractional(self):
        rec = stoich.calculate_amounts(moles=0.5, formula=_H2O)
        assert math.isclose(rec["value"], _H2O_MM / 2, rel_tol=1e-4)

    def test_moles_uncertainty_included(self):
        rec = stoich.calculate_amounts(moles=1.0, formula=_H2O, moles_uncertainty=0.001)
        assert rec["uncertainty"] > 0.0

    def test_ambigous_input_raises(self):
        try:
            stoich.calculate_amounts(mass_g=10.0, moles=1.0, formula=_H2O)
            raise AssertionError("expected ValueError for ambiguous input")
        except ValueError:
            pass

    def test_neither_input_raises(self):
        try:
            stoich.calculate_amounts(formula=_H2O)
            raise AssertionError("expected ValueError for neither input")
        except ValueError:
            pass


# ---------------------------------------------------------------------------
# calculate_concentration — molarity solver
# ---------------------------------------------------------------------------


class TestCalculateConcentrationSolveConc:
    def test_solve_concentration(self):
        rec = stoich.calculate_concentration(moles=0.5, volume_L=1.0)
        assert rec["name"] == "concentration"
        assert rec["unit"] == "mol/L"
        assert math.isclose(rec["value"], 0.5, rel_tol=1e-9)

    def test_solve_concentration_uncertainty(self):
        rec = stoich.calculate_concentration(moles=0.5, volume_L=1.0, moles_uncertainty=0.001, volume_uncertainty=0.002)
        assert rec["uncertainty"] > 0.0

    def test_zero_volume_raises(self):
        try:
            stoich.calculate_concentration(moles=1.0, volume_L=0.0)
            raise AssertionError("expected ValueError for zero volume")
        except ValueError:
            pass


class TestCalculateConcentrationSolveMoles:
    def test_solve_moles(self):
        rec = stoich.calculate_concentration(volume_L=2.0, concentration=1.5)
        assert rec["name"] == "amount_substance"
        assert rec["unit"] == "mol"
        assert math.isclose(rec["value"], 3.0, rel_tol=1e-9)

    def test_solve_moles_uncertainty(self):
        rec = stoich.calculate_concentration(
            volume_L=2.0,
            concentration=1.5,
            volume_uncertainty=0.01,
            concentration_uncertainty=0.01,
        )
        assert rec["uncertainty"] > 0.0

    def test_zero_concentration_moles_raises(self):
        try:
            stoich.calculate_concentration(volume_L=1.0, concentration=0.0)
            raise AssertionError("expected ValueError for zero concentration")
        except ValueError:
            pass


class TestCalculateConcentrationSolveVolume:
    def test_solve_volume(self):
        rec = stoich.calculate_concentration(moles=2.0, concentration=0.25)
        assert rec["name"] == "volume"
        assert rec["unit"] == "L"
        assert math.isclose(rec["value"], 8.0, rel_tol=1e-9)

    def test_solve_volume_uncertainty(self):
        rec = stoich.calculate_concentration(
            moles=2.0,
            concentration=0.25,
            moles_uncertainty=0.001,
            concentration_uncertainty=0.001,
        )
        assert rec["uncertainty"] > 0.0

    def test_zero_concentration_volume_raises(self):
        try:
            stoich.calculate_concentration(moles=1.0, concentration=0.0)
            raise AssertionError("expected ValueError for zero concentration")
        except ValueError:
            pass


class TestCalculateConcentrationEdgeCases:
    def test_one_input_raises(self):
        try:
            stoich.calculate_concentration(moles=1.0)
            raise AssertionError("expected ValueError for single input")
        except ValueError:
            pass

    def test_three_inputs_raises(self):
        try:
            stoich.calculate_concentration(moles=1.0, volume_L=1.0, concentration=1.0)
            raise AssertionError("expected ValueError for three inputs")
        except ValueError:
            pass

    def test_zero_inputs_raises(self):
        try:
            stoich.calculate_concentration()
            raise AssertionError("expected ValueError for zero inputs")
        except ValueError:
            pass


# ---------------------------------------------------------------------------
# calculate_yield — percent yield
# ---------------------------------------------------------------------------


class TestCalculateYield:
    def test_normal_yield(self):
        rec = stoich.calculate_yield(actual_g=9.0, theoretical_g=10.0)
        assert rec["name"] == "yield"
        assert rec["unit"] == "percent"
        assert math.isclose(rec["value"], 90.0, rel_tol=1e-9)

    def test_perfect_yield(self):
        rec = stoich.calculate_yield(actual_g=10.0, theoretical_g=10.0)
        assert math.isclose(rec["value"], 100.0, rel_tol=1e-9)

    def test_yield_above_100_flagged(self):
        rec = stoich.calculate_yield(actual_g=10.5, theoretical_g=10.0)
        assert rec["value"] > 100.0
        assert "limitations" in rec
        assert len(rec["limitations"]) >= 1
        assert "yield>100" in str(rec["limitations"])

    def test_yield_below_100_no_limitation_flag(self):
        rec = stoich.calculate_yield(actual_g=9.5, theoretical_g=10.0)
        assert "limitations" not in rec or len(rec["limitations"]) == 0

    def test_yield_uncertainty_propagated(self):
        rec = stoich.calculate_yield(actual_g=9.0, theoretical_g=10.0, actual_unc=0.05, theoretical_unc=0.05)
        assert rec["uncertainty"] > 0.0

    def test_zero_actual_yield(self):
        rec = stoich.calculate_yield(actual_g=0.0, theoretical_g=10.0)
        assert math.isclose(rec["value"], 0.0, rel_tol=1e-9)

    def test_negative_actual_yield(self):
        rec = stoich.calculate_yield(actual_g=-1.0, theoretical_g=10.0)
        assert rec["value"] < 0.0

    def test_zero_theoretical_raises(self):
        try:
            stoich.calculate_yield(actual_g=5.0, theoretical_g=0.0)
            raise AssertionError("expected ValueError for zero theoretical")
        except ValueError:
            pass

    def test_negative_theoretical_raises(self):
        try:
            stoich.calculate_yield(actual_g=5.0, theoretical_g=-1.0)
            raise AssertionError("expected ValueError for negative theoretical")
        except ValueError:
            pass


# ---------------------------------------------------------------------------
# _propagate_relative — uncertainty helper
# ---------------------------------------------------------------------------


class TestPropagateRelative:
    def test_no_uncertainties_returns_zero(self):
        result = stoich._propagate_relative(100.0)
        assert result == 0.0

    def test_single_relative(self):
        result = stoich._propagate_relative(100.0, 0.01)
        assert math.isclose(result, 1.0, rel_tol=1e-9)

    def test_two_relatives_sqrt_sum_squares(self):
        result = stoich._propagate_relative(100.0, 0.03, 0.04)
        expected = 100.0 * math.sqrt(0.03**2 + 0.04**2)  # 5.0
        assert math.isclose(result, expected, rel_tol=1e-9)

    def test_mixed_zero_and_nonzero(self):
        result = stoich._propagate_relative(50.0, 0.0, 0.02, 0.0)
        assert math.isclose(result, 1.0, rel_tol=1e-9)
