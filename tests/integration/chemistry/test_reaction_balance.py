"""Integration tests: reaction balance -> conservation -> stoichiometry.

Exercises CHEM-AT-006 (atom/mass/charge balance; unaccounted imbalance cannot
return ``succeeded``) and CHEM-AT-007 (stoichiometry unit round-trip +
uncertainty) from ``docs/specs/FEATURE_CHEMISTRY_EXPERT.md`` §15, composed
end-to-end: a balanced reaction's coefficients feed into stoichiometric amount
and yield calculations.
"""

from __future__ import annotations

import math

from general_ludd.chemistry.core import molar_mass
from general_ludd.chemistry.reactions import balance_reaction, classify_reaction
from general_ludd.chemistry.stoichiometry import (
    calculate_amounts,
    calculate_yield,
)


class TestUnbalancedReactionCannotSucceed:
    """CHEM-AT-006: an unaccounted imbalance cannot return succeeded."""

    def test_h2_to_h2o_invents_oxygen_and_fails(self):
        # H2 -> H2O conserves H but invents O on the product side.
        result = balance_reaction({"reactants": ["H2"], "products": ["H2O"]})
        assert result["balanced"] is False
        assert result["status"] == "failed"
        codes = [e.get("code", "") for e in result.get("errors", [])]
        assert any("imbalance" in c or "unbalanced" in c or "atom" in c for c in codes)

    def test_all_three_verification_checks_present(self):
        result = balance_reaction({"reactants": ["H2", "O2"], "products": ["H2O"]})
        checks = {v["check"] for v in result["verification"]}
        assert {"mass_balance", "atom_balance", "charge_balance"}.issubset(checks)


class TestBalancedReactionConservation:
    """Atom, mass, and charge conservation hold for a balanced reaction."""

    def test_water_formation_balances_atoms_mass_charge(self):
        result = balance_reaction({"reactants": ["H2", "O2"], "products": ["H2O"]})
        assert result["status"] == "succeeded"
        assert result["balanced"] is True
        coeffs = result["coefficients"]
        assert coeffs["reactants"]["H2"] == 2
        assert coeffs["reactants"]["O2"] == 1
        assert coeffs["products"]["H2O"] == 2
        for check in result["verification"]:
            assert check["status"] == "pass"

    def test_combustion_balances_and_classifies(self):
        result = balance_reaction({"reactants": ["CH4", "O2"], "products": ["CO2", "H2O"]})
        assert result["balanced"] is True
        kind = classify_reaction({"reactants": ["CH4", "O2"], "products": ["CO2", "H2O"]})
        assert kind["classification"] == "combustion"
        atom = next(v for v in result["verification"] if v["check"] == "atom_balance")
        assert atom["status"] == "pass"


class TestStoichiometryFromBalancedReaction:
    """Stoichiometric calculations compose with balanced coefficients."""

    def test_water_formation_mass_from_balanced_coefficients(self):
        # 2 H2 + O2 -> 2 H2O. Start with 2 mol H2 -> how much H2O?
        result = balance_reaction({"reactants": ["H2", "O2"], "products": ["H2O"]})
        assert result["balanced"] is True
        h2_coeff = result["coefficients"]["reactants"]["H2"]
        h2o_coeff = result["coefficients"]["products"]["H2O"]
        moles_h2o = (2.0 / h2_coeff) * h2o_coeff
        mass_rec = calculate_amounts(moles=moles_h2o, formula="H2O")
        assert mass_rec["unit"] == "g"
        mm = molar_mass("H2O")["value"]
        assert math.isclose(mass_rec["value"], 2.0 * mm, rel_tol=1e-6)

    def test_balanced_reaction_round_trips_mass_conservation(self):
        result = balance_reaction({"reactants": ["NaOH", "HCl"], "products": ["NaCl", "H2O"]})
        assert result["balanced"] is True
        coeffs_r = result["coefficients"]["reactants"]
        coeffs_p = result["coefficients"]["products"]
        mass_r = sum(molar_mass(f)["value"] * c for f, c in coeffs_r.items())
        mass_p = sum(molar_mass(f)["value"] * c for f, c in coeffs_p.items())
        assert math.isclose(mass_r, mass_p, rel_tol=1e-6)

    def test_yield_from_stoichiometric_limit(self):
        # NaOH + HCl -> NaCl + H2O. 1 mol NaOH -> theoretical 1 mol NaCl.
        result = balance_reaction({"reactants": ["NaOH", "HCl"], "products": ["NaCl", "H2O"]})
        assert result["balanced"] is True
        nacl_coeff = result["coefficients"]["products"]["NaCl"]
        naoh_coeff = result["coefficients"]["reactants"]["NaOH"]
        theoretical_mol = (1.0 / naoh_coeff) * nacl_coeff
        theoretical_mass = calculate_amounts(moles=theoretical_mol, formula="NaCl")["value"]
        actual_mass = calculate_amounts(moles=0.95, formula="NaCl")["value"]
        yld = calculate_yield(actual_g=actual_mass, theoretical_g=theoretical_mass)
        assert yld["unit"] == "percent"
        assert math.isclose(yld["value"], 95.0, rel_tol=1e-3)
