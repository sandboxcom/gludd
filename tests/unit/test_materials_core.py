"""Tests for the materials_engineer core module (general_ludd.materials).

Covers the top 5 user-visible roles from spec MATE-001 §3:
  requirements_capture, material_select, polymer_process_plan,
  metal_forming_plan, strength_assess.

These tests exercise property/access functions over a representative
material registry and verify the safety fail-closed behavior mandated by
MATE-SAFE-003 (no fabricated precision), MATE-SAFE-006 (fail closed), and
MATE-DEC-002 (screening and ranking).
"""

from __future__ import annotations

from general_ludd.materials.core import (
    ASSESS_FAIL_CLOSED,
    INSUFFICIENT_CONTEXT,
    INSUFFICIENT_DATA,
    MATERIAL_FAMILIES,
    ROLES,
    assess_strength,
    get_properties,
    list_material_families,
    lookup_material,
    normalize_requirements,
    plan_metal_forming,
    plan_polymer_process,
    select_materials,
)


class TestRegistries:
    def test_five_material_families_registered(self):
        assert len(MATERIAL_FAMILIES) == 5
        for fam in ("polymer", "metal", "ceramic", "composite", "textile"):
            assert fam in MATERIAL_FAMILIES, f"missing family {fam}"

    def test_five_roles_registered(self):
        assert len(ROLES) == 5
        for role in (
            "requirements_capture",
            "material_select",
            "polymer_process_plan",
            "metal_forming_plan",
            "strength_assess",
        ):
            assert role in ROLES, f"missing role {role}"

    def test_list_material_families_returns_tuple(self):
        fams = list_material_families()
        assert isinstance(fams, tuple)
        assert set(fams) == set(MATERIAL_FAMILIES)


class TestLookupMaterial:
    def test_lookup_known_material_returns_dict(self):
        mat = lookup_material("pa66_gf30")
        assert mat is not None
        assert mat["family"] == "polymer"
        assert "designation" in mat

    def test_lookup_unknown_returns_none(self):
        assert lookup_material("unobtanium") is None

    def test_lookup_case_insensitive(self):
        assert lookup_material("PA66_GF30")["material_id"] == "pa66_gf30"
        assert lookup_material("  aisi_1045 ")["material_id"] == "aisi_1045"


class TestProperties:
    def test_property_carries_units_and_uncertainty(self):
        props = get_properties("aisi_1045")
        assert len(props) >= 1
        young = next(p for p in props if p["name"] == "youngs_modulus")
        assert young["unit"] == "GPa"
        assert "uncertainty" in young
        assert young["basis"] in ("nominal", "typical", "minimum", "specimen")

    def test_property_without_condition_marked_insufficient_context(self):
        # A property missing required condition metadata SHALL be flagged.
        mat = lookup_material("abs")
        assert mat is not None
        unconditioned = [p for p in mat["properties"] if not p.get("condition")]
        for prop in unconditioned:
            assert prop.get("state") == INSUFFICIENT_CONTEXT


class TestRequirementsCapture:
    def test_normalize_captures_loads_and_environment(self):
        raw = {
            "load_cases": [{"id": "lc1", "type": "tensile", "magnitude": 250, "unit": "MPa"}],
            "environment": [{"factor": "temp", "range": "-20..80", "unit": "C"}],
            "design_life": {"value": 10, "unit": "years"},
            "failure_consequence": "significant",
        }
        norm = normalize_requirements(raw)
        assert norm["load_cases"][0]["id"] == "lc1"
        assert norm["environment"][0]["factor"] == "temp"
        assert norm["design_life"]["value"] == 10
        assert norm["failure_consequence"] == "significant"
        assert norm["schema_version"]

    def test_normalize_marks_missing_mandatory_fields_unknown(self):
        # MATE-DEC-001: ranking invalid until mandatory constraints present or
        # explicitly marked unknown.
        norm = normalize_requirements({})
        for field in ("load_cases", "environment", "design_life", "manufacturing"):
            assert norm[field] == "unknown", f"{field} not marked unknown"
        assert norm["geometry_refs"] == "unknown"

    def test_normalize_flags_safety_critical_consequence(self):
        norm = normalize_requirements({"failure_consequence": "safety_critical"})
        assert norm["failure_consequence"] == "safety_critical"
        assert norm["requires_human_review"] is True


