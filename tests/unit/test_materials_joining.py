"""Tests for the joining/welding advisor (spec MATE-001 §4.4) and the machining
advisor (spec MATE-001 §4.5).

Covers the joining compatibility and classification rules:
  - fusion vs solid-state vs cold vs pressure vs polymer vs brazing/soldering
    vs adhesive vs mechanical classification
  - material compatibility (metallurgical, galvanic, thermal-expansion, HAZ)
  - dissimilar-metal risk flagging (galvanic coupling, CTE mismatch)
  - inspectability assessment
  - thermoset + remelt (fusion) process -> rejected (mirrors polymer plan rule)

Also covers the machining advisor: datum scheme, accessibility, fixturing,
tool class, chatter risk, tolerance capability, surface integrity.
"""

from __future__ import annotations

from general_ludd.materials.core import SCHEMA_VERSION
from general_ludd.materials.joining import JoiningAdvisor
from general_ludd.materials.machining import MachiningAdvisor


class TestJoiningProcessClassification:
    def test_classify_fusion_processes(self):
        adv = JoiningAdvisor()
        for proc in ("gmaaw", "gmaw", "smaaw", "tig", "mig", "laser_welding", "ebw"):
            cls = adv.classify_process(proc)
            assert cls["category"] == "fusion", f"{proc} should be fusion, got {cls}"

    def test_classify_solid_state_processes(self):
        adv = JoiningAdvisor()
        for proc in ("friction_stir_welding", "fsw", "diffusion_bonding", "ultrasonic_welding"):
            cls = adv.classify_process(proc)
            assert cls["category"] == "solid_state", f"{proc} should be solid_state, got {cls}"

    def test_classify_cold_pressure_polymer_and_other_categories(self):
        adv = JoiningAdvisor()
        assert adv.classify_process("cold_welding")["category"] == "cold"
        assert adv.classify_process("resistance_welding")["category"] == "pressure"
        assert adv.classify_process("hot_plate_welding")["category"] == "polymer"
        assert adv.classify_process("brazing")["category"] == "brazing"
        assert adv.classify_process("soldering")["category"] == "soldering"
        assert adv.classify_process("adhesive_bonding")["category"] == "adhesive"
        assert adv.classify_process("bolted")["category"] == "mechanical"

    def test_classify_unknown_process_returns_insufficient_data(self):
        adv = JoiningAdvisor()
        cls = adv.classify_process("phaser_welding")
        assert cls["category"] == "unknown"
        assert cls["state"] == "insufficient_data"


class TestJoiningCompatibility:
    def test_similar_steel_fusion_is_compatible(self):
        adv = JoiningAdvisor()
        result = adv.assess_compatibility("aisi_1045", "aisi_1045", "gmaw")
        assert result["compatible"] is True
        assert result["state"] == "candidate"
        # Two identical steels: no galvanic risk
        assert result.get("galvanic_risk") in (False, None)

    def test_dissimilar_steel_aluminum_flags_galvanic_risk(self):
        adv = JoiningAdvisor()
        result = adv.assess_compatibility("aisi_1045", "aa6061_t6", "gmaw")
        # Steel/aluminum: galvanic risk and intermetallic concerns
        risks = result.get("risks", [])
        risk_kinds = {r["kind"] for r in risks}
        assert "galvanic" in risk_kinds, "steel/aluminum must flag galvanic risk"

    def test_dissimilar_metals_flag_thermal_expansion_mismatch(self):
        adv = JoiningAdvisor()
        result = adv.assess_compatibility("aisi_1045", "aa6061_t6", "gmaw")
        risk_kinds = {r["kind"] for r in result.get("risks", [])}
        # CTE mismatch between steel (~12) and aluminum (~23) µm/m·K
        assert "thermal_expansion_mismatch" in risk_kinds

    def test_unknown_material_returns_insufficient_data(self):
        adv = JoiningAdvisor()
        result = adv.assess_compatibility("unobtanium", "aisi_1045", "gmaw")
        assert result["state"] == "insufficient_data"
        assert "unknown" in result.get("reason", "").lower()

    def test_haz_concern_returned_for_fusion_processes(self):
        adv = JoiningAdvisor()
        result = adv.assess_compatibility("aisi_1045", "aisi_1045", "gmaw")
        # HAZ is a property of fusion welding — must be present in the assessment
        assert "haz" in result, "fusion welds must surface HAZ concerns"
        assert isinstance(result["haz"], dict)


