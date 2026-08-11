"""Deep edge-case tests for the materials_engineer core module.

Covers safety invariants (MATE-SAFE-003, MATE-SAFE-006), fail-closed
behavior on degenerate inputs, cross-cutting key-structure checks, and
boundary conditions the happy-path tests in test_materials_core.py skip.
"""

from __future__ import annotations

import pytest

from general_ludd.materials.core import (
    ASSESS_FAIL_CLOSED,
    INSUFFICIENT_DATA,
    MATERIALS,
    METAL_FORMING_OPS,
    POLYMER_PROCESSES,
    SCHEMA_VERSION,
    assess_strength,
    get_properties,
    list_material_families,
    lookup_material,
    normalize_requirements,
    plan_metal_forming,
    plan_polymer_process,
    select_materials,
)

# ── lookup_material deep ───────────────────────────────────────────────────


class TestLookupMaterialDeep:
    def test_empty_string_returns_none(self):
        assert lookup_material("") is None

    def test_whitespace_only_returns_none(self):
        assert lookup_material("   ") is None

    def test_none_like_string_not_matched(self):
        assert lookup_material("None") is None

    def test_alias_with_mixed_spacing(self):
        m = lookup_material("  Nylon 66 GF30 ")
        assert m is not None
        assert m["material_id"] == "pa66_gf30"

    def test_alias_case_insensitive_with_leading_trailing_whitespace(self):
        m = lookup_material("  c45  ")
        assert m is not None
        assert m["material_id"] == "aisi_1045"

    def test_partial_substring_does_not_match(self):
        assert lookup_material("aisi") is None

    def test_material_id_with_underscore_remains_exact(self):
        m = lookup_material("aa6061_t6")
        assert m is not None
        assert m["material_id"] == "aa6061_t6"


# ── get_properties deep ────────────────────────────────────────────────────


class TestGetPropertiesDeep:
    def test_unknown_material_returns_empty(self):
        assert get_properties("nonexistent") == []

    def test_empty_string_returns_empty(self):
        assert get_properties("") == []

    def test_lookup_via_alias_returns_same_properties(self):
        by_id = get_properties("pa66_gf30")
        by_alias = get_properties("PA66-GF30")
        assert len(by_id) == len(by_alias)

    def test_all_registered_materials_have_at_least_one_property(self):
        for mid in MATERIALS:
            props = get_properties(mid)
            assert len(props) >= 1, f"{mid} has no properties"

    def test_property_shape_has_required_keys(self):
        for mid in MATERIALS:
            for prop in get_properties(mid):
                for key in ("name", "value_or_range", "unit", "basis", "method", "uncertainty"):
                    assert key in prop, f"{mid}.{prop.get('name', '?')} missing {key}"


# ── normalize_requirements deep ────────────────────────────────────────────


class TestNormalizeRequirementsDeep:
    def test_all_unknown_defaults_on_empty_dict(self):
        norm = normalize_requirements({})
        unknownable = (
            "geometry_refs",
            "load_cases",
            "environment",
            "design_life",
            "manufacturing",
            "interfaces",
            "tolerances",
            "inspection",
            "cost_sustainability",
        )
        for field in unknownable:
            assert norm[field] == "unknown", f"{field} should default to unknown"

    def test_schema_version_always_set(self):
        assert normalize_requirements({})["schema_version"] == SCHEMA_VERSION

    def test_non_safety_consequence_does_not_force_review(self):
        for fc in ("noncritical", "significant", "unknown"):
            norm = normalize_requirements({"failure_consequence": fc})
            assert norm["requires_human_review"] is False, f"fc={fc}"

    def test_safety_critical_forces_review(self):
        norm = normalize_requirements({"failure_consequence": "safety_critical"})
        assert norm["requires_human_review"] is True

    def test_assumptions_default_to_empty_list(self):
        norm = normalize_requirements({})
        assert norm["assumptions"] == []

    def test_passed_assumptions_are_preserved(self):
        norm = normalize_requirements({"assumptions": [{"id": "a1", "text": "conservative"}]})
        assert len(norm["assumptions"]) == 1
        assert norm["assumptions"][0]["id"] == "a1"

    def test_explicit_fields_survive_round_trip(self):
        raw = {
            "geometry_refs": "cad_v2",
            "load_cases": [{"type": "tensile", "magnitude": 100, "unit": "MPa"}],
            "environment": [{"factor": "corrosion"}],
            "design_life": {"value": 5, "unit": "years"},
            "manufacturing": "cnc_only",
            "failure_consequence": "significant",
        }
        norm = normalize_requirements(raw)
        assert norm["geometry_refs"] == "cad_v2"
        assert norm["load_cases"] == raw["load_cases"]
        assert norm["environment"] == raw["environment"]
        assert norm["design_life"] == raw["design_life"]
        assert norm["manufacturing"] == "cnc_only"


# ── select_materials deep ──────────────────────────────────────────────────


