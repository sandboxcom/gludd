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
