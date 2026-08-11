"""Tests for metal forming experts (spec MATE-001 section 4.3).

Covers alloy condition/temper effects, formability assessment, springback
estimation, heat-treatment recommendations, and hot tearing risk per MATE-AT-003
(negative fixtures prove unsuitable heat treatments are blocked).
"""

from __future__ import annotations

import pytest

from general_ludd.materials.metals import MetalFormingAdvisor


@pytest.fixture
def advisor() -> MetalFormingAdvisor:
    return MetalFormingAdvisor()


# ---------------------------------------------------------------------------
# Alloy condition / temper effects
# ---------------------------------------------------------------------------


class TestAlloyConditionTemper:
    def test_t6_temper_identified(self, advisor: MetalFormingAdvisor) -> None:
        info = advisor.describe_condition("aa6061_t6")
        assert info["temper"] == "T6"
        assert info["condition_class"] in ("solution_heat_treated_and_aged", "precipitation_hardened")

    def test_cold_drawn_steel_identified(self, advisor: MetalFormingAdvisor) -> None:
        info = advisor.describe_condition("aisi_1045")
        assert "cold_drawn" in info["product_form"] or info["product_form"] == "cold_drawn"
        assert info["work_hardened"] is True

    def test_unknown_material_condition_fail_closed(self, advisor: MetalFormingAdvisor) -> None:
        info = advisor.describe_condition("unobtanium_alloy")
        assert info["state"] == "insufficient_data"

    def test_non_metal_material_condition_fail_closed(self, advisor: MetalFormingAdvisor) -> None:
        info = advisor.describe_condition("pa66_gf30")
        assert info["state"] == "insufficient_data"
        assert "non-metal" in info["reason"].lower()

    def test_condition_designation_included(self, advisor: MetalFormingAdvisor) -> None:
        info = advisor.describe_condition("aa6061_t6")
        assert info["designation"]
        assert info["base_alloy_family"] == "non_ferrous_aluminum"
        assert info["state"] == "ok"


# ---------------------------------------------------------------------------
# Formability assessment
# ---------------------------------------------------------------------------


class TestFormability:
    def test_aluminum_t6_low_formability(self, advisor: MetalFormingAdvisor) -> None:
        result = advisor.assess_formability("aa6061_t6", "bending")
        assert result["formability_rating"] in ("poor", "fair", "good", "excellent")
        assert result["formability_rating"] in ("poor", "fair")  # T6 is not very formable

    def test_annealed_or_cold_drawn_better_formability(self, advisor: MetalFormingAdvisor) -> None:
        steel = advisor.assess_formability("aisi_1045", "stamping")
        assert steel["formability_rating"] in ("fair", "good", "excellent")

    def test_unknown_operation_fail_closed(self, advisor: MetalFormingAdvisor) -> None:
        result = advisor.assess_formability("aa6061_t6", "teleportation")
        assert result["state"] == "insufficient_data"

    def test_non_metal_material_formability_fail_closed(self, advisor: MetalFormingAdvisor) -> None:
        result = advisor.assess_formability("pa66_gf30", "bending")
        assert result["state"] == "insufficient_data"
        assert result["formability_rating"] is None

    def test_formability_fallback_unrated_operation(self, advisor: MetalFormingAdvisor) -> None:
        result = advisor.assess_formability("aa6061_t6", "rolling")
        assert result["formability_rating"] in ("poor", "fair", "good", "excellent")
        assert result["temper"] is not None

    def test_formability_includes_annealing_note(self, advisor: MetalFormingAdvisor) -> None:
        result = advisor.assess_formability("aa6061_t6", "bending")
        assert "note" in result
        assert "annealing" in result["note"].lower()


# ---------------------------------------------------------------------------
# Springback estimation
# ---------------------------------------------------------------------------


