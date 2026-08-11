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


# ---------------------------------------------------------------------------
# Select process — untested branches
# ---------------------------------------------------------------------------


class TestSelectProcessUntested:
    def test_selects_sla_for_thermoset(self, advisor: AdditiveManufacturingAdvisor) -> None:
        result = advisor.select_process(material_id="epoxy_cast", requirements={})
        assert result["process"] == "SLA"

    def test_high_strength_routes_to_sls_for_thermoplastic(self, advisor: AdditiveManufacturingAdvisor) -> None:
        result = advisor.select_process(material_id="abs", requirements={"strength": "high"})
        assert result["process"] == "SLS"

    def test_high_detail_on_metal_stays_ded_not_sla(self, advisor: AdditiveManufacturingAdvisor) -> None:
        result = advisor.select_process(material_id="aisi_1045", requirements={"detail": "high"})
        assert result["process"] == "DED"

    def test_requirements_none_defaults_to_empty(self, advisor: AdditiveManufacturingAdvisor) -> None:
        result = advisor.select_process(material_id="abs", requirements=None)
        assert result["process"] == "FDM"
        assert result["compatible"] is True


# ---------------------------------------------------------------------------
# Orientation recommendation — untested branches
# ---------------------------------------------------------------------------


class TestOrientationUntested:
    def test_unknown_material_returns_insufficient_data(self, advisor: AdditiveManufacturingAdvisor) -> None:
        result = advisor.orientation_recommendation(material_id="unobtainium", load_direction="z_axis")
        assert result["state"] == "insufficient_data"
        assert "unknown material" in result["reason"]

    def test_build_direction_token_is_z_axis_alias(self, advisor: AdditiveManufacturingAdvisor) -> None:
        result = advisor.orientation_recommendation(material_id="abs", load_direction="build_direction")
        assert result["z_strength_ratio"] < 1.0
        assert result["anisotropy_ratio"] == "high"

    def test_z_axis_hyphenated_token_recognized(self, advisor: AdditiveManufacturingAdvisor) -> None:
        result = advisor.orientation_recommendation(material_id="abs", load_direction="z-axis")
        assert result["z_strength_ratio"] < 1.0

    def test_default_load_direction_is_in_plane(self, advisor: AdditiveManufacturingAdvisor) -> None:
        result = advisor.orientation_recommendation(material_id="abs")
        assert result["anisotropy_ratio"] == "low"


# ---------------------------------------------------------------------------
# Support strategy — untested branches
# ---------------------------------------------------------------------------


class TestSupportStrategyUntested:
    def test_unknown_process_returns_insufficient_data(self, advisor: AdditiveManufacturingAdvisor) -> None:
        result = advisor.support_strategy(process="EBM", overhang_deg=30.0)
        assert result["state"] == "insufficient_data"
        assert result["supports_required"] is None

    def test_sls_bed_supports_no_extra_supports(self, advisor: AdditiveManufacturingAdvisor) -> None:
        result = advisor.support_strategy(process="SLS", overhang_deg=10.0)
        assert result["supports_required"] is False
        assert "bed" in result["reason"]

    def test_ded_substrate_supports_no_extra_supports(self, advisor: AdditiveManufacturingAdvisor) -> None:
        result = advisor.support_strategy(process="DED", overhang_deg=10.0)
        assert result["supports_required"] is False


# ---------------------------------------------------------------------------
# Porosity estimation — untested branches
# ---------------------------------------------------------------------------


class TestPorosityUntested:
    def test_unknown_process_returns_insufficient_data(self, advisor: AdditiveManufacturingAdvisor) -> None:
        result = advisor.estimate_porosity(process="LENS")
        assert result["state"] == "insufficient_data"
        assert result["porosity_pct"] is None

    @pytest.mark.parametrize("process", ["FDM", "SLA", "SLS", "DED"])
    def test_all_known_processes_return_valid_range(self, advisor: AdditiveManufacturingAdvisor, process: str) -> None:
        result = advisor.estimate_porosity(process=process)
        assert 0.0 < result["porosity_pct"] <= 10.0
        assert isinstance(result["range_pct"], list)
        assert len(result["range_pct"]) == 2
        assert result["range_pct"][0] <= result["porosity_pct"] <= result["range_pct"][1]


# ---------------------------------------------------------------------------
# Residual stress — untested branches
# ---------------------------------------------------------------------------


class TestResidualStressUntested:
    def test_unknown_process_returns_insufficient_data(self, advisor: AdditiveManufacturingAdvisor) -> None:
        result = advisor.check_residual_stress(process="LENS")
        assert result["state"] == "insufficient_data"
        assert result["residual_stress_risk"] is None

    def test_fdm_medium_risk_includes_anneal_mitigation(self, advisor: AdditiveManufacturingAdvisor) -> None:
        result = advisor.check_residual_stress(process="FDM")
        assert result["residual_stress_risk"] == "medium"
        mitigation = result["mitigation"]
        assert any("anneal" in m for m in mitigation)

    def test_ded_high_risk_includes_stress_relief(self, advisor: AdditiveManufacturingAdvisor) -> None:
        result = advisor.check_residual_stress(process="DED")
        assert result["residual_stress_risk"] == "high"
        mitigation = result["mitigation"]
        assert any("stress-relief anneal" in m for m in mitigation)


# ---------------------------------------------------------------------------
# Minimum feature — untested branches
# ---------------------------------------------------------------------------


class TestMinimumFeatureUntested:
    def test_unknown_process_returns_insufficient_data(self, advisor: AdditiveManufacturingAdvisor) -> None:
        result = advisor.check_minimum_feature(process="LENS", feature_size_mm=1.0)
        assert result["state"] == "insufficient_data"
        assert result["printable"] is None

    def test_exact_boundary_is_printable(self, advisor: AdditiveManufacturingAdvisor) -> None:
        result = advisor.check_minimum_feature(process="FDM", feature_size_mm=1.0)
        assert result["printable"] is True


# ---------------------------------------------------------------------------
# Module constants
# ---------------------------------------------------------------------------


class TestModuleConstants:
    def test_additive_processes_tuple(self) -> None:
        from general_ludd.materials.additive import ADDITIVE_PROCESSES

        assert ADDITIVE_PROCESSES == ("FDM", "SLA", "SLS", "DED")
        assert isinstance(ADDITIVE_PROCESSES, tuple)

    def test_z_strength_ratios_in_range(self) -> None:
        from general_ludd.materials.additive import _Z_STRENGTH_RATIO

        for process, ratio in _Z_STRENGTH_RATIO.items():
            assert 0.0 < ratio <= 1.0, f"{process} ratio {ratio} out of range"


# ---------------------------------------------------------------------------
# _material_class helper
# ---------------------------------------------------------------------------


class TestMaterialClassHelper:
    def test_thermoset_polymer_returns_thermoset(self) -> None:
        from general_ludd.materials.additive import _material_class

        klass, _mat = _material_class("epoxy_cast")
        assert klass == "thermoset"

    def test_metal_returns_ferrous_carbon(self) -> None:
        from general_ludd.materials.additive import _material_class

        klass, _mat = _material_class("aisi_1045")
        assert klass == "ferrous_carbon"

    def test_thermoplastic_returns_thermoplastic(self) -> None:
        from general_ludd.materials.additive import _material_class

        klass, _mat = _material_class("abs")
        assert klass == "thermoplastic"

    def test_unknown_material_returns_none(self) -> None:
        from general_ludd.materials.additive import _material_class

        klass, mat = _material_class("unobtainium")
        assert klass is None
        assert mat is None
