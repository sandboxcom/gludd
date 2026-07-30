"""Unit tests for ``general_ludd.chemistry.reactions`` and ``stoichiometry`` (Phase B).

Covers CHEM-005 (reactions) and CHEM-007 (stoichiometry) from
``docs/specs/FEATURE_CHEMISTRY_EXPERT.md``:

* CHEM-AT-006 — atom/mass/charge balance; unaccounted imbalance cannot return
  ``succeeded``.
* CHEM-AT-007 — stoichiometry round-trips units and propagates uncertainty.

Modules are loaded by file path (mirroring ``test_chemistry_core.py``) so the
suite is robust to ``sys.path`` variations inside worktrees.
"""

from __future__ import annotations

import importlib.util
import math
import os

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
_CORE_PATH = os.path.join(_PROJECT_ROOT, "src", "general_ludd", "chemistry", "core.py")
_RXN_PATH = os.path.join(_PROJECT_ROOT, "src", "general_ludd", "chemistry", "reactions.py")
_STOI_PATH = os.path.join(_PROJECT_ROOT, "src", "general_ludd", "chemistry", "stoichiometry.py")


def _load_module(path: str, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None, f"{name} spec failed"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


core = _load_module(_CORE_PATH, "chemistry_core_b")
reactions = _load_module(_RXN_PATH, "chemistry_reactions_under_test")
stoichiometry = _load_module(_STOI_PATH, "chemistry_stoichiometry_under_test")


# ---------------------------------------------------------------------------
# CHEM-005 reaction reasoning — balance_reaction
# ---------------------------------------------------------------------------


class TestBalanceReaction:
    def test_simple_synthesis_balanced(self):
        result = reactions.balance_reaction({"reactants": ["H2", "O2"], "products": ["H2O"]})
        assert result["status"] == "succeeded"
        assert result["balanced"] is True
        coeffs = result["coefficients"]
        assert coeffs["reactants"]["H2"] == 2
        assert coeffs["reactants"]["O2"] == 1
        assert coeffs["products"]["H2O"] == 2

    def test_atom_balance_passes(self):
        result = reactions.balance_reaction({"reactants": ["CH4", "O2"], "products": ["CO2", "H2O"]})
        assert result["balanced"] is True
        atom_check = next(v for v in result["verification"] if v["check"] == "atom_balance")
        assert atom_check["status"] == "pass"

    def test_mass_balance_passes(self):
        result = reactions.balance_reaction({"reactants": ["NaOH", "HCl"], "products": ["NaCl", "H2O"]})
        mass_check = next(v for v in result["verification"] if v["check"] == "mass_balance")
        assert mass_check["status"] == "pass"

    def test_charge_balance_passes_for_ionic(self):
        result = reactions.balance_reaction({"reactants": ["[Na+]", "[Cl-]"], "products": ["NaCl"]})
        charge_check = next(v for v in result["verification"] if v["check"] == "charge_balance")
        assert charge_check["status"] == "pass"

    def test_unbalanced_reaction_not_succeeded(self):
        # CHEM-AT-006: unaccounted imbalance cannot return succeeded.
        result = reactions.balance_reaction({"reactants": ["H2", "O2"], "products": ["H2O2"]})
        if not result["balanced"]:
            assert result["status"] != "succeeded"
            errs = [e.get("code", "") for e in result.get("errors", [])]
            assert any("unbalanced" in c or "imbalance" in c or "atom" in c for c in errs) or errs

    def test_unbalanced_with_unaccounted_imbalance_fails(self):
        # H2 -> H2O conserves H but invents O on product side.
        result = reactions.balance_reaction({"reactants": ["H2"], "products": ["H2O"]})
        assert result["balanced"] is False
        assert result["status"] == "failed"

    def test_missing_reactants_refused(self):
        result = reactions.balance_reaction({"reactants": [], "products": ["H2O"]})
        assert result["status"] == "refused"

    def test_missing_products_refused(self):
        result = reactions.balance_reaction({"reactants": ["H2"], "products": []})
        assert result["status"] == "refused"

    def test_all_verification_checks_present(self):
        result = reactions.balance_reaction({"reactants": ["H2", "O2"], "products": ["H2O"]})
        checks = {v["check"] for v in result["verification"]}
        assert {"mass_balance", "atom_balance", "charge_balance"}.issubset(checks)


# ---------------------------------------------------------------------------
# CHEM-005 classify_reaction
# ---------------------------------------------------------------------------


class TestClassifyReaction:
    def test_synthesis_classification(self):
        # 2 Na + Cl2 -> 2 NaCl : two reactants -> one product
        kind = reactions.classify_reaction({"reactants": ["Na", "Cl2"], "products": ["NaCl"]})
        assert kind["classification"] == "synthesis"

    def test_decomposition_classification(self):
        # 2 H2O -> 2 H2 + O2 : one reactant -> multiple products
        kind = reactions.classify_reaction({"reactants": ["H2O"], "products": ["H2", "O2"]})
        assert kind["classification"] == "decomposition"

    def test_single_displacement_classification(self):
        # Zn + 2 HCl -> ZnCl2 + H2
        kind = reactions.classify_reaction({"reactants": ["Zn", "HCl"], "products": ["ZnCl2", "H2"]})
        assert kind["classification"] == "single_displacement"

    def test_acid_base_classification(self):
        # NaOH + HCl -> NaCl + H2O
        kind = reactions.classify_reaction({"reactants": ["NaOH", "HCl"], "products": ["NaCl", "H2O"]})
        assert kind["classification"] == "acid_base"

    def test_combustion_classification(self):
        # CH4 + 2 O2 -> CO2 + 2 H2O
        kind = reactions.classify_reaction({"reactants": ["CH4", "O2"], "products": ["CO2", "H2O"]})
        assert kind["classification"] == "combustion"


# ---------------------------------------------------------------------------
# CHEM-005 compare_reactions
# ---------------------------------------------------------------------------


class TestCompareReactions:
    def test_identical_reactions_similarity_one(self):
        r = {"reactants": ["H2", "O2"], "products": ["H2O"]}
        sim = reactions.compare_reactions(r, r)
        assert sim["similarity"] == 1.0
        assert sim["same_reactants"] is True
        assert sim["same_products"] is True

    def test_disjoint_reactions_similarity_zero(self):
        r1 = {"reactants": ["H2", "O2"], "products": ["H2O"]}
        r2 = {"reactants": ["Na", "Cl2"], "products": ["NaCl"]}
        sim = reactions.compare_reactions(r1, r2)
        assert sim["similarity"] == 0.0


# ---------------------------------------------------------------------------
# CHEM-007 stoichiometry — calculate_amounts
# ---------------------------------------------------------------------------


class TestCalculateAmounts:
    def test_moles_from_mass_and_formula(self):
        # 18.015 g of H2O -> ~1 mol
        rec = stoichiometry.calculate_amounts(mass_g=18.015, formula="H2O")
        assert rec["name"] == "amount_substance"
        assert rec["unit"] == "mol"
        assert math.isclose(rec["value"], 1.0, rel_tol=1e-3)

    def test_moles_uncertainty_propagated(self):
        rec = stoichiometry.calculate_amounts(mass_g=36.03, formula="H2O", mass_uncertainty=0.02)
        assert rec["uncertainty"] > 0.0

    def test_mass_from_moles_round_trip(self):
        # round-trip: start moles -> compute mass -> back to moles
        rec = stoichiometry.calculate_amounts(moles=2.0, formula="CO2")
        assert rec["unit"] == "g"
        roundtrip = stoichiometry.calculate_amounts(mass_g=rec["value"], formula="CO2")
        assert math.isclose(roundtrip["value"], 2.0, rel_tol=1e-6)

    def test_zero_mass_is_zero_moles(self):
        rec = stoichiometry.calculate_amounts(mass_g=0.0, formula="NaCl")
        assert rec["value"] == 0.0


# ---------------------------------------------------------------------------
# CHEM-007 stoichiometry — calculate_concentration
# ---------------------------------------------------------------------------


class TestCalculateConcentration:
    def test_molarity_from_moles_and_volume(self):
        rec = stoichiometry.calculate_concentration(moles=1.0, volume_L=1.0)
        assert rec["unit"] == "mol/L"
        assert math.isclose(rec["value"], 1.0)

    def test_molarity_round_trip(self):
        rec = stoichiometry.calculate_concentration(moles=0.5, volume_L=0.25)
        assert math.isclose(rec["value"], 2.0)
        back = stoichiometry.calculate_concentration(concentration=rec["value"], volume_L=0.25)
        assert math.isclose(back["value"], 0.5)
        assert back["unit"] == "mol"

    def test_molarity_uncertainty_propagated(self):
        rec = stoichiometry.calculate_concentration(
            moles=1.0, moles_uncertainty=0.01, volume_L=1.0, volume_uncertainty=0.01
        )
        assert rec["uncertainty"] > 0.0


# ---------------------------------------------------------------------------
# CHEM-007 stoichiometry — calculate_yield
# ---------------------------------------------------------------------------


class TestCalculateYield:
    def test_percent_yield_basic(self):
        rec = stoichiometry.calculate_yield(actual_g=8.0, theoretical_g=10.0)
        assert rec["unit"] == "percent"
        assert math.isclose(rec["value"], 80.0)

    def test_yield_uncertainty_propagated(self):
        rec = stoichiometry.calculate_yield(actual_g=8.0, theoretical_g=10.0, actual_unc=0.1, theoretical_unc=0.2)
        assert rec["uncertainty"] > 0.0

    def test_yield_over_100_flagged(self):
        rec = stoichiometry.calculate_yield(actual_g=12.0, theoretical_g=10.0)
        assert rec["value"] > 100.0
        assert any("yield>100" in lim or "exceeds" in lim for lim in rec.get("limitations", []))

    def test_yield_zero_theoretical_raises(self):
        try:
            stoichiometry.calculate_yield(actual_g=1.0, theoretical_g=0.0)
            raise AssertionError("expected ValueError")
        except ValueError:
            pass


# ---------------------------------------------------------------------------
# CHEM-AT-007 unit round-trip and uncertainty
# ---------------------------------------------------------------------------


class TestUnitRoundTrip:
    def test_mass_moles_mass_round_trip(self):
        for formula in ("H2O", "CO2", "NaCl", "C6H12O6"):
            mm = core.molar_mass(formula)["value"]
            mass0 = 2.5 * mm
            moles_rec = stoichiometry.calculate_amounts(mass_g=mass0, formula=formula)
            mass_rec = stoichiometry.calculate_amounts(moles=moles_rec["value"], formula=formula)
            assert math.isclose(mass_rec["value"], mass0, rel_tol=1e-6), formula

    def test_moles_unit_is_mol(self):
        rec = stoichiometry.calculate_amounts(mass_g=10.0, formula="H2O")
        assert rec["unit"] == "mol"

    def test_mass_unit_is_g(self):
        rec = stoichiometry.calculate_amounts(moles=1.0, formula="H2O")
        assert rec["unit"] == "g"

    def test_concentration_unit_is_molar(self):
        rec = stoichiometry.calculate_concentration(moles=1.0, volume_L=1.0)
        assert rec["unit"] == "mol/L"
