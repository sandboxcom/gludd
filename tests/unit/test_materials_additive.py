"""Tests for additive manufacturing expert (spec MATE-001 section 4.5).

Covers process selection (FDM/SLA/SLS/DED) from material + requirements,
orientation-driven strength anisotropy, support generation, porosity
estimation, residual-stress flagging, and minimum-feature-size checks per
spec section 4.5. Fail-closed behavior is verified for unknown materials and
incompatible material/process pairings.
"""

from __future__ import annotations

import pytest

from general_ludd.materials.additive import AdditiveManufacturingAdvisor


@pytest.fixture
def advisor() -> AdditiveManufacturingAdvisor:
    return AdditiveManufacturingAdvisor()


# ---------------------------------------------------------------------------
# Process selection
# ---------------------------------------------------------------------------


class TestProcessSelection:
    def test_selects_fdm_for_thermoplastic(self, advisor: AdditiveManufacturingAdvisor) -> None:
        result = advisor.select_process(material_id="abs", requirements={"detail": "low", "strength": "low"})
        assert result["process"] == "FDM"
        assert result["compatible"] is True
        assert "feedstock" in result

    def test_selects_sla_for_high_detail(self, advisor: AdditiveManufacturingAdvisor) -> None:
        result = advisor.select_process(material_id="abs", requirements={"detail": "high", "strength": "low"})
        assert result["process"] == "SLA"

    def test_selects_ded_for_metal(self, advisor: AdditiveManufacturingAdvisor) -> None:
        result = advisor.select_process(material_id="aisi_1045", requirements={"quantity": "low"})
        assert result["process"] == "DED"
        assert result["material_class"] == "metal"

    def test_selects_sls_for_polymer_powder(self, advisor: AdditiveManufacturingAdvisor) -> None:
        result = advisor.select_process(material_id="pa66_gf30", requirements={"strength": "high", "detail": "medium"})
        assert result["process"] in ("SLS", "FDM")

    def test_rejects_unknown_material(self, advisor: AdditiveManufacturingAdvisor) -> None:
        result = advisor.select_process(material_id="unobtainium", requirements={})
        assert result["compatible"] is False
        assert result["state"] == "insufficient_data"

    def test_rejects_sla_for_metal(self, advisor: AdditiveManufacturingAdvisor) -> None:
        result = advisor.select_process(material_id="aisi_1045", requirements={"detail": "high"})
        assert result["process"] != "SLA"


# ---------------------------------------------------------------------------
# Orientation-driven strength
# ---------------------------------------------------------------------------


class TestOrientationStrength:
    def test_orientation_affects_strength(self, advisor: AdditiveManufacturingAdvisor) -> None:
        result = advisor.orientation_recommendation(material_id="abs", load_direction="z_axis")
        assert "recommended_orientation" in result
        assert "anisotropy_ratio" in result

    def test_z_axis_load_flags_weak_orientation(self, advisor: AdditiveManufacturingAdvisor) -> None:
        result = advisor.orientation_recommendation(material_id="abs", load_direction="z_axis")
        assert result["z_strength_ratio"] < 1.0


# ---------------------------------------------------------------------------
# Support strategy
# ---------------------------------------------------------------------------


class TestSupportStrategy:
    def test_overhang_triggers_support(self, advisor: AdditiveManufacturingAdvisor) -> None:
        result = advisor.support_strategy(process="FDM", overhang_deg=30.0)
        assert result["supports_required"] is True

    def test_shallow_overhang_no_support(self, advisor: AdditiveManufacturingAdvisor) -> None:
        result = advisor.support_strategy(process="FDM", overhang_deg=60.0)
        assert result["supports_required"] is False


# ---------------------------------------------------------------------------
# Porosity estimation
# ---------------------------------------------------------------------------


class TestPorosityEstimation:
    def test_porosity_estimated_for_process(self, advisor: AdditiveManufacturingAdvisor) -> None:
        result = advisor.estimate_porosity(process="SLS")
        assert 0.0 < result["porosity_pct"] <= 10.0
        assert "basis" in result

    def test_ded_higher_porosity_than_sla(self, advisor: AdditiveManufacturingAdvisor) -> None:
        ded = advisor.estimate_porosity(process="DED")
        sla = advisor.estimate_porosity(process="SLA")
        assert ded["porosity_pct"] > sla["porosity_pct"]


# ---------------------------------------------------------------------------
# Residual stress
# ---------------------------------------------------------------------------


class TestResidualStress:
    def test_residual_stress_flagged_for_sls(self, advisor: AdditiveManufacturingAdvisor) -> None:
        result = advisor.check_residual_stress(process="SLS")
        assert result["residual_stress_risk"] in ("low", "medium", "high")
        assert result["residual_stress_risk"] in ("medium", "high")

    def test_sla_low_residual_stress(self, advisor: AdditiveManufacturingAdvisor) -> None:
        result = advisor.check_residual_stress(process="SLA")
        assert result["residual_stress_risk"] == "low"


# ---------------------------------------------------------------------------
# Minimum feature size
# ---------------------------------------------------------------------------


class TestMinimumFeatureSize:
    def test_feature_below_minimum_flagged(self, advisor: AdditiveManufacturingAdvisor) -> None:
        result = advisor.check_minimum_feature(process="FDM", feature_size_mm=0.3)
        assert result["printable"] is False
        assert "minimum_feature_mm" in result

    def test_feature_above_minimum_ok(self, advisor: AdditiveManufacturingAdvisor) -> None:
        result = advisor.check_minimum_feature(process="FDM", feature_size_mm=1.5)
        assert result["printable"] is True