class TestThermosetRemeltRejection:
    def test_thermoset_fusion_welding_rejected(self):
        adv = JoiningAdvisor()
        result = adv.assess_compatibility("epoxy_cast", "epoxy_cast", "laser_welding")
        assert result["compatible"] is False
        assert result["state"] == "rejected"
        reason = result.get("reason", "").lower()
        assert "thermoset" in reason or "remelt" in reason, (
            f"thermoset+fusion must be rejected with thermoset/remelt reason, got: {result}"
        )


class TestInspectability:
    def test_fusion_weld_carries_inspectability_assessment(self):
        adv = JoiningAdvisor()
        result = adv.assess_compatibility("aisi_1045", "aisi_1045", "gmaw")
        insp = result.get("inspectability")
        assert insp is not None, "fusion weld must have inspectability assessment"
        assert "methods" in insp
        # NDT methods appropriate for steel fusion welds
        methods = {m.lower() for m in insp["methods"]}
        assert any("vt" in m or "visual" in m or "radiograph" in m or "ultrasonic" in m for m in methods), (
            f"steel fusion welds need VT/RT/UT inspection methods, got {methods}"
        )

    def test_adhesive_joint_inspectability_is_limited(self):
        adv = JoiningAdvisor()
        result = adv.assess_compatibility("aa6061_t6", "aa6061_t6", "adhesive_bonding")
        insp = result.get("inspectability", {})
        # Adhesive bonds are difficult to inspect — must surface the limitation
        assert insp.get("difficulty") in ("high", "limited"), (
            f"adhesive bond inspectability must be high/limited difficulty, got {insp}"
        )


class TestClassifyProcessEdgeCases:
    def test_classify_is_case_insensitive(self):
        adv = JoiningAdvisor()
        assert adv.classify_process("GMAW")["category"] == "fusion"
        assert adv.classify_process("BraZing")["category"] == "brazing"
        assert adv.classify_process("FSW")["category"] == "solid_state"

    def test_classify_strips_whitespace(self):
        adv = JoiningAdvisor()
        assert adv.classify_process("  tig  ")["category"] == "fusion"
        assert adv.classify_process("\tbolted\n")["category"] == "mechanical"

    def test_classify_returns_reason_for_unknown(self):
        adv = JoiningAdvisor()
        cls = adv.classify_process("magic_glue")
        assert "reason" in cls
        assert "magic_glue" in cls["reason"]


class TestMissingMaterialHandling:
    def test_missing_material_b_returns_insufficient_data(self):
        adv = JoiningAdvisor()
        result = adv.assess_compatibility("aisi_1045", "unobtanium", "gmaw")
        assert result["state"] == "insufficient_data"
        assert "unobtanium" in result["reason"]

    def test_both_materials_missing_returns_insufficient_data(self):
        adv = JoiningAdvisor()
        result = adv.assess_compatibility("foo", "bar", "tig")
        assert result["state"] == "insufficient_data"

    def test_unknown_process_and_known_materials_returns_insufficient_data(self):
        adv = JoiningAdvisor()
        result = adv.assess_compatibility("aisi_1045", "aa6061_t6", "phaser_welding")
        assert result["state"] == "insufficient_data"
        assert result["category"] == "unknown"
        assert result["compatible"] is False


class TestNonFusionProcessAssessments:
    def test_cold_welding_assessment(self):
        adv = JoiningAdvisor()
        result = adv.assess_compatibility("aisi_1045", "aisi_1045", "cold_welding")
        assert result["category"] == "cold"
        assert result["compatible"] is True
        assert result["state"] == "candidate"
        assert result["haz"]["present"] is False

    def test_solid_state_welding_assessment(self):
        adv = JoiningAdvisor()
        result = adv.assess_compatibility("aisi_1045", "aa6061_t6", "fsw")
        assert result["category"] == "solid_state"
        assert "haz" in result
        assert result["haz"]["present"] is False

    def test_pressure_welding_assessment(self):
        adv = JoiningAdvisor()
        result = adv.assess_compatibility("aisi_1045", "aisi_1045", "spot_welding")
        assert result["category"] == "pressure"
        assert result["compatible"] is True

    def test_mechanical_fastening_assessment(self):
        adv = JoiningAdvisor()
        result = adv.assess_compatibility("aisi_1045", "aa6061_t6", "riveting")
        assert result["category"] == "mechanical"
        assert result["compatible"] is True
        assert result["haz"]["present"] is False

    def test_brazing_assessment(self):
        adv = JoiningAdvisor()
        result = adv.assess_compatibility("aisi_1045", "aa6061_t6", "vacuum_brazing")
        assert result["category"] == "brazing"
        assert result["compatible"] is True

    def test_soldering_assessment(self):
        adv = JoiningAdvisor()
        result = adv.assess_compatibility("aisi_1045", "aisi_1045", "wave_soldering")
        assert result["category"] == "soldering"
        assert result["compatible"] is True

    def test_polymer_joining_assessment(self):
        adv = JoiningAdvisor()
        result = adv.assess_compatibility("abs", "abs", "hot_plate_welding")
        assert result["category"] == "polymer"
        assert result["compatible"] is True


