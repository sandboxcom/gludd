"""Unit tests for ``general_ludd.chemistry.safety`` and ``properties`` (Phase B).

Covers CHEM-008 (safety and compatibility) and CHEM-004 (property evidence)
from ``docs/specs/FEATURE_CHEMISTRY_EXPERT.md`` §9 and §2:

* Risk classification across low / moderate / high / prohibited tiers, including
  acute toxicity, chronic toxicity (carcinogen), flammability, explosivity, and
  reactivity (water-reactive).
* Incompatibility detection for oxidizer+flammable and acid+base pairs.
* Missing hazard evidence → actionable protocol output refused (research may
  continue) per §9 row "Missing current hazard or incompatibility evidence".
* Facility lacks required control → ``refused`` with a named reason; never a
  suggestion to bypass the control.
* Scale (industrial vs lab) and concentration elevate risk per §9 and §7.5
  ("a lab-scale procedure cannot be linearly scaled").
* Property lookup returns measured/predicted values with method, conditions,
  uncertainty, and provenance; conflicting observations are retained as
  distinct records rather than collapsed (CHEM-AT-003).

Modules are loaded by file path (mirroring ``test_chemistry_core.py``) so the
suite is robust to ``sys.path`` variations inside worktrees.
"""

from __future__ import annotations

import importlib.util
import os
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
_CORE_PATH = os.path.join(_PROJECT_ROOT, "src", "general_ludd", "chemistry", "core.py")
_SAFETY_PATH = os.path.join(_PROJECT_ROOT, "src", "general_ludd", "chemistry", "safety.py")
_PROPERTIES_PATH = os.path.join(_PROJECT_ROOT, "src", "general_ludd", "chemistry", "properties.py")


