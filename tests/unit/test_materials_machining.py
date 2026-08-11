"""Deep tests for ``general_ludd.materials.machining`` (spec MATE-001 §4.5).

Covers every branch of :meth:`MachiningAdvisor.plan`:
  - Unknown process / material → ``insufficient_data`` (MATE-SAFE-006)
  - Datum scheme lookup (process-specific vs default)
  - Fixturing lookup (process-specific vs default generic)
  - Tool class hierarchy (material-specific > family > default "coated_carbide")
  - Chatter risk hierarchy (material-specific > family > default "medium")
  - Tolerance capability (process-specific vs default IT9)
  - Surface integrity (process-specific vs default)
  - Accessibility (process-specific vs default "ok")
  - Special case: aa6061_t6 + grinding → loading_of_wheel concern
  - Whitespace handling in process name
  - Module-level constant shape invariants
"""

from __future__ import annotations

import pytest

from general_ludd.materials.machining import (
    MACHINING_PROCESSES,
    MachiningAdvisor,
)

# ── Module-level constants ────────────────────────────────────────────────


class TestMachiningProcesses:
    def test_expected_processes_present(self):
        assert "milling" in MACHINING_PROCESSES
        assert "turning" in MACHINING_PROCESSES
        assert "drilling" in MACHINING_PROCESSES
        assert "grinding" in MACHINING_PROCESSES
        assert "edm" in MACHINING_PROCESSES
        assert "wire_edm" in MACHINING_PROCESSES
        assert "waterjet" in MACHINING_PROCESSES
        assert "honing" in MACHINING_PROCESSES
        assert "lapping" in MACHINING_PROCESSES
        assert "boring" in MACHINING_PROCESSES
        assert "reaming" in MACHINING_PROCESSES
        assert "broaching" in MACHINING_PROCESSES
        assert "laser_machining" in MACHINING_PROCESSES
        assert "plasma_machining" in MACHINING_PROCESSES
        assert "chemical_machining" in MACHINING_PROCESSES

    def test_is_frozenset(self):
        assert isinstance(MACHINING_PROCESSES, frozenset)


# ── Unknown process / material (MATE-SAFE-006) ────────────────────────────


class TestInsufficientData:
    def test_unknown_process_returns_insufficient_data(self):
        advisor = MachiningAdvisor()
        result = advisor.plan("aisi_1045", "laser_sintering")
        assert result["state"] == "insufficient_data"
        assert result["material_id"] == "aisi_1045"
        assert "unrecognized" in result["reason"]
        assert "laser_sintering" in result["reason"]

    def test_unknown_material_returns_insufficient_data(self):
        advisor = MachiningAdvisor()
        result = advisor.plan("unobtanium", "milling")
        assert result["state"] == "insufficient_data"
        assert result["material_id"] == "unobtanium"
        assert "unknown material" in result["reason"]

    def test_both_unknown_process_checked_first(self):
        advisor = MachiningAdvisor()
        result = advisor.plan("unobtanium", "laser_sintering")
        assert result["state"] == "insufficient_data"
        assert "unrecognized" in result["reason"]

    def test_insufficient_data_carries_schema_version(self):
        advisor = MachiningAdvisor()
        result = advisor.plan("aisi_1045", "laser_sintering")
        assert "schema_version" in result
        assert result["schema_version"].startswith("mate-001")


# ── Full plan shape ───────────────────────────────────────────────────────


class TestPlanHappyPath:
    def test_valid_material_and_process_returns_candidate_state(self):
        advisor = MachiningAdvisor()
        result = advisor.plan("aisi_1045", "milling")
        assert result["state"] == "candidate"

    def test_plan_contains_all_required_sections(self):
        advisor = MachiningAdvisor()
        result = advisor.plan("aisi_1045", "milling")
        expected_keys = {
            "schema_version",
            "material_id",
            "process",
            "state",
            "datum_scheme",
            "accessibility",
            "fixturing",
            "tool_class",
            "chatter_risk",
            "tolerance_capability",
            "surface_integrity",
        }
        assert expected_keys <= set(result.keys())

    def test_schema_version_set(self):
        advisor = MachiningAdvisor()
        result = advisor.plan("aisi_1045", "milling")
        assert result["schema_version"].startswith("mate-001")

    def test_process_is_normalized_to_lowercase(self):
        advisor = MachiningAdvisor()
        result = advisor.plan("aisi_1045", "  MiLLiNg  ")
        assert result["process"] == "milling"