class TestSelectMaterialsDeep:
    def test_empty_candidate_list_all_defaults(self):
        result = select_materials({"load_cases": []})
        assert len(result["candidates"]) == len(MATERIALS)
        assert result["verdict"] == "candidate"

    def test_all_unknown_materials_rejected(self):
        result = select_materials({"load_cases": []}, candidates=["a", "b", "c"])
        assert all(c["state"] == "rejected" for c in result["candidates"])
        assert result["verdict"] == "infeasible"

    def test_verdict_infeasible_when_no_survivors(self):
        reqs = {"load_cases": [{"type": "yield", "magnitude": 9999, "unit": "MPa"}]}
        result = select_materials(reqs, candidates=["abs", "pa66_gf30"])
        assert result["verdict"] == "infeasible"

    def test_verdict_candidate_when_at_least_one_survivor(self):
        reqs = {"load_cases": [{"type": "yield", "magnitude": 30, "unit": "MPa"}]}
        result = select_materials(reqs, candidates=["abs", "aisi_1045"])
        assert result["verdict"] == "candidate"

    def test_no_load_cases_all_survive(self):
        result = select_materials({"load_cases": []}, candidates=["abs", "aisi_1045"])
        assert all(c["state"] == "survived" for c in result["candidates"])

    def test_load_cases_as_string_not_list_no_crashing(self):
        result = select_materials({"load_cases": "unknown"}, candidates=["abs"])
        assert len(result["candidates"]) == 1

    def test_no_magnitude_key_in_load_case(self):
        try:
            select_materials(
                {"load_cases": [{"type": "yield", "unit": "MPa", "id": "lc1"}]},
                candidates=["aisi_1045"],
            )
            raised = False
        except KeyError:
            raised = True
        assert raised, "missing magnitude key must raise KeyError"

    def test_every_survivor_has_designation_and_family(self):
        result = select_materials(
            {"load_cases": [{"type": "yield", "magnitude": 30, "unit": "MPa"}]},
            candidates=["aisi_1045", "aa6061_t6"],
        )
        for c in result["candidates"]:
            assert "designation" in c
            assert "family" in c

    def test_every_rejected_has_reason(self):
        result = select_materials(
            {"load_cases": [{"type": "yield", "magnitude": 9999, "unit": "MPa"}]},
            candidates=["abs"],
        )
        assert result["candidates"][0]["state"] == "rejected"
        assert result["candidates"][0]["reason"]

    def test_schema_version_present(self):
        result = select_materials({"load_cases": []})
        assert result["schema_version"] == SCHEMA_VERSION


# ── plan_polymer_process deep ──────────────────────────────────────────────


class TestPlanPolymerProcessDeep:
    def test_unknown_material_returns_insufficient_data(self):
        plan = plan_polymer_process("nonexistent", "injection_molding")
        assert plan["state"] == INSUFFICIENT_DATA

    def test_metal_material_returns_insufficient_data(self):
        plan = plan_polymer_process("aisi_1045", "injection_molding")
        assert plan["state"] == INSUFFICIENT_DATA
        assert "not a polymer" in plan["reason"].lower()

    def test_thermoplastic_processes_are_a_subset_of_all(self):
        for tp in ("injection_molding", "extrusion", "blow_molding", "thermoforming"):
            assert tp in POLYMER_PROCESSES

    def test_every_polymer_process_returns_recognized_result(self):
        for proc in POLYMER_PROCESSES:
            plan = plan_polymer_process("abs", proc)
            assert plan["state"] in (INSUFFICIENT_DATA, "candidate", "rejected")

    def test_drying_note_present_for_thermoplastic(self):
        plan = plan_polymer_process("abs", "injection_molding")
        assert plan.get("drying_note")

    def test_drying_note_empty_for_thermoset(self):
        plan = plan_polymer_process("epoxy_cast", "compression_molding")
        assert plan.get("drying_note") == ""

    def test_thermoset_with_non_remelt_process_is_candidate(self):
        plan = plan_polymer_process("epoxy_cast", "compression_molding")
        assert plan["state"] == "candidate"
        assert plan["compatible"] is True

    def test_generic_window_fallback_when_no_specific_window(self):
        plan = plan_polymer_process("abs", "extrusion")
        assert "melt_temperature" in plan.get("process_window", {})


# ── plan_metal_forming deep ────────────────────────────────────────────────


class TestPlanMetalFormingDeep:
    def test_unknown_material_returns_insufficient_data(self):
        plan = plan_metal_forming("nonexistent", "stamping")
        assert plan["state"] == INSUFFICIENT_DATA

    def test_polymer_material_returns_insufficient_data(self):
        plan = plan_metal_forming("abs", "stamping")
        assert plan["state"] == INSUFFICIENT_DATA
        assert "not a metal" in plan["reason"].lower()

    def test_unknown_operation_returns_insufficient_data(self):
        plan = plan_metal_forming("aisi_1045", "laser_cutting")
        assert plan["state"] == INSUFFICIENT_DATA

    def test_every_metal_forming_op_returns_valid_state(self):
        for op in METAL_FORMING_OPS:
            plan = plan_metal_forming("aisi_1045", op)
            assert plan["state"] in (INSUFFICIENT_DATA, "candidate")

    def test_springback_pct_is_numeric(self):
        plan = plan_metal_forming("aa6061_t6", "bending")
        assert isinstance(plan["springback"]["estimate_pct"], (int, float))

    def test_stock_form_based_on_operation(self):
        plan = plan_metal_forming("aisi_1045", "stamping")
        assert plan["stock_form"] == "bar"
        plan2 = plan_metal_forming("aisi_1045", "bending")
        assert plan2["stock_form"] == "sheet"

    def test_heat_treatment_has_required_flag(self):
        plan = plan_metal_forming("aa6061_t6", "forging")
        assert "heat_treatment" in plan
        assert isinstance(plan["heat_treatment"]["required"], bool)