def _load(path: str, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None, f"{name} spec failed"
    mod = importlib.util.module_from_spec(spec)
    # Register before exec so @dataclass (Python 3.14+) and other decorators
    # that resolve ``sys.modules[cls.__module__]`` succeed during module load.
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


core = _load(_CORE_PATH, "chem_safety_core_under_test")
safety = _load(_SAFETY_PATH, "chem_safety_under_test")
properties = _load(_PROPERTIES_PATH, "chem_properties_under_test")


_TIER_RANK = {"low": 0, "moderate": 1, "high": 2, "prohibited": 3}


# ---------------------------------------------------------------------------
# CHEM-008 risk classification — tier ladder
# ---------------------------------------------------------------------------


class TestRiskTierLadder:
    def test_low_risk_water(self):
        screen = safety.classify_risk("water")
        assert screen.risk_tier == "low"
        assert screen.hazard_classes == []

    def test_moderate_risk_ethanol_is_flammable(self):
        screen = safety.classify_risk("ethanol")
        assert screen.risk_tier == "moderate"
        assert "flammable" in screen.hazard_classes

    def test_high_risk_sulfuric_acid_is_corrosive_oxidizer(self):
        screen = safety.classify_risk("sulfuric acid")
        assert screen.risk_tier == "high"
        assert "corrosive_strong_acid" in screen.hazard_classes
        assert "oxidizer" in screen.hazard_classes

    def test_prohibited_risk_picric_acid_is_explosive(self):
        screen = safety.classify_risk("picric acid")
        assert screen.risk_tier == "prohibited"
        assert "explosive" in screen.hazard_classes


# ---------------------------------------------------------------------------
# CHEM-008 hazard-class coverage — acute / chronic / reactivity
# ---------------------------------------------------------------------------


class TestHazardClasses:
    def test_acute_toxicity_elevated_to_prohibited(self):
        # Hydrogen cyanide is acute toxic — spec §9 elevates these.
        screen = safety.classify_risk("hydrogen cyanide")
        assert screen.risk_tier == "prohibited"
        assert "acute_toxic" in screen.hazard_classes

    def test_chronic_toxicity_carcinogen_elevated(self):
        # Benzene is a carcinogen (chronic toxicity).
        screen = safety.classify_risk("benzene")
        assert screen.risk_tier == "high"
        assert "carcinogen" in screen.hazard_classes

    def test_reactivity_water_reactive_sodium_metal(self):
        screen = safety.classify_risk("sodium metal")
        assert screen.risk_tier == "high"
        assert "water_reactive" in screen.hazard_classes
        # Required controls include inert atmosphere handling.
        assert "inert_atmosphere" in screen.required_controls


# ---------------------------------------------------------------------------
# CHEM-008 incompatibility detection
# ---------------------------------------------------------------------------


class TestCompatibility:
    def test_oxidizer_plus_flammable_detected(self):
        # Acetone (flammable) + hydrogen peroxide (oxidizer) → prohibited pair.
        findings = safety.check_compatibility(["acetone", "hydrogen peroxide"])
        assert findings, "expected at least one incompatibility finding"
        kinds = {f["kind"] for f in findings}
        assert "oxidizer_flammable" in kinds

    def test_acid_base_exotherm_detected(self):
        findings = safety.check_compatibility(["hydrochloric acid", "sodium hydroxide"])
        kinds = {f["kind"] for f in findings}
        assert "acid_base_exotherm" in kinds

    def test_compatible_pair_returns_empty(self):
        findings = safety.check_compatibility(["water", "sodium chloride"])
        assert findings == []

    def test_incompatibility_propagates_into_safety_screen(self):
        screen = safety.classify_risk(["acetone", "hydrogen peroxide"])
        assert screen.incompatibilities
        assert screen.risk_tier in {"high", "prohibited"}


# ---------------------------------------------------------------------------
# CHEM-008 §9 refusal behavior
# ---------------------------------------------------------------------------


class TestRefusal:
    def test_missing_hazard_evidence_refuses_protocol(self):
        # Unknown chemical → moderate with missing-evidence limitation, and
        # actionable output is refused (research may continue).
        screen = safety.classify_risk("totally-unknown-xyzzy-compound")
        assert screen.risk_tier == "moderate"
        assert any("missing" in lim and "hazard-evidence" in lim for lim in screen.limitations), (
            f"expected missing-hazard-evidence limitation, got: {screen.limitations}"
        )
        assert screen.refused_reason is not None
        assert "hazard-evidence" in screen.refused_reason

    def test_facility_lacks_control_is_refused(self):
        # Sulfuric acid requires acid_PPE; facility only has ventilation.
        screen = safety.classify_risk(
            "sulfuric acid",
            facility_controls=["ventilation"],
        )
        assert "acid_PPE" in screen.required_controls
        assert "acid_PPE" in screen.missing_controls
        assert screen.refused_reason is not None
        assert "control" in screen.refused_reason.lower()

    def test_facility_with_all_controls_not_refused_for_controls(self):
        # Sulfuric acid WITH all required controls → high risk but not refused
        # for missing controls.
        screen = safety.classify_risk(
            "sulfuric acid",
            facility_controls=["acid_PPE", "emergency_shower", "slow_add_to_water"],
        )
        assert screen.missing_controls == []
        assert screen.risk_tier == "high"
        # Refusal reason may still be present for high tier pending approval,
        # but it must NOT cite missing controls.
        if screen.refused_reason is not None:
            assert "control" not in screen.refused_reason.lower()

    def test_prohibited_request_carries_policy_refused_reason(self):
        screen = safety.classify_risk("nitroglycerin")
        assert screen.risk_tier == "prohibited"
        assert screen.refused_reason is not None
        # Per §9: refuse actionable detail, never suggest bypassing controls.
        assert "bypass" not in screen.refused_reason.lower()


# ---------------------------------------------------------------------------
# CHEM-008 §9 / §7.5 scale- and concentration-dependent risk
# ---------------------------------------------------------------------------


class TestScaleAndConcentration:
    def test_industrial_scale_bumps_moderate_to_high(self):
        # Ethanol is moderate at lab scale; industrial amplifies flammable
        # vapor / exotherm hazards per §7.5.
        screen = safety.classify_risk("ethanol", scale="industrial")
        assert screen.risk_tier == "high"

    def test_lab_scale_keeps_ethanol_moderate(self):
        screen = safety.classify_risk("ethanol", scale="lab")
        assert screen.risk_tier == "moderate"

    def test_concentrated_hcl_elevated_above_dilute(self):
        dilute = safety.classify_risk("hydrochloric acid", concentration=0.1)
        concentrated = safety.classify_risk("hydrochloric acid", concentration=12.0)
        assert _TIER_RANK[concentrated.risk_tier] >= _TIER_RANK[dilute.risk_tier]
        # Concentrated acid is never "low".
        assert concentrated.risk_tier != "low"


# ---------------------------------------------------------------------------
# SafetyScreen shape / serialization
# ---------------------------------------------------------------------------


class TestSafetyScreenShape:
    def test_to_dict_round_trip(self):
        screen = safety.classify_risk("ethanol")
        d = screen.to_dict()
        for key in (
            "schema_version",
            "risk_tier",
            "required_controls",
            "missing_controls",
            "hazard_classes",
            "incompatibilities",
            "limitations",
            "safety",
            "refused_reason",
        ):
            assert key in d, f"missing key in to_dict output: {key}"
        assert d["risk_tier"] == screen.risk_tier

    def test_safety_block_has_review_id_matching_tier(self):
        screen = safety.classify_risk("ethanol")
        sb = screen.safety
        assert "review_id" in sb
        assert sb["risk_tier"] == screen.risk_tier
        # Moderate risk does not require empty approvals; high/prohibited do.
        if screen.risk_tier in {"high", "prohibited"}:
            assert sb["approvals"] == []


# ---------------------------------------------------------------------------
# CHEM-004 property evidence
# ---------------------------------------------------------------------------


class TestPropertyLookup:
    def test_returns_measured_value_with_method(self):
        result = properties.lookup_property("water", "boiling_point")
        assert result["status"] == "succeeded"
        assert result["observations"], "expected at least one observation"
        methods = {o["method"] for o in result["observations"]}
        assert methods & {"measured", "predicted", "derived"}, f"expected a recognized method, got: {methods}"

    def test_every_observation_carries_unit_and_uncertainty(self):
        result = properties.lookup_property("water", "boiling_point")
        for obs in result["observations"]:
            assert obs.get("unit")
            assert "uncertainty" in obs
            assert obs["uncertainty"] >= 0

    def test_every_observation_carries_conditions_dict(self):
        result = properties.lookup_property("water", "boiling_point")
        for obs in result["observations"]:
            assert isinstance(obs["conditions"], dict)
            assert obs["conditions"], "conditions dict must not be empty"

    def test_every_observation_carries_provenance(self):
        result = properties.lookup_property("water", "boiling_point")
        for obs in result["observations"]:
            prov = obs["provenance"]
            assert "source_id" in prov
            assert "locator" in prov
            assert prov["locator"]

    def test_conflicting_values_retained_as_distinct_observations(self):
        # Water boiling point at two different pressures must remain as two
        # distinct observations — the newest never silently wins (CHEM-AT-003).
        result = properties.lookup_property("water", "boiling_point")
        keys = {(o["value"], o["conditions"].get("pressure")) for o in result["observations"]}
        assert len(keys) >= 2, f"expected >=2 distinct (value, pressure) observations, got: {keys}"

    def test_unknown_property_returns_empty_with_limitation(self):
        result = properties.lookup_property("water", "totally_made_up_property")
        assert result["observations"] == []
        assert result["status"] in {"degraded", "refused", "failed"}
        assert result["limitations"], "expected a limitation explaining the gap"

    def test_condition_filter_selects_only_matching_evidence(self):
        result = properties.lookup_property("water", "boiling_point", conditions={"pressure": "1 atm"})
        assert result["observations"]
        for obs in result["observations"]:
            assert obs["conditions"].get("pressure") == "1 atm"

    def test_predicted_property_marked_distinct_from_measured(self):
        # Methane's boiling point includes both a measured value and a
        # predicted (group-contribution) estimate; both retained.
        result = properties.lookup_property("methane", "boiling_point")
        methods = {o["method"] for o in result["observations"]}
        assert "predicted" in methods, f"expected a predicted-method observation, got methods: {methods}"
        assert "measured" in methods


# ---------------------------------------------------------------------------
# Import sanity
# ---------------------------------------------------------------------------


class TestImportSanity:
    def test_safety_exports(self):
        for symbol in ("classify_risk", "check_compatibility", "SafetyScreen"):
            assert hasattr(safety, symbol), f"safety missing public symbol: {symbol}"

    def test_properties_exports(self):
        assert hasattr(properties, "lookup_property")