class TestSpringback:
    def test_aluminum_high_springback(self, advisor: MetalFormingAdvisor) -> None:
        result = advisor.estimate_springback("aa6061_t6", "bending")
        assert result["springback_pct"] > 0
        assert result["springback_pct"] >= 0.3  # Al alloys spring back more

    def test_steel_lower_springback_than_aluminum(self, advisor: MetalFormingAdvisor) -> None:
        al = advisor.estimate_springback("aa6061_t6", "bending")
        steel = advisor.estimate_springback("aisi_1045", "bending")
        assert al["springback_pct"] > steel["springback_pct"]

    def test_springback_includes_compensation_note(self, advisor: MetalFormingAdvisor) -> None:
        result = advisor.estimate_springback("aa6061_t6", "bending")
        assert "compensation_strategy" in result
        assert len(result["compensation_strategy"]) > 0

    def test_non_metal_material_springback_fail_closed(self, advisor: MetalFormingAdvisor) -> None:
        result = advisor.estimate_springback("pa66_gf30", "bending")
        assert result["state"] == "insufficient_data"
        assert result["springback_pct"] is None

    def test_springback_generic_fallback_for_unlisted_operation(self, advisor: MetalFormingAdvisor) -> None:
        result = advisor.estimate_springback("aa6061_t6", "drawing")
        assert result["springback_pct"] == 0.5
        assert result["unit"] == "percent"

    def test_springback_generic_value_material_specific_compensation(self, advisor: MetalFormingAdvisor) -> None:
        result = advisor.estimate_springback("aa6061_t6", "drawing")
        assert result["springback_pct"] == 0.5
        assert "compensation_strategy" in result
        assert any("overbend" in s.lower() for s in result["compensation_strategy"])


# ---------------------------------------------------------------------------
# Heat treatment recommendations
# ---------------------------------------------------------------------------


class TestHeatTreatment:
    def test_t6_after_forging_needs_resolution_treat(self, advisor: MetalFormingAdvisor) -> None:
        result = advisor.recommend_heat_treatment("aa6061_t6", "forging")
        assert result["required"] is True
        assert any("solution" in step.lower() or "age" in step.lower() for step in result["steps"])

    def test_cold_formed_steel_optional_stress_relief(self, advisor: MetalFormingAdvisor) -> None:
        result = advisor.recommend_heat_treatment("aisi_1045", "bending")
        assert result["required"] is False

    def test_unknown_material_heat_treat_fail_closed(self, advisor: MetalFormingAdvisor) -> None:
        result = advisor.recommend_heat_treatment("unobtanium", "forging")
        assert result["state"] == "insufficient_data"

    def test_non_metal_material_heat_treat_fail_closed(self, advisor: MetalFormingAdvisor) -> None:
        result = advisor.recommend_heat_treatment("pa66_gf30", "forging")
        assert result["state"] == "insufficient_data"

    def test_heat_treatment_generic_fallback_unlisted_operation(self, advisor: MetalFormingAdvisor) -> None:
        result = advisor.recommend_heat_treatment("aa6061_t6", "stamping")
        assert result["required"] is False
        assert "stress-relief" in result["steps"][0].lower()
        assert "generic" in result["reason"].lower()


# ---------------------------------------------------------------------------
# Hot tearing risk
# ---------------------------------------------------------------------------


class TestHotTearingRisk:
    def test_aluminum_alloy_hot_tearing_risk(self, advisor: MetalFormingAdvisor) -> None:
        result = advisor.hot_tearing_risk("aa6061_t6")
        assert result["risk_level"] in ("low", "medium", "high")

    def test_steel_low_hot_tearing(self, advisor: MetalFormingAdvisor) -> None:
        result = advisor.hot_tearing_risk("aisi_1045")
        assert result["risk_level"] == "low"

    def test_hot_tearing_includes_mitigation(self, advisor: MetalFormingAdvisor) -> None:
        result = advisor.hot_tearing_risk("aa6061_t6")
        assert "mitigation" in result
        assert len(result["mitigation"]) > 0

    def test_non_metal_material_hot_tearing_fail_closed(self, advisor: MetalFormingAdvisor) -> None:
        result = advisor.hot_tearing_risk("pa66_gf30")
        assert result["state"] == "insufficient_data"
        assert result["risk_level"] is None

    def test_unknown_material_hot_tearing_fail_closed(self, advisor: MetalFormingAdvisor) -> None:
        result = advisor.hot_tearing_risk("unobtanium")
        assert result["state"] == "insufficient_data"

    def test_hot_tearing_designation_included(self, advisor: MetalFormingAdvisor) -> None:
        result = advisor.hot_tearing_risk("aa6061_t6")
        assert result["designation"]