class TestMaterialSelect:
    def test_rejects_hard_constraint_violation(self):
        # A polymer candidate cannot meet a 2000 MPa yield requirement.
        reqs = normalize_requirements(
            {
                "load_cases": [{"id": "y1", "type": "yield", "magnitude": 2000, "unit": "MPa"}],
                "failure_consequence": "significant",
            }
        )
        result = select_materials(reqs, candidates=["pa66_gf30", "abs"])
        rejected = [c for c in result["candidates"] if c["state"] == "rejected"]
        assert len(rejected) >= 1
        assert "hard_constraint" in rejected[0]["reason"]

    def test_ranks_surviving_candidates_with_margins(self):
        reqs = normalize_requirements(
            {
                "load_cases": [{"id": "y1", "type": "yield", "magnitude": 200, "unit": "MPa"}],
                "failure_consequence": "significant",
            }
        )
        result = select_materials(reqs, candidates=["pa66_gf30", "aisi_1045"])
        surviving = [c for c in result["candidates"] if c["state"] != "rejected"]
        assert len(surviving) >= 1
        for cand in surviving:
            assert "requirement_margins" in cand
            assert cand["requirement_margins"], "no margins computed"
            for margin in cand["requirement_margins"]:
                assert "margin" in margin

    def test_select_exposes_provenance_and_unknowns(self):
        reqs = normalize_requirements({"failure_consequence": "noncritical"})
        result = select_materials(reqs, candidates=["aisi_1045"])
        assert "candidates" in result
        for cand in result["candidates"]:
            assert "source" in cand
            assert "unknowns" in cand


class TestPolymerProcessPlan:
    def test_thermoplastic_injection_plan_has_process_window(self):
        plan = plan_polymer_process("abs", "injection_molding")
        assert plan["process_family"] == "injection_molding"
        assert "process_window" in plan
        assert "melt_temperature" in plan["process_window"]

    def test_thermoset_not_remeltable_flagged(self):
        # MATE-AT-003: thermosets are not planned as remeltable.
        plan = plan_polymer_process("epoxy_cast", "injection_molding")
        assert plan["compatible"] is False
        assert "remelt" in plan["reason"].lower() or "thermoset" in plan["reason"].lower()

    def test_unknown_polymer_process_returns_insufficient_data(self):
        plan = plan_polymer_process("abs", "friction_stir_welding")
        assert plan["state"] == INSUFFICIENT_DATA


class TestMetalFormingPlan:
    def test_steel_stamping_includes_springback(self):
        plan = plan_metal_forming("aisi_1045", "stamping")
        assert plan["process_family"] == "stamping"
        assert "springback" in plan
        assert isinstance(plan["springback"]["estimate_pct"], (int, float))

    def test_aluminum_forging_includes_heat_treatment_note(self):
        plan = plan_metal_forming("aa6061_t6", "forging")
        assert plan["process_family"] == "forging"
        assert "heat_treatment" in plan


class TestStrengthAssess:
    def test_static_tension_check_returns_margin(self):
        verdict = assess_strength(
            "aisi_1045",
            {"type": "tensile", "magnitude": 300, "unit": "MPa"},
        )
        assert verdict["failure_mode"] == "tensile_yield"
        assert "margin" in verdict
        assert verdict["margin"] > 0  # 1045 steel yield > 300 MPa

    def test_fail_closed_on_missing_property(self):
        # MATE-SAFE-006: missing property SHALL block a positive verdict.
        verdict = assess_strength(
            "abs",
            {"type": "fatigue", "magnitude": 50, "unit": "MPa", "cycles": 1_000_000},
        )
        assert verdict["state"] in (INSUFFICIENT_DATA, ASSESS_FAIL_CLOSED)

    def test_unit_mismatch_blocks_positive_verdict(self):
        # MATE-SAFE-006: unit mismatch SHALL block a positive verdict.
        verdict = assess_strength(
            "aisi_1045",
            {"type": "tensile", "magnitude": 300, "unit": "ksi"},
        )
        assert verdict["state"] in (INSUFFICIENT_DATA, ASSESS_FAIL_CLOSED)