# ── Datum scheme ───────────────────────────────────────────────────────────


class TestDatumScheme:
    def test_process_specific_datum_turning(self):
        advisor = MachiningAdvisor()
        result = advisor.plan("aisi_1045", "turning")
        assert result["datum_scheme"]["primary"] == "spindle_axis_face"
        assert result["datum_scheme"]["secondary"] == "chuck_jaw_datum"
        assert result["datum_scheme"]["tertiary"] == "tailstock_center"

    def test_process_specific_datum_drilling(self):
        advisor = MachiningAdvisor()
        result = advisor.plan("abs", "drilling")
        assert result["datum_scheme"]["primary"] == "perpendicular_face"
        assert result["datum_scheme"]["secondary"] == "drill_axis"

    def test_process_specific_datum_grinding(self):
        advisor = MachiningAdvisor()
        result = advisor.plan("aisi_1045", "grinding")
        assert result["datum_scheme"]["primary"] == "ground_finish_face"
        assert "heat-treated" in result["datum_scheme"]["notes"]

    def test_default_datum_for_unlisted_process(self):
        advisor = MachiningAdvisor()
        result = advisor.plan("aisi_1045", "edm")
        assert result["datum_scheme"]["primary"] == "locating_face"
        assert result["datum_scheme"]["secondary"] == "bolt_pattern_axis"
        assert result["datum_scheme"]["tertiary"] == "anti_rotation_pin"

    def test_default_datum_for_waterjet(self):
        advisor = MachiningAdvisor()
        result = advisor.plan("aisi_1045", "waterjet")
        assert result["datum_scheme"]["primary"] == "locating_face"

    def test_datum_scheme_is_a_copy_not_shared_reference(self):
        advisor = MachiningAdvisor()
        r1 = advisor.plan("aisi_1045", "milling")
        r2 = advisor.plan("aa6061_t6", "milling")
        assert r1["datum_scheme"] is not r2["datum_scheme"]


# ── Fixturing ────────────────────────────────────────────────────────────────


class TestFixturing:
    def test_milling_fixturing(self):
        advisor = MachiningAdvisor()
        result = advisor.plan("aisi_1045", "milling")
        assert result["fixturing"]["clamp_type"] == "tombstone_or_vise"
        assert result["fixturing"]["method"] == "hydraulic_vise"
        assert result["fixturing"]["rigidity"] == "high"

    def test_turning_fixturing(self):
        advisor = MachiningAdvisor()
        result = advisor.plan("aisi_1045", "turning")
        assert result["fixturing"]["clamp_type"] == "chuck"
        assert result["fixturing"]["method"] == "3_jaw_chuck"

    def test_drilling_fixturing(self):
        advisor = MachiningAdvisor()
        result = advisor.plan("aa6061_t6", "drilling")
        assert result["fixturing"]["clamp_type"] == "drill_jig"
        assert result["fixturing"]["rigidity"] == "medium"

    def test_boring_fixturing(self):
        advisor = MachiningAdvisor()
        result = advisor.plan("aisi_1045", "boring")
        assert result["fixturing"]["clamp_type"] == "boring_fixture"
        assert result["fixturing"]["method"] == "steady_rest"

    def test_grinding_fixturing(self):
        advisor = MachiningAdvisor()
        result = advisor.plan("aisi_1045", "grinding")
        assert result["fixturing"]["clamp_type"] == "magnetic_chuck"
        assert result["fixturing"]["method"] == "electromagnetic"

    def test_default_fixturing_for_unlisted_process(self):
        advisor = MachiningAdvisor()
        result = advisor.plan("aisi_1045", "edm")
        assert result["fixturing"]["clamp_type"] == "generic_vise"
        assert result["fixturing"]["method"] == "manual"
        assert result["fixturing"]["rigidity"] == "medium"

    def test_fixturing_is_a_copy(self):
        advisor = MachiningAdvisor()
        r1 = advisor.plan("aisi_1045", "milling")
        r2 = advisor.plan("aisi_1045", "milling")
        assert r1["fixturing"] is not r2["fixturing"]


