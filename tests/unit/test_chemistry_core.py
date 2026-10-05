"""Unit tests for ``general_ludd.chemistry.core``.

Covers the top 5 capabilities from
``docs/specs/FEATURE_CHEMISTRY_EXPERT.md`` section 2:

* CHEM-001 Expert router
* CHEM-002 Chemical identity (preserves stereo / isotope / salt / mixture)
* CHEM-005 Reaction reasoning (atom / mass / charge balance)
* CHEM-007 Stoichiometry (units, molar mass, yield, uncertainty)
* CHEM-008 Safety and compatibility (risk tier, incompatibilities)

The module is imported through its installed package path so coverage and
runtime import behavior match the application boundary.
"""

from __future__ import annotations

import math

from general_ludd.chemistry import core

# ---------------------------------------------------------------------------
# CHEM-001 Expert router
# ---------------------------------------------------------------------------


class TestRouter:
    def test_routes_identity_task(self):
        decision = core.route_chemistry_task({"task": "identity"})
        assert decision["capability"] == "identity_resolve"
        assert decision["status"] in {"succeeded", "degraded"}

    def test_routes_reaction_task(self):
        decision = core.route_chemistry_task({"task": "reaction"})
        assert decision["capability"] == "reaction_analyze"

    def test_routes_stoichiometry_task(self):
        decision = core.route_chemistry_task({"task": "stoichiometry"})
        assert decision["capability"] == "stoichiometry"

    def test_routes_hazard_task(self):
        decision = core.route_chemistry_task({"task": "hazard"})
        assert decision["capability"] == "hazard_review"

    def test_high_risk_entity_requires_hazard_gate(self):
        decision = core.route_chemistry_task({"task": "reaction", "entities": [{"query": "picric acid"}]})
        assert decision["requires_hazard_review"] is True
        assert decision["risk_tier"] in {"high", "prohibited"}

    def test_unknown_task_is_refused(self):
        decision = core.route_chemistry_task({"task": "dark_chemistry"})
        assert decision["status"] == "refused"
        assert decision["errors"]

    def test_missing_task_is_refused(self):
        decision = core.route_chemistry_task({})
        assert decision["status"] == "refused"


# ---------------------------------------------------------------------------
# CHEM-002 Chemical identity
# ---------------------------------------------------------------------------


class TestIdentity:
    def test_resolves_common_name_water(self):
        entity = core.resolve_identity({"query": "water"})
        assert entity["names"][0]["value"].lower() == "water"
        assert entity["structure"]["representation"] in {"smiles", "composition"}
        assert entity["validation"][0]["status"] == "pass"

    def test_preserves_stereochemistry_marker(self):
        entity = core.resolve_identity({"query": "C[C@H](N)C(=O)O"})
        assert entity["structure"]["stereochemistry"] == "specified"
        assert "@" in entity["structure"]["value"]

    def test_detects_isotope_marker(self):
        entity = core.resolve_identity({"query": "[13C]"})
        assert entity["structure"]["isotopes"] == "specified"

    def test_detects_salt_mixture_from_dot_disconnection(self):
        entity = core.resolve_identity({"query": "[Na+].[Cl-]"})
        assert entity["kind"] == "mixture"
        assert len(entity["components"]) >= 2

    def test_smiles_charge_is_captured(self):
        entity = core.resolve_identity({"query": "[NH4+]"})
        assert entity["structure"]["charge"] == 1

    def test_unknown_query_returns_warning_validation(self):
        entity = core.resolve_identity({"query": "totally-not-a-chemical-xyzzy"})
        statuses = {v["status"] for v in entity["validation"]}
        assert "warning" in statuses or "fail" in statuses


# ---------------------------------------------------------------------------
# CHEM-005 Reaction reasoning
# ---------------------------------------------------------------------------


class TestFormulaParse:
    def test_parses_simple_water(self):
        assert core.parse_formula("H2O") == {"H": 2, "O": 1}

    def test_parses_no_count_means_one(self):
        assert core.parse_formula("NaCl") == {"Na": 1, "Cl": 1}

    def test_parses_paren_groups(self):
        assert core.parse_formula("Ca(OH)2") == {"Ca": 1, "O": 2, "H": 2}

    def test_parses_nested_parens(self):
        assert core.parse_formula("(NH4)2SO4") == {"N": 2, "H": 8, "S": 1, "O": 4}

    def test_rejects_empty_formula(self):
        try:
            core.parse_formula("")
            raised = False
        except (ValueError, KeyError):
            raised = True
        assert raised