# ── assess_strength deep ───────────────────────────────────────────────────


class TestAssessStrengthDeep:
    def test_unknown_material_returns_insufficient_data(self):
        v = assess_strength("nonexistent", {"type": "tensile", "magnitude": 100, "unit": "MPa"})
        assert v["state"] == INSUFFICIENT_DATA
        assert "unknown" in v["reason"].lower()

    def test_unsupported_load_type_returns_insufficient_data(self):
        v = assess_strength("aisi_1045", {"type": "creep", "magnitude": 100, "unit": "MPa"})
        assert v["state"] == INSUFFICIENT_DATA

    def test_missing_magnitude_returns_insufficient_data(self):
        v = assess_strength("aisi_1045", {"type": "tensile", "unit": "MPa"})
        assert v["state"] == INSUFFICIENT_DATA

    def test_zero_magnitude_returns_insufficient_data(self):
        v = assess_strength("aisi_1045", {"type": "tensile", "magnitude": 0.0, "unit": "MPa"})
        assert v["state"] == INSUFFICIENT_DATA

    def test_negative_magnitude_returns_fail(self):
        v = assess_strength("aisi_1045", {"type": "tensile", "magnitude": -10.0, "unit": "MPa"})
        assert v["state"] == "fail"

    def test_unit_mismatch_fail_closed(self):
        v = assess_strength("aisi_1045", {"type": "tensile", "magnitude": 300, "unit": "GPa"})
        assert v["state"] == ASSESS_FAIL_CLOSED

    def test_missing_type_returns_insufficient_data(self):
        v = assess_strength("aisi_1045", {"magnitude": 300, "unit": "MPa"})
        assert v["state"] == INSUFFICIENT_DATA

    def test_compression_load_type_mapped(self):
        v = assess_strength("aisi_1045", {"type": "compression", "magnitude": 300, "unit": "MPa"})
        assert v["state"] == INSUFFICIENT_DATA

    def test_shear_load_type_mapped(self):
        v = assess_strength("aisi_1045", {"type": "shear", "magnitude": 300, "unit": "MPa"})
        assert v["state"] == INSUFFICIENT_DATA

    def test_ultimate_load_type_mapped(self):
        v = assess_strength("aisi_1045", {"type": "ultimate", "magnitude": 400, "unit": "MPa"})
        assert v["state"] in ("pass", "fail")

    def test_pass_verdict_includes_capacity_and_applied(self):
        v = assess_strength("aisi_1045", {"type": "yield", "magnitude": 200, "unit": "MPa"})
        assert v["state"] == "pass"
        assert v["capacity"] > v["applied"]

    def test_fail_verdict_when_applied_exceeds_capacity(self):
        v = assess_strength("aa6061_t6", {"type": "yield", "magnitude": 500, "unit": "MPa"})
        assert v["state"] == "fail"

    def test_margin_computed_correctly(self):
        v = assess_strength("aisi_1045", {"type": "yield", "magnitude": 100, "unit": "MPa"})
        expected = (310.0 - 100.0) / 100.0
        assert v["margin"] == pytest.approx(expected)

    def test_failure_mode_named_for_tensile(self):
        v = assess_strength("aisi_1045", {"type": "tensile", "magnitude": 100, "unit": "MPa"})
        assert v["failure_mode"] == "tensile_yield"

    def test_uncertainty_present_in_verdict(self):
        v = assess_strength("aisi_1045", {"type": "yield", "magnitude": 200, "unit": "MPa"})
        assert "uncertainty" in v
        assert isinstance(v["uncertainty"], (int, float))


# ── cross-cutting: all registered materials are well-formed ────────────────


class TestMaterialRegistryIntegrity:
    def test_every_material_has_material_id_key(self):
        for mid, mat in MATERIALS.items():
            assert mat["material_id"] == mid

    def test_every_material_has_family_in_registered_set(self):
        from general_ludd.materials.core import MATERIAL_FAMILIES

        for mat in MATERIALS.values():
            assert mat["family"] in MATERIAL_FAMILIES

    def test_every_material_has_source(self):
        for mat in MATERIALS.values():
            assert isinstance(mat["source"], dict)
            assert mat["source"]

    def test_every_material_has_non_empty_designation(self):
        for mat in MATERIALS.values():
            assert mat["designation"]

    def test_list_material_families_matches_registry(self):
        families_in_data = {mat["family"] for mat in MATERIALS.values()}
        registered = set(list_material_families())
        assert families_in_data.issubset(registered)