# ── Tool class ─────────────────────────────────────────────────────────────


class TestToolClass:
    def test_material_specific_override_aisi_1045(self):
        advisor = MachiningAdvisor()
        result = advisor.plan("aisi_1045", "milling")
        assert result["tool_class"] == "coated_carbide"

    def test_material_specific_override_aa6061_t6(self):
        advisor = MachiningAdvisor()
        result = advisor.plan("aa6061_t6", "milling")
        assert result["tool_class"] == "polished_uncoated_carbide"

    def test_material_specific_override_pa66_gf30(self):
        advisor = MachiningAdvisor()
        result = advisor.plan("pa66_gf30", "milling")
        assert result["tool_class"] == "polycrystalline_diamond"

    def test_material_specific_override_abs(self):
        advisor = MachiningAdvisor()
        result = advisor.plan("abs", "drilling")
        assert result["tool_class"] == "high_speed_steel"

    def test_material_specific_override_epoxy_cast(self):
        advisor = MachiningAdvisor()
        result = advisor.plan("epoxy_cast", "milling")
        assert result["tool_class"] == "tungsten_carbide"

    def test_family_fallback_metal(self):
        advisor = MachiningAdvisor()
        result = advisor.plan("aisi_1045", "grinding")
        assert result["tool_class"] == "coated_carbide"

    def test_family_fallback_polymer(self):
        advisor = MachiningAdvisor()
        result = advisor.plan("abs", "milling")
        assert result["tool_class"] == "high_speed_steel"

    def test_default_tool_class_when_family_unknown(self):
        advisor = MachiningAdvisor()
        result = advisor.plan("aisi_1045", "waterjet")
        assert result["tool_class"] == "coated_carbide"


# ── Chatter risk ──────────────────────────────────────────────────────────


class TestChatterRisk:
    def test_chatter_material_specific_aa6061_t6_high(self):
        advisor = MachiningAdvisor()
        result = advisor.plan("aa6061_t6", "milling")
        assert result["chatter_risk"] == "high"

    def test_chatter_material_specific_aisi_1045_low(self):
        advisor = MachiningAdvisor()
        result = advisor.plan("aisi_1045", "milling")
        assert result["chatter_risk"] == "low"

    def test_chatter_material_specific_abs_low(self):
        advisor = MachiningAdvisor()
        result = advisor.plan("abs", "milling")
        assert result["chatter_risk"] == "low"

    def test_chatter_material_specific_pa66_gf30_medium(self):
        advisor = MachiningAdvisor()
        result = advisor.plan("pa66_gf30", "drilling")
        assert result["chatter_risk"] == "medium"

    def test_chatter_material_specific_epoxy_cast_low(self):
        advisor = MachiningAdvisor()
        result = advisor.plan("epoxy_cast", "milling")
        assert result["chatter_risk"] == "low"


# ── Tolerance capability ──────────────────────────────────────────────────


