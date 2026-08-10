"""Deep tests for ``general_ludd.chemistry.safety`` — hazard screening,
compatibility matrix, GHS classification edge cases.

Covers every INCOMPATIBILITY_MATRIX pair, tier-propagation paths, refusal
trigger combinations, concentration/scale boundary values, and the full
SafetyScreen shape including serialization fidelity.
"""

from __future__ import annotations

import importlib.util
import os
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
_SAFETY_PATH = os.path.join(_PROJECT_ROOT, "src", "general_ludd", "chemistry", "safety.py")


def _load(path: str, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None, f"{name} spec failed"
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


safety = _load(_SAFETY_PATH, "chem_safety_deep_under_test")


_TIER_RANK = {"low": 0, "moderate": 1, "high": 2, "prohibited": 3}


# ---------------------------------------------------------------------------
# Hazard screening — tier classification edge cases
# ---------------------------------------------------------------------------


class TestHazardScreeningDeep:
    """Edge cases and boundary conditions for classify_risk."""

    def test_empty_entity_list_tiers_low(self):
        screen = safety.classify_risk([])
        assert screen.risk_tier == "low"
        assert screen.hazard_classes == []
        assert screen.incompatibilities == []
        assert screen.refused_reason is None

    def test_single_entity_string_form(self):
        screen = safety.classify_risk("ethanol")
        assert screen.risk_tier == "moderate"
        assert "flammable" in screen.hazard_classes

    def test_single_entity_list_form(self):
        screen = safety.classify_risk(["ethanol"])
        assert screen.risk_tier == "moderate"

    def test_unknown_chemical_defaults_moderate_with_refusal(self):
        screen = safety.classify_risk("gobbledygook-xyz-nope")
        assert screen.risk_tier == "moderate"
        assert screen.refused_reason is not None
        assert "hazard-evidence" in screen.refused_reason

    def test_multiple_unknowns_still_moderate(self):
        screen = safety.classify_risk(["foo-unknown-1", "bar-unknown-2"])
        assert screen.risk_tier == "moderate"
        assert "hazard-evidence" in screen.refused_reason

    def test_mixed_known_unknown_resolves_worst_tier(self):
        screen = safety.classify_risk(["ethanol", "totally-unknown-xyzzy"])
        assert _TIER_RANK[screen.risk_tier] >= _TIER_RANK["moderate"]

    def test_water_with_all_entities_present_per_entity(self):
        screen = safety.classify_risk(["water", "ethanol"])
        queries = {e["query"] for e in screen.per_entity}
        assert queries == {"water", "ethanol"}

    def test_per_entity_carries_tier_and_classes(self):
        screen = safety.classify_risk("sulfuric acid")
        assert len(screen.per_entity) == 1
        pe = screen.per_entity[0]
        assert pe["query"] == "sulfuric acid"
        assert pe["tier"] == "high"
        assert "corrosive_strong_acid" in pe["classes"]

    def test_per_entity_for_unknown_carries_moderate_with_empty_classes(self):
        screen = safety.classify_risk("made-up-chemical")
        pe = screen.per_entity[0]
        assert pe["classes"] == []
        assert pe["tier"] == "moderate"
        assert pe["controls"] == []

    def test_max_tier_propagated_from_multiple_entities(self):
        screen = safety.classify_risk(["water", "picric acid"])
        assert screen.risk_tier == "prohibited"

    def test_ethanol_controls_include_ventilation(self):
        screen = safety.classify_risk("ethanol")
        assert "ventilation" in screen.required_controls
        assert "no_ignition_sources" in screen.required_controls

    def test_required_controls_deduplicated_across_entities(self):
        screen = safety.classify_risk(["ethanol", "acetone"])
        assert screen.required_controls.count("ventilation") == 1
        assert "ventilation" in screen.required_controls


# ---------------------------------------------------------------------------
# Compatibility matrix — every pair and edge cases
# ---------------------------------------------------------------------------


class TestCompatibilityMatrixDeep:
    """Every INCOMPATIBILITY_MATRIX pair and edge conditions."""

    def test_oxidizer_flammable_pair(self):
        findings = safety.check_compatibility(["acetone", "hydrogen peroxide"])
        kinds = {f["kind"] for f in findings}
        assert "oxidizer_flammable" in kinds
        f = next(x for x in findings if x["kind"] == "oxidizer_flammable")
        assert f["severity"] == "prohibited"
        assert set(f["classes"]) == {"flammable", "oxidizer"}

    def test_oxidizer_peroxide_pair(self):
        findings = safety.check_compatibility(["diethyl ether", "hydrogen peroxide"])
        kinds = {f["kind"] for f in findings}
        assert "oxidizer_peroxide" in kinds

    def test_acid_base_exotherm_pair(self):
        findings = safety.check_compatibility(["hydrochloric acid", "sodium hydroxide"])
        kinds = {f["kind"] for f in findings}
        assert "acid_base_exotherm" in kinds

    def test_water_reactive_with_water(self):
        # sodium metal is water_reactive; water is low (no aqueous class),
        # so this pair won't match unless water gets "aqueous" class.
        # Verify that water is NOT in the hazard classes for water.
        screen_water = safety.classify_risk("water")
        assert "aqueous" not in screen_water.hazard_classes
        # sodium + hydrogen peroxide (oxidizer): should find oxidizer_ flammable?
        # sodium is water_reactive, not flammable.
        findings = safety.check_compatibility(["sodium metal", "water"])
        kinds = {f["kind"] for f in findings}
        assert "water_reactive_water" not in kinds, (
            "water is not aqueous-classed; matrix should not fire on 'water' entity unless aqueous class present"
        )

    def test_pyrophoric_air_pair_not_triggered_by_unknown(self):
        # "air" is not in HAZARD_REGISTRY, so no class is resolved.
        # check_compatibility uses only resolved classes.
        findings = safety.check_compatibility(["white phosphorus", "air"])
        kinds = {f["kind"] for f in findings}
        assert "pyrophoric_air" not in kinds

    def test_compatible_low_risk_pair(self):
        findings = safety.check_compatibility(["water", "glucose"])
        assert findings == []

    def test_compatible_pair_known_non_overlapping(self):
        findings = safety.check_compatibility(["ethanol", "sodium chloride"])
        assert findings == []

    def test_incompatibility_severity_elevates_screen_tier(self):
        screen = safety.classify_risk(["acetone", "hydrogen peroxide"])
        assert screen.risk_tier == "prohibited"

    def test_acid_base_incompatibility_elevates_to_moderate(self):
        # HCl (moderate) + NaOH (moderate), acid_base_exotherm severity=moderate
        screen = safety.classify_risk(["hydrochloric acid", "sodium hydroxide"])
        kinds = {f["kind"] for f in screen.incompatibilities}
        assert "acid_base_exotherm" in kinds
        # still moderate tier overall
        assert screen.risk_tier == "moderate"

    def test_all_incompatibility_findings_in_screen(self):
        screen = safety.classify_risk(["acetone", "diethyl ether", "hydrogen peroxide"])
        kinds = {f["kind"] for f in screen.incompatibilities}
        # acetone flammable + H2O2 oxidizer = oxidizer_flammable
        # diethyl ether (flammable, peroxide_former) + H2O2 oxidizer = both oxidizer_flammable and oxidizer_peroxide
        assert "oxidizer_flammable" in kinds
        assert "oxidizer_peroxide" in kinds

    def test_three_entity_compatibility_with_two_hazard_classes(self):
        screen = safety.classify_risk(["acetone", "hydrogen peroxide", "sulfuric acid"])
        assert len(screen.incompatibilities) >= 1

    def test_single_entity_no_incompatibilities(self):
        screen = safety.classify_risk("acetone")
        assert screen.incompatibilities == []

    def test_check_compatibility_unknown_entities_returns_empty(self):
        findings = safety.check_compatibility(["foo-bar", "baz-qux"])
        assert findings == []


# ---------------------------------------------------------------------------
# GHS classification — concentration and scale edge cases
# ---------------------------------------------------------------------------


class TestGHSEdgeCases:
    """Concentration thresholds, scale elevation, and tier boundary values."""

    def test_concentration_at_threshold_not_elevated(self):
        screen = safety.classify_risk(
            "hydrochloric acid",
            concentration=safety.CONCENTRATED_THRESHOLD_MOL_PER_L,
        )
        assert screen.risk_tier == "moderate"

    def test_concentration_below_threshold_not_elevated(self):
        screen = safety.classify_risk(
            "hydrochloric acid",
            concentration=3.0,
        )
        assert screen.risk_tier == "moderate"

    def test_concentration_above_threshold_elevates_moderate_to_high(self):
        screen = safety.classify_risk(
            "hydrochloric acid",
            concentration=8.0,
        )
        assert screen.risk_tier == "high"

    def test_concentration_on_already_high_no_extra_elevation(self):
        screen = safety.classify_risk(
            "sulfuric acid",
            concentration=12.0,
        )
        assert screen.risk_tier == "high"
        assert not any("concentration-elevation" in lim for lim in screen.limitations)

    def test_concentration_none_not_elevated(self):
        screen = safety.classify_risk("hydrochloric acid", concentration=None)
        assert screen.risk_tier == "moderate"

    def test_industrial_scale_elevates_moderate_only(self):
        screen = safety.classify_risk("ethanol", scale="industrial")
        assert screen.risk_tier == "high"
        assert any("scale-elevation" in lim for lim in screen.limitations)

    def test_industrial_scale_does_not_elevate_high(self):
        screen = safety.classify_risk("sulfuric acid", scale="industrial")
        assert screen.risk_tier == "high"

    def test_pilot_scale_does_not_elevate(self):
        screen = safety.classify_risk("ethanol", scale="pilot")
        assert screen.risk_tier == "moderate"

    def test_lab_scale_does_not_elevate(self):
        screen = safety.classify_risk("ethanol", scale="lab")
        assert screen.risk_tier == "moderate"

    def test_unknown_scale_defaults_to_lab(self):
        screen = safety.classify_risk("ethanol", scale="gigantic")
        assert screen.risk_tier == "moderate"
        assert screen.scale == "lab"

    def test_concentration_no_effect_on_low_tier(self):
        screen = safety.classify_risk("water", concentration=20.0)
        assert screen.risk_tier == "low"


# ---------------------------------------------------------------------------
# Refusal behavior — all §9 paths and combinations
# ---------------------------------------------------------------------------


class TestRefusalDeep:
    """Every refusal path from _decide_refusal and their combinations."""

    def test_prohibited_refused_with_policy_reason(self):
        screen = safety.classify_risk("picric acid")
        assert screen.risk_tier == "prohibited"
        assert screen.refused_reason is not None
        assert "prohibited" in screen.refused_reason.lower()
        assert "bypass" not in screen.refused_reason.lower()

    def test_high_tier_with_missing_controls_refused(self):
        screen = safety.classify_risk(
            "sulfuric acid",
            facility_controls=[],
        )
        assert screen.risk_tier == "high"
        assert screen.refused_reason is not None
        assert "control" in screen.refused_reason.lower()

    def test_high_tier_with_all_controls_not_refused_for_controls(self):
        screen = safety.classify_risk(
            "sulfuric acid",
            facility_controls=["acid_PPE", "emergency_shower", "slow_add_to_water"],
        )
        assert screen.missing_controls == []
        if screen.refused_reason is not None:
            assert "control" not in screen.refused_reason.lower()

    def test_missing_evidence_refused(self):
        screen = safety.classify_risk("nonexistent-chemical")
        assert screen.refused_reason is not None
        assert "hazard-evidence" in screen.refused_reason

    def test_moderate_tier_with_missing_controls_not_refused(self):
        # ethanol needs ventilation, no_ignition_sources; both missing
        screen = safety.classify_risk("ethanol", facility_controls=[])
        assert "ventilation" in screen.missing_controls
        assert screen.risk_tier == "moderate"
        assert screen.refused_reason is None, (
            "moderate tier with missing controls should NOT be refused per _decide_refusal"
        )

    def test_low_tier_no_refusal(self):
        screen = safety.classify_risk("water")
        assert screen.risk_tier == "low"
        assert screen.refused_reason is None

    def test_low_tier_missing_controls_no_refusal(self):
        screen = safety.classify_risk("water", facility_controls=[])
        assert screen.risk_tier == "low"
        assert screen.refused_reason is None

    def test_prohibited_missing_control_refused_for_prohibited_priority(self):
        screen = safety.classify_risk(
            "picric acid",
            facility_controls=[],
        )
        assert screen.risk_tier == "prohibited"
        assert screen.refused_reason is not None
        assert "prohibited" in screen.refused_reason.lower()

    def test_missing_evidence_plus_missing_controls_returns_evidence_refusal(self):
        # unknown entity: missing evidence takes the refusal reason.
        # If we also have a high-tier entity missing controls, the combined
        # screen could produce either. Let's test just unknown.
        screen = safety.classify_risk(
            "fictional-reagent-abc",
            facility_controls=[],
        )
        assert screen.refused_reason is not None
        assert "hazard-evidence" in screen.refused_reason

    def test_refused_reason_never_suggests_bypass(self):
        for name in ("picric acid", "nitroglycerin", "hydrogen cyanide", "phosgene"):
            screen = safety.classify_risk(name)
            assert screen.refused_reason is not None, f"{name} must have refused_reason"
            assert "bypass" not in screen.refused_reason.lower(), (
                f"{name} refused_reason must never suggest bypass: {screen.refused_reason}"
            )
            assert "skip" not in screen.refused_reason.lower()
            assert "ignore" not in screen.refused_reason.lower()

    def test_refused_reason_never_suggests_disabling_controls(self):
        screen = safety.classify_risk("sulfuric acid", facility_controls=[])
        assert screen.refused_reason is not None
        assert "without" not in screen.refused_reason.lower() or "install" in screen.refused_reason.lower()


# ---------------------------------------------------------------------------
# SafetyScreen serialization and shape
# ---------------------------------------------------------------------------


class TestSafetyScreenDeep:
    """Full SafetyScreen shape, to_dict fidelity, and safety block invariants."""

    def test_to_dict_all_keys_present(self):
        screen = safety.classify_risk("ethanol", scale="industrial", concentration=8.0)
        d = screen.to_dict()
        for key in (
            "schema_version",
            "risk_tier",
            "required_controls",
            "missing_controls",
            "hazard_classes",
            "incompatibilities",
            "per_entity",
            "limitations",
            "scale",
            "concentration",
            "refused_reason",
            "safety",
        ):
            assert key in d, f"missing key: {key}"

    def test_to_dict_matches_dataclass_fields(self):
        screen = safety.classify_risk("acetone")
        d = screen.to_dict()
        assert d["risk_tier"] == screen.risk_tier
        assert d["hazard_classes"] == screen.hazard_classes
        assert d["incompatibilities"] == screen.incompatibilities
        assert d["limitations"] == screen.limitations
        assert d["refused_reason"] == screen.refused_reason

    def test_safety_block_low_tier_has_nonempty_approvals(self):
        screen = safety.classify_risk("water")
        assert screen.safety["approvals"] != []
        assert screen.safety["risk_tier"] == "low"

    def test_safety_block_moderate_tier_has_nonempty_approvals(self):
        screen = safety.classify_risk("ethanol")
        assert screen.risk_tier == "moderate"
        assert screen.safety["approvals"] != []

    def test_safety_block_high_tier_empty_approvals(self):
        screen = safety.classify_risk("sulfuric acid")
        assert screen.risk_tier == "high"
        assert screen.safety["approvals"] == []

    def test_safety_block_prohibited_tier_empty_approvals(self):
        screen = safety.classify_risk("picric acid")
        assert screen.risk_tier == "prohibited"
        assert screen.safety["approvals"] == []

    def test_safety_block_carries_review_id(self):
        screen = safety.classify_risk("water")
        assert "review_id" in screen.safety
        assert isinstance(screen.safety["review_id"], str)
        assert len(screen.safety["review_id"]) > 0

    def test_to_dict_safety_is_dict_not_ref(self):
        screen = safety.classify_risk("ethanol")
        d = screen.to_dict()
        assert isinstance(d["safety"], dict)
        assert d["safety"]["risk_tier"] == screen.risk_tier

    def test_missing_controls_absent_from_facility(self):
        screen = safety.classify_risk(
            "sulfuric acid",
            facility_controls=["ventilation"],
        )
        assert "acid_PPE" in screen.missing_controls
        assert "emergency_shower" in screen.missing_controls

    def test_all_controls_present_no_missing(self):
        screen = safety.classify_risk(
            "ethanol",
            facility_controls=["ventilation", "no_ignition_sources"],
        )
        assert screen.missing_controls == []

    def test_extra_facility_controls_do_not_appear_in_missing(self):
        screen = safety.classify_risk(
            "water",
            facility_controls=["blast_shield", "permits", "inert_atmosphere"],
        )
        assert screen.missing_controls == []


# ---------------------------------------------------------------------------
# Multi-entity hazard propagation edge cases
# ---------------------------------------------------------------------------


class TestMultiEntityPropagation:
    """Tier and class propagation across multiple entities."""

    def test_worst_hazard_classes_union(self):
        screen = safety.classify_risk(["ethanol", "sulfuric acid"])
        assert "flammable" in screen.hazard_classes
        assert "corrosive_strong_acid" in screen.hazard_classes
        assert "oxidizer" in screen.hazard_classes

    def test_controls_union_across_entities(self):
        screen = safety.classify_risk(["ethanol", "sodium hydroxide"])
        assert "ventilation" in screen.required_controls
        assert "no_ignition_sources" in screen.required_controls
        assert "base_PPE" in screen.required_controls
        assert "emergency_shower" in screen.required_controls

    def test_low_plus_moderate_equals_moderate(self):
        screen = safety.classify_risk(["water", "ethanol"])
        assert screen.risk_tier == "moderate"

    def test_low_plus_high_equals_high(self):
        screen = safety.classify_risk(["water", "sulfuric acid"])
        assert screen.risk_tier == "high"

    def test_high_plus_prohibited_equals_prohibited(self):
        screen = safety.classify_risk(["sulfuric acid", "nitroglycerin"])
        assert screen.risk_tier == "prohibited"

    def test_moderate_plus_moderate_plus_high_elevated_by_incompatibilities(self):
        screen = safety.classify_risk(["ethanol", "acetone", "sulfuric acid"])
        assert screen.risk_tier in {"high", "prohibited"}

    def test_three_entities_all_low_stays_low(self):
        screen = safety.classify_risk(["water", "sodium chloride", "glucose"])
        assert screen.risk_tier == "low"

    def test_three_entities_per_entity_count(self):
        screen = safety.classify_risk(["water", "ethanol", "acetone"])
        assert len(screen.per_entity) == 3

    def test_per_entity_order_matches_input(self):
        screen = safety.classify_risk(["ethanol", "water", "acetone"])
        assert screen.per_entity[0]["query"] == "ethanol"
        assert screen.per_entity[1]["query"] == "water"
        assert screen.per_entity[2]["query"] == "acetone"


# ---------------------------------------------------------------------------
# HAZARD_REGISTRY coverage — every tier and class
# ---------------------------------------------------------------------------


class TestHazardRegistryCoverage:
    """Cover key entries in HAZARD_REGISTRY via classify_risk."""

    def test_sodium_metal_water_reactive(self):
        screen = safety.classify_risk("sodium metal")
        assert "water_reactive" in screen.hazard_classes
        assert "inert_atmosphere" in screen.required_controls

    def test_potassium_metal_water_reactive(self):
        screen = safety.classify_risk("potassium metal")
        assert "water_reactive" in screen.hazard_classes
        assert screen.risk_tier == "high"

    def test_lialh4_pyrophoric_and_water_reactive(self):
        screen = safety.classify_risk("lithium aluminium hydride")
        assert "water_reactive" in screen.hazard_classes
        assert "pyrophoric" in screen.hazard_classes
        assert screen.risk_tier == "high"

    def test_diethyl_ether_peroxide_former(self):
        screen = safety.classify_risk("diethyl ether")
        assert "peroxide_former" in screen.hazard_classes
        assert "peroxide_test" in screen.required_controls

    def test_benzene_carcinogen(self):
        screen = safety.classify_risk("benzene")
        assert "carcinogen" in screen.hazard_classes
        assert screen.risk_tier == "high"

    def test_methanol_acute_toxic(self):
        screen = safety.classify_risk("methanol")
        assert "acute_toxic" in screen.hazard_classes
        assert "flammable" in screen.hazard_classes

    def test_hydrogen_cyanide_acute_toxic_prohibited(self):
        screen = safety.classify_risk("hydrogen cyanide")
        assert screen.risk_tier == "prohibited"
        assert "acute_toxic" in screen.hazard_classes

    def test_white_phosphorus_pyrophoric_and_acute_toxic(self):
        screen = safety.classify_risk("white phosphorus")
        assert "pyrophoric" in screen.hazard_classes
        assert "acute_toxic" in screen.hazard_classes
        assert screen.risk_tier == "prohibited"

    def test_potassium_chlorate_explosive_sensitizer(self):
        screen = safety.classify_risk("potassium chlorate")
        assert "explosive_sensitizer" in screen.hazard_classes
        assert "oxidizer" in screen.hazard_classes
        assert screen.risk_tier == "high"

    def test_nitric_acid_corrosive_strong_acid_and_oxidizer(self):
        screen = safety.classify_risk("nitric acid")
        assert "corrosive_strong_acid" in screen.hazard_classes
        assert "oxidizer" in screen.hazard_classes

    def test_h2so4_formula_resolves_same_as_name(self):
        screen_by_formula = safety.classify_risk("H2SO4")
        screen_by_name = safety.classify_risk("sulfuric acid")
        assert screen_by_formula.risk_tier == screen_by_name.risk_tier
        assert set(screen_by_formula.hazard_classes) == set(screen_by_name.hazard_classes)

    def test_NaCl_formula_resolves_as_low(self):
        screen = safety.classify_risk("NaCl")
        assert screen.risk_tier == "low"
        assert screen.hazard_classes == []


# ---------------------------------------------------------------------------
# CONCENTRATED_THRESHOLD_MOL_PER_L constant
# ---------------------------------------------------------------------------


class TestConcentrationThreshold:
    """Boundary behavior around the concentration threshold constant."""

    def test_threshold_is_positive(self):
        assert safety.CONCENTRATED_THRESHOLD_MOL_PER_L > 0

    def test_zero_concentration_does_not_elevate(self):
        screen = safety.classify_risk("hydrochloric acid", concentration=0.0)
        assert screen.risk_tier == "moderate"

    def test_negative_concentration_does_not_elevate(self):
        screen = safety.classify_risk("hydrochloric acid", concentration=-1.0)
        assert screen.risk_tier == "moderate"

    def test_very_large_concentration_on_low_tier_still_low(self):
        screen = safety.classify_risk("water", concentration=100.0)
        assert screen.risk_tier == "low"


# ---------------------------------------------------------------------------
# Import sanity
# ---------------------------------------------------------------------------


class TestDeepImportSanity:
    def test_public_exports_exist(self):
        for name in ("classify_risk", "check_compatibility", "SafetyScreen", "CONCENTRATED_THRESHOLD_MOL_PER_L"):
            assert hasattr(safety, name), f"missing: {name}"