class TestGalvanicRiskEdgeCases:
    def test_same_galvanic_bucket_no_flag(self):
        adv = JoiningAdvisor()
        result = adv.assess_compatibility("aisi_1045", "aisi_1045", "gmaw")
        assert result.get("galvanic_risk") is False

    def test_polymer_to_polymer_no_galvanic_flag(self):
        adv = JoiningAdvisor()
        result = adv.assess_compatibility("abs", "abs", "hot_plate_welding")
        assert result.get("galvanic_risk") is False

    def test_polymer_to_metal_no_galvanic_flag(self):
        adv = JoiningAdvisor()
        result = adv.assess_compatibility("abs", "aisi_1045", "adhesive_bonding")
        assert result.get("galvanic_risk") is False


class TestCTEMismatchEdgeCases:
    def test_polymer_causes_high_cte_mismatch(self):
        adv = JoiningAdvisor()
        result = adv.assess_compatibility("abs", "aisi_1045", "adhesive_bonding")
        risk_kinds = {r["kind"] for r in result.get("risks", [])}
        assert "thermal_expansion_mismatch" in risk_kinds

    def test_similar_cte_no_mismatch_flag(self):
        adv = JoiningAdvisor()
        result = adv.assess_compatibility("aisi_1045", "aisi_1045", "gmaw")
        risk_kinds = {r["kind"] for r in result.get("risks", [])}
        assert "thermal_expansion_mismatch" not in risk_kinds


class TestIntermetallicEdgeCases:
    def test_same_metal_class_no_intermetallic_flag(self):
        adv = JoiningAdvisor()
        result = adv.assess_compatibility("aisi_1045", "aisi_1045", "gmaw")
        risk_kinds = {r["kind"] for r in result.get("risks", [])}
        assert "intermetallic_formation" not in risk_kinds

    def test_metal_to_polymer_no_intermetallic_flag(self):
        adv = JoiningAdvisor()
        result = adv.assess_compatibility("aisi_1045", "abs", "adhesive_bonding")
        risk_kinds = {r["kind"] for r in result.get("risks", [])}
        assert "intermetallic_formation" not in risk_kinds


class TestThermosetRemeltAdditional:
    def test_thermoset_material_b_fusion_rejected(self):
        adv = JoiningAdvisor()
        result = adv.assess_compatibility("aisi_1045", "epoxy_cast", "gmaw")
        assert result["compatible"] is False
        assert result["state"] == "rejected"
        assert "thermoset" in result["reason"].lower()

    def test_thermoplastic_fusion_not_rejected(self):
        adv = JoiningAdvisor()
        result = adv.assess_compatibility("abs", "abs", "laser_welding")
        assert result["state"] != "rejected"