class TestToleranceCapability:
    def test_milling_tolerance_it8(self):
        advisor = MachiningAdvisor()
        result = advisor.plan("aisi_1045", "milling")
        assert result["tolerance_capability"]["it_grade"] == "IT8"
        assert result["tolerance_capability"]["band_mm"] == 0.027

    def test_turning_tolerance_it7(self):
        advisor = MachiningAdvisor()
        result = advisor.plan("aisi_1045", "turning")
        assert result["tolerance_capability"]["it_grade"] == "IT7"
        assert result["tolerance_capability"]["band_mm"] == 0.016

    def test_drilling_tolerance_it11(self):
        advisor = MachiningAdvisor()
        result = advisor.plan("aa6061_t6", "drilling")
        assert result["tolerance_capability"]["it_grade"] == "IT11"
        assert result["tolerance_capability"]["band_mm"] == 0.13

    def test_grinding_tolerance_it5(self):
        advisor = MachiningAdvisor()
        result = advisor.plan("aisi_1045", "grinding")
        assert result["tolerance_capability"]["it_grade"] == "IT5"
        assert result["tolerance_capability"]["band_mm"] == 0.006

    def test_honing_tolerance_it4(self):
        advisor = MachiningAdvisor()
        result = advisor.plan("aisi_1045", "honing")
        assert result["tolerance_capability"]["it_grade"] == "IT4"
        assert result["tolerance_capability"]["band_mm"] == 0.003

    def test_lapping_tolerance_it3(self):
        advisor = MachiningAdvisor()
        result = advisor.plan("aisi_1045", "lapping")
        assert result["tolerance_capability"]["it_grade"] == "IT3"
        assert result["tolerance_capability"]["band_mm"] == 0.001

    def test_waterjet_tolerance_it12(self):
        advisor = MachiningAdvisor()
        result = advisor.plan("aisi_1045", "waterjet")
        assert result["tolerance_capability"]["it_grade"] == "IT12"
        assert result["tolerance_capability"]["band_mm"] == 0.21

    def test_default_tolerance_for_unlisted_process(self):
        advisor = MachiningAdvisor()
        result = advisor.plan("aisi_1045", "plasma_machining")
        assert result["tolerance_capability"]["it_grade"] == "IT9"
        assert result["tolerance_capability"]["band_mm"] == 0.052


# ── Surface integrity ────────────────────────────────────────────────────


class TestSurfaceIntegrity:
    def test_milling_surface_integrity(self):
        advisor = MachiningAdvisor()
        result = advisor.plan("aisi_1045", "milling")
        assert result["surface_integrity"]["ra_um"] == 1.6
        assert "work_hardening_layer" in result["surface_integrity"]["concerns"]
        assert "residual_stress" in result["surface_integrity"]["concerns"]

    def test_turning_surface_integrity(self):
        advisor = MachiningAdvisor()
        result = advisor.plan("aisi_1045", "turning")
        assert result["surface_integrity"]["ra_um"] == 1.6
        assert "built_up_edge" in result["surface_integrity"]["concerns"]

    def test_drilling_surface_integrity(self):
        advisor = MachiningAdvisor()
        result = advisor.plan("aa6061_t6", "drilling")
        assert result["surface_integrity"]["ra_um"] == 3.2
        assert "burr_formation" in result["surface_integrity"]["concerns"]

    def test_grinding_surface_integrity(self):
        advisor = MachiningAdvisor()
        result = advisor.plan("aisi_1045", "grinding")
        assert result["surface_integrity"]["ra_um"] == 0.4
        assert "grinding_burns" in result["surface_integrity"]["concerns"]

    def test_edm_surface_integrity(self):
        advisor = MachiningAdvisor()
        result = advisor.plan("aisi_1045", "edm")
        assert result["surface_integrity"]["ra_um"] == 1.6
        assert "recast_layer" in result["surface_integrity"]["concerns"]
        assert "heat_affected_zone" in result["surface_integrity"]["concerns"]

    def test_waterjet_surface_integrity(self):
        advisor = MachiningAdvisor()
        result = advisor.plan("aisi_1045", "waterjet")
        assert result["surface_integrity"]["ra_um"] == 3.2
        assert "burr_at_exit" in result["surface_integrity"]["concerns"]
        assert "taper" in result["surface_integrity"]["concerns"]

    def test_laser_machining_surface_integrity(self):
        advisor = MachiningAdvisor()
        result = advisor.plan("aisi_1045", "laser_machining")
        assert result["surface_integrity"]["ra_um"] == 3.2
        assert "striations" in result["surface_integrity"]["concerns"]

    def test_default_surface_integrity_for_unlisted_process(self):
        advisor = MachiningAdvisor()
        result = advisor.plan("aisi_1045", "plasma_machining")
        assert result["surface_integrity"]["ra_um"] == 3.2
        assert result["surface_integrity"]["concerns"] == []
        assert result["surface_integrity"]["notes"] == ""