class TestReactions:
    def test_balances_combustion_of_hydrogen(self):
        result = core.analyze_reaction({"reactants": ["H2", "O2"], "products": ["H2O"]})
        assert result["balanced"] is True
        assert result["coefficients"]["reactants"]["H2"] == 2
        assert result["coefficients"]["products"]["H2O"] == 2

    def test_detects_imbalanced_reaction(self):
        result = core.analyze_reaction({"reactants": ["Na"], "products": ["Cl2"]})
        assert result["balanced"] is False
        assert any(v["status"] == "fail" for v in result["verification"])

    def test_mass_balance_passes_for_balanced_reaction(self):
        result = core.analyze_reaction({"reactants": ["CH4", "O2"], "products": ["CO2", "H2O"]})
        mass_check = next(v for v in result["verification"] if v["check"] == "mass_balance")
        assert mass_check["status"] == "pass"

    def test_charge_balance_flagged(self):
        result = core.analyze_reaction({"reactants": ["NaCl"], "products": ["Na+", "Cl-"]})
        # net charge on both sides must match; we model formulas as net charge 0
        # unless explicit ions are present.
        assert "charge_balance" in {v["check"] for v in result["verification"]}


# ---------------------------------------------------------------------------
# CHEM-007 Stoichiometry
# ---------------------------------------------------------------------------


class TestStoichiometry:
    def test_molar_mass_water(self):
        mm = core.molar_mass("H2O")
        assert math.isclose(mm["value"], 18.015, abs_tol=0.05)
        assert mm["unit"] == "g/mol"

    def test_molar_mass_glucose(self):
        mm = core.molar_mass("C6H12O6")
        assert math.isclose(mm["value"], 180.156, abs_tol=0.5)

    def test_moles_from_mass(self):
        result = core.stoichiometry_moles(mass_g=18.015, formula="H2O")
        assert math.isclose(result["value"], 1.0, abs_tol=1e-3)
        assert result["unit"] == "mol"

    def test_dilution_solves_for_final_volume(self):
        result = core.stoichiometry_dilution(c1=1.0, v1=0.5, c2=0.1, v2=None)
        assert math.isclose(result["v2"], 5.0, abs_tol=1e-6)

    def test_yield_percent(self):
        result = core.stoichiometry_yield(actual_g=8.0, theoretical_g=10.0)
        assert math.isclose(result["value"], 80.0, abs_tol=1e-6)
        assert result["unit"] == "percent"

    def test_uncertainty_propagates_through_multiplication(self):
        result = core.stoichiometry_yield(
            actual_g=8.0,
            theoretical_g=10.0,
            actual_unc=0.2,
            theoretical_unc=0.1,
        )
        assert result["uncertainty"] > 0
        # z = x/y; relative uncertainty combines in quadrature
        rel = math.sqrt((0.2 / 8.0) ** 2 + (0.1 / 10.0) ** 2)
        assert math.isclose(result["uncertainty"], 80.0 * rel, rel_tol=1e-3)


# ---------------------------------------------------------------------------
# CHEM-008 Safety and compatibility
# ---------------------------------------------------------------------------


class TestSafety:
    def test_water_is_low_risk(self):
        result = core.screen_hazards({"query": "water"})
        assert result["risk_tier"] == "low"

    def test_strong_acid_is_at_least_moderate(self):
        result = core.screen_hazards({"query": "H2SO4"})
        assert result["risk_tier"] in {"moderate", "high"}

    def test_explosophore_is_high_or_prohibited(self):
        result = core.screen_hazards({"query": "picric acid"})
        assert result["risk_tier"] in {"high", "prohibited"}
        assert "explosive" in result["hazard_classes"]

    def test_incompatible_pair_is_flagged(self):
        result = core.screen_hazards({"entities": ["acetone", "hydrogen peroxide"]})
        assert result["risk_tier"] in {"high", "prohibited"}
        assert result["incompatibilities"]

    def test_review_id_present(self):
        result = core.screen_hazards({"query": "ethanol"})
        assert "review_id" in result["safety"]
        assert result["safety"]["risk_tier"] == result["risk_tier"]

    def test_prohibited_request_records_policy_decision(self):
        result = core.screen_hazards({"query": "nitroglycerin", "facility_controls": []})
        assert result["risk_tier"] == "prohibited"
        assert (
            any("policy" in note.lower() for note in result.get("limitations", []))
            or result["safety"]["approvals"] == []
        )


# ---------------------------------------------------------------------------
# Import sanity
# ---------------------------------------------------------------------------


class TestImportSanity:
    def test_module_exports_required_symbols(self):
        for symbol in (
            "route_chemistry_task",
            "resolve_identity",
            "analyze_reaction",
            "parse_formula",
            "molar_mass",
            "stoichiometry_moles",
            "stoichiometry_dilution",
            "stoichiometry_yield",
            "screen_hazards",
        ):
            assert hasattr(core, symbol), f"missing public symbol: {symbol}"