class TestInspectabilityAllCategories:
    def test_solid_state_inspectability(self):
        adv = JoiningAdvisor()
        result = adv.assess_compatibility("aisi_1045", "aa6061_t6", "fsw")
        insp = result["inspectability"]
        assert insp["difficulty"] == "medium"
        assert "UT" in insp["methods"]

    def test_cold_welding_inspectability(self):
        adv = JoiningAdvisor()
        result = adv.assess_compatibility("aisi_1045", "aisi_1045", "cold_welding")
        insp = result["inspectability"]
        assert insp["difficulty"] == "low"
        assert "VT" in insp["methods"]

    def test_pressure_welding_inspectability(self):
        adv = JoiningAdvisor()
        result = adv.assess_compatibility("aisi_1045", "aisi_1045", "spot_welding")
        insp = result["inspectability"]
        assert insp["difficulty"] == "medium"
        assert "UT" in insp["methods"]

    def test_polymer_inspectability(self):
        adv = JoiningAdvisor()
        result = adv.assess_compatibility("abs", "abs", "hot_plate_welding")
        insp = result["inspectability"]
        assert insp["difficulty"] == "low"
        assert "leak_test" in insp["methods"]

    def test_brazing_inspectability(self):
        adv = JoiningAdvisor()
        result = adv.assess_compatibility("aisi_1045", "aa6061_t6", "vacuum_brazing")
        insp = result["inspectability"]
        assert insp["difficulty"] == "medium"
        assert "RT" in insp["methods"]

    def test_soldering_inspectability(self):
        adv = JoiningAdvisor()
        result = adv.assess_compatibility("aisi_1045", "aisi_1045", "wave_soldering")
        insp = result["inspectability"]
        assert insp["difficulty"] == "low"
        assert "X-ray" in insp["methods"]

    def test_mechanical_inspectability(self):
        adv = JoiningAdvisor()
        result = adv.assess_compatibility("aisi_1045", "aa6061_t6", "riveting")
        insp = result["inspectability"]
        assert insp["difficulty"] == "low"
        assert "torque_check" in insp["methods"]


class TestSchemaVersionAndMetadata:
    def test_result_includes_schema_version(self):
        adv = JoiningAdvisor()
        result = adv.assess_compatibility("aisi_1045", "aisi_1045", "gmaw")
        assert "schema_version" in result
        assert result["schema_version"] == SCHEMA_VERSION
        assert isinstance(result["schema_version"], str)

    def test_result_includes_material_ids(self):
        adv = JoiningAdvisor()
        result = adv.assess_compatibility("aisi_1045", "aa6061_t6", "gmaw")
        assert result["material_a"] == "aisi_1045"
        assert result["material_b"] == "aa6061_t6"
        assert result["process"] == "gmaw"


class TestMachiningAdvisor:
    def test_datum_scheme_returned(self):
        adv = MachiningAdvisor()
        plan = adv.plan("aisi_1045", "milling")
        assert "datum_scheme" in plan
        ds = plan["datum_scheme"]
        assert "primary" in ds and "secondary" in ds

    def test_accessibility_assessment(self):
        adv = MachiningAdvisor()
        plan = adv.plan("aisi_1045", "milling")
        assert "accessibility" in plan
        acc = plan["accessibility"]
        assert "status" in acc

    def test_fixturing_requirements_returned(self):
        adv = MachiningAdvisor()
        plan = adv.plan("aa6061_t6", "milling")
        assert "fixturing" in plan
        fix = plan["fixturing"]
        assert "clamp_type" in fix or "method" in fix

    def test_cutting_tool_class_selection(self):
        adv = MachiningAdvisor()
        plan = adv.plan("aisi_1045", "milling")
        assert "tool_class" in plan
        # Hardened steel requires carbide or better
        tool = plan["tool_class"].lower()
        assert "carbide" in tool or "cermet" in tool or "ceramic" in tool, (
            f"1045 milling needs carbide+, got: {plan['tool_class']}"
        )

    def test_chatter_risk_flag_for_aluminum(self):
        adv = MachiningAdvisor()
        plan = adv.plan("aa6061_t6", "milling")
        assert "chatter_risk" in plan
        # Aluminum's low modulus / high ductility raises chatter risk
        assert plan["chatter_risk"] in ("low", "medium", "high")

    def test_tolerance_capability(self):
        adv = MachiningAdvisor()
        plan = adv.plan("aisi_1045", "turning")
        assert "tolerance_capability" in plan
        tol = plan["tolerance_capability"]
        assert "it_grade" in tol or "band_mm" in tol

    def test_surface_integrity_returned(self):
        adv = MachiningAdvisor()
        plan = adv.plan("aisi_1045", "grinding")
        assert "surface_integrity" in plan
        si = plan["surface_integrity"]
        assert isinstance(si, dict)

    def test_unknown_material_returns_insufficient_data(self):
        adv = MachiningAdvisor()
        plan = adv.plan("unobtanium", "milling")
        assert plan["state"] == "insufficient_data"

    def test_unknown_process_returns_insufficient_data(self):
        adv = MachiningAdvisor()
        plan = adv.plan("aisi_1045", "phaser_machining")
        assert plan["state"] == "insufficient_data"