# ── Accessibility ─────────────────────────────────────────────────────────


class TestAccessibility:
    def test_default_accessibility_ok(self):
        advisor = MachiningAdvisor()
        result = advisor.plan("aisi_1045", "milling")
        assert result["accessibility"]["status"] == "ok"

    def test_boring_accessibility_limited(self):
        advisor = MachiningAdvisor()
        result = advisor.plan("aisi_1045", "boring")
        assert result["accessibility"]["status"] == "limited"
        assert "bore" in result["accessibility"]["notes"]

    def test_edm_accessibility_limited(self):
        advisor = MachiningAdvisor()
        result = advisor.plan("aisi_1045", "edm")
        assert result["accessibility"]["status"] == "limited"
        assert "dielectric" in result["accessibility"]["notes"]

    def test_wire_edm_accessibility_ok(self):
        advisor = MachiningAdvisor()
        result = advisor.plan("aisi_1045", "wire_edm")
        assert result["accessibility"]["status"] == "ok"
        assert "start hole" in result["accessibility"]["notes"]

    def test_honing_accessibility_limited(self):
        advisor = MachiningAdvisor()
        result = advisor.plan("aisi_1045", "honing")
        assert result["accessibility"]["status"] == "limited"
        assert "through-bore" in result["accessibility"]["notes"]

    def test_lapping_accessibility_ok(self):
        advisor = MachiningAdvisor()
        result = advisor.plan("aisi_1045", "lapping")
        assert result["accessibility"]["status"] == "ok"

    def test_drilling_accessibility_ok(self):
        advisor = MachiningAdvisor()
        result = advisor.plan("aa6061_t6", "drilling")
        assert result["accessibility"]["status"] == "ok"
        assert "cross-holes" in result["accessibility"]["notes"]

    def test_default_accessibility_for_unlisted_process(self):
        advisor = MachiningAdvisor()
        result = advisor.plan("aisi_1045", "plasma_machining")
        assert result["accessibility"]["status"] == "ok"
        assert "CAM simulation" in result["accessibility"]["notes"]


# ── Special case: aa6061_t6 + grinding ────────────────────────────────────


class TestAluminumGrindingSpecialCase:
    def test_aluminum_grinding_adds_loading_of_wheel_concern(self):
        advisor = MachiningAdvisor()
        result = advisor.plan("aa6061_t6", "grinding")
        assert "loading_of_wheel" in result["surface_integrity"]["concerns"]
        assert "silicon-carbide" in result["surface_integrity"]["notes"]

    def test_aluminum_grinding_preserves_existing_concerns(self):
        advisor = MachiningAdvisor()
        result = advisor.plan("aa6061_t6", "grinding")
        concerns = result["surface_integrity"]["concerns"]
        assert "grinding_burns" in concerns
        assert "tensile_residual_stress" in concerns
        assert "micro_cracking" in concerns
        assert "loading_of_wheel" in concerns

    def test_aluminum_milling_does_not_add_wheel_concern(self):
        advisor = MachiningAdvisor()
        result = advisor.plan("aa6061_t6", "milling")
        assert "loading_of_wheel" not in result["surface_integrity"]["concerns"]

    def test_steel_grinding_does_not_add_wheel_concern(self):
        advisor = MachiningAdvisor()
        result = advisor.plan("aisi_1045", "grinding")
        assert "loading_of_wheel" not in result["surface_integrity"]["concerns"]


# ── Cross-material consistency ───────────────────────────────────────────


class TestCrossMaterialConsistency:
    @pytest.mark.parametrize("material_id", ["aisi_1045", "aa6061_t6", "abs", "pa66_gf30", "epoxy_cast"])
    def test_all_known_materials_return_candidate_for_milling(self, material_id: str):
        advisor = MachiningAdvisor()
        result = advisor.plan(material_id, "milling")
        assert result["state"] == "candidate"

    @pytest.mark.parametrize("process", ["milling", "turning", "drilling", "grinding", "edm"])
    def test_all_known_processes_return_candidate_for_steel(self, process: str):
        advisor = MachiningAdvisor()
        result = advisor.plan("aisi_1045", process)
        assert result["state"] == "candidate"


# ── Tool class hierarchy: family fallback ─────────────────────────────────


class TestToolClassFamilyFallback:
    @pytest.mark.parametrize(
        "material_id,expected_class",
        [
            ("aisi_1045", "coated_carbide"),
            ("aa6061_t6", "polished_uncoated_carbide"),
            ("pa66_gf30", "polycrystalline_diamond"),
            ("abs", "high_speed_steel"),
            ("epoxy_cast", "tungsten_carbide"),
        ],
    )
    def test_material_specific_overrides_family(self, material_id: str, expected_class: str):
        advisor = MachiningAdvisor()
        result = advisor.plan(material_id, "turning")
        assert result["tool_class"] == expected_class


# ── Plan idempotency ────────────────────────────────────────────────────


class TestPlanIdempotency:
    def test_same_inputs_produce_same_output_keys(self):
        advisor = MachiningAdvisor()
        r1 = advisor.plan("aisi_1045", "milling")
        r2 = advisor.plan("aisi_1045", "milling")
        assert r1 == r2

    def test_different_materials_produce_different_results(self):
        advisor = MachiningAdvisor()
        r1 = advisor.plan("aisi_1045", "milling")
        r2 = advisor.plan("aa6061_t6", "milling")
        assert r1 != r2


# ── Process whitespace normalization ───────────────────────────────────


class TestProcessWhitespace:
    def test_leading_and_trailing_whitespace_stripped(self):
        advisor = MachiningAdvisor()
        result = advisor.plan("aisi_1045", "  milling  ")
        assert result["process"] == "milling"

    def test_mixed_case_normalized(self):
        advisor = MachiningAdvisor()
        result = advisor.plan("aisi_1045", "MiLLiNg")
        assert result["process"] == "milling"

    def test_uppercase_normalized(self):
        advisor = MachiningAdvisor()
        result = advisor.plan("aisi_1045", "DRILLING")
        assert result["process"] == "drilling"


# ── Reaming, broaching, chemical_machining edge processes ──────────────


class TestEdgeProcesses:
    def test_reaming(self):
        advisor = MachiningAdvisor()
        result = advisor.plan("aisi_1045", "reaming")
        assert result["state"] == "candidate"
        assert result["tolerance_capability"]["it_grade"] == "IT7"
        assert result["tolerance_capability"]["band_mm"] == 0.013

    def test_broaching(self):
        advisor = MachiningAdvisor()
        result = advisor.plan("aisi_1045", "broaching")
        assert result["state"] == "candidate"
        assert result["tolerance_capability"]["it_grade"] == "IT8"
        assert result["tolerance_capability"]["band_mm"] == 0.022

    def test_chemical_machining(self):
        advisor = MachiningAdvisor()
        result = advisor.plan("aisi_1045", "chemical_machining")
        assert result["state"] == "candidate"
        assert result["accessibility"]["status"] == "ok"


# ── Accessibility copy isolation ────────────────────────────────────────


class TestCopyIsolation:
    def test_all_sections_are_copies_not_shared_refs(self):
        advisor = MachiningAdvisor()
        r1 = advisor.plan("aisi_1045", "milling")
        r2 = advisor.plan("aisi_1045", "milling")
        for key in ("datum_scheme", "fixturing", "tolerance_capability", "surface_integrity", "accessibility"):
            assert r1[key] is not r2[key], f"{key} dicts are the same object"
