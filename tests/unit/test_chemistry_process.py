"""Unit tests for ``general_ludd.chemistry.process`` — CHEM-017 process scale-up.

Covers heat transfer scaling, mixing assessment (Reynolds), thermal runaway
risk, and separation feasibility.
"""

from __future__ import annotations

import importlib.util
import os
import sys

import pytest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
_PROCESS_PATH = os.path.join(_PROJECT_ROOT, "src", "general_ludd", "chemistry", "process.py")


def _load(path: str, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None, f"{name} spec failed"
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


process_mod = _load(_PROCESS_PATH, "chem_process_under_test")

ProcessScaleUp = process_mod.ProcessScaleUp
LINEAR_SCALE_NOT_VALID = process_mod.LINEAR_SCALE_NOT_VALID


@pytest.fixture
def evaluator() -> ProcessScaleUp:
    return ProcessScaleUp()


# ---------------------------------------------------------------------------
# heat_transfer_check
# ---------------------------------------------------------------------------


class TestHeatTransferCheck:
    def test_returns_schema_version(self, evaluator: ProcessScaleUp) -> None:
        r = evaluator.heat_transfer_check(1.0, 1000.0, 0.5)
        assert r["schema_version"] == "1.0"

    def test_sv_ratio_decreases_with_scale(self, evaluator: ProcessScaleUp) -> None:
        r = evaluator.heat_transfer_check(1.0, 1000.0, 0.5)
        assert r["lab_sv_ratio"] > r["plant_sv_ratio"]
        assert r["sv_ratio_loss_factor"] > 1.0

    def test_geometric_scaling_factor(self, evaluator: ProcessScaleUp) -> None:
        lab_sa = 0.5
        r = evaluator.heat_transfer_check(1.0, 8.0, lab_sa)
        expected_plant = lab_sa * (8.0 ** (2.0 / 3.0))
        assert r["plant_surface_area_m2"] == pytest.approx(expected_plant)

    def test_1000x_scale_sv_drops_by_factor_10(self, evaluator: ProcessScaleUp) -> None:
        r = evaluator.heat_transfer_check(1.0, 1000.0, 0.5)
        assert r["sv_ratio_loss_factor"] == pytest.approx(1000.0 ** (1.0 / 3.0), rel=1e-9)

    def test_1x_scale_no_loss(self, evaluator: ProcessScaleUp) -> None:
        r = evaluator.heat_transfer_check(5.0, 5.0, 2.0)
        assert r["sv_ratio_loss_factor"] == pytest.approx(1.0)

    def test_includes_linear_scale_caveat(self, evaluator: ProcessScaleUp) -> None:
        r = evaluator.heat_transfer_check(1.0, 500.0, 0.3)
        limitations_text = " ".join(r["limitations"])
        assert LINEAR_SCALE_NOT_VALID in r["limitations"]
        assert "cooling capacity" in limitations_text.lower()

    def test_zero_lab_volume_raises(self, evaluator: ProcessScaleUp) -> None:
        with pytest.raises(ValueError, match="lab_volume_l"):
            evaluator.heat_transfer_check(0.0, 1000.0, 0.5)

    def test_negative_lab_volume_raises(self, evaluator: ProcessScaleUp) -> None:
        with pytest.raises(ValueError):
            evaluator.heat_transfer_check(-1.0, 1000.0, 0.5)

    def test_zero_plant_volume_raises(self, evaluator: ProcessScaleUp) -> None:
        with pytest.raises(ValueError, match="plant_volume_l"):
            evaluator.heat_transfer_check(1.0, 0.0, 0.5)

    def test_zero_surface_area_raises(self, evaluator: ProcessScaleUp) -> None:
        with pytest.raises(ValueError, match="lab_surface_area_m2"):
            evaluator.heat_transfer_check(1.0, 1000.0, 0.0)

    def test_name_in_result(self, evaluator: ProcessScaleUp) -> None:
        r = evaluator.heat_transfer_check(1.0, 100.0, 0.5)
        assert r["name"] == "heat_transfer_scale_check"

    def test_units_present(self, evaluator: ProcessScaleUp) -> None:
        r = evaluator.heat_transfer_check(1.0, 100.0, 0.5)
        assert r["unit_area"] == "m^2"
        assert r["unit_sv"] == "m^2/m^3"


# ---------------------------------------------------------------------------
# mixing_assessment
# ---------------------------------------------------------------------------


class TestMixingAssessment:
    def test_laminar_regime(self, evaluator: ProcessScaleUp) -> None:
        r = evaluator.mixing_assessment(0.1, 10.0, 1000.0, 100.0)
        assert r["regime"] == "laminar"
        assert r["reynolds_number"] < 10.0

    def test_transitional_regime(self, evaluator: ProcessScaleUp) -> None:
        r = evaluator.mixing_assessment(0.1, 100.0, 1000.0, 0.01)
        assert r["regime"] == "transitional"
        assert 10.0 <= r["reynolds_number"] < 10000.0

    def test_turbulent_regime(self, evaluator: ProcessScaleUp) -> None:
        r = evaluator.mixing_assessment(0.3, 500.0, 1000.0, 0.001)
        assert r["regime"] == "turbulent"
        assert r["reynolds_number"] >= 10000.0

    def test_zero_speed_is_laminar(self, evaluator: ProcessScaleUp) -> None:
        r = evaluator.mixing_assessment(0.1, 0.0, 1000.0, 1.0)
        assert r["regime"] == "laminar"
        assert r["reynolds_number"] == 0.0

    def test_includes_scale_caveat(self, evaluator: ProcessScaleUp) -> None:
        r = evaluator.mixing_assessment(0.2, 300.0, 1000.0, 0.005)
        assert LINEAR_SCALE_NOT_VALID in r["limitations"]

    def test_zero_diameter_raises(self, evaluator: ProcessScaleUp) -> None:
        with pytest.raises(ValueError, match="impeller_diameter_m"):
            evaluator.mixing_assessment(0.0, 100.0, 1000.0, 0.01)

    def test_negative_diameter_raises(self, evaluator: ProcessScaleUp) -> None:
        with pytest.raises(ValueError):
            evaluator.mixing_assessment(-0.1, 100.0, 1000.0, 0.01)

    def test_zero_density_raises(self, evaluator: ProcessScaleUp) -> None:
        with pytest.raises(ValueError, match="fluid_density_kg_m3"):
            evaluator.mixing_assessment(0.1, 100.0, 0.0, 0.01)

    def test_zero_viscosity_raises(self, evaluator: ProcessScaleUp) -> None:
        with pytest.raises(ValueError, match="fluid_viscosity_pa_s"):
            evaluator.mixing_assessment(0.1, 100.0, 1000.0, 0.0)

    def test_schema_version(self, evaluator: ProcessScaleUp) -> None:
        r = evaluator.mixing_assessment(0.2, 300.0, 1000.0, 0.005)
        assert r["schema_version"] == "1.0"

    def test_re_at_boundary_10(self, evaluator: ProcessScaleUp) -> None:
        r = evaluator.mixing_assessment(0.1, 60.0 * 0.01 / (1000.0 * 0.1**2) * 10.0, 1000.0, 0.01)
        if r["reynolds_number"] < 10.0:
            assert r["regime"] == "laminar"
        else:
            assert r["regime"] in ("transitional", "turbulent")


# ---------------------------------------------------------------------------
# runaway_risk
# ---------------------------------------------------------------------------


class TestRunawayRisk:
    def test_low_risk_small_exotherm(self, evaluator: ProcessScaleUp) -> None:
        r = evaluator.runaway_risk(10.0, 5.0, 10.0, 300.0)
        assert r["runaway_risk"] == "low"

    def test_moderate_risk_with_cooling(self, evaluator: ProcessScaleUp) -> None:
        r = evaluator.runaway_risk(100.0, 30.0, 50.0, 350.0)
        assert r["runaway_risk"] == "moderate"

    def test_high_risk_moderate_exotherm_no_cooling(self, evaluator: ProcessScaleUp) -> None:
        r = evaluator.runaway_risk(100.0, 30.0, 0.0, 350.0)
        assert r["runaway_risk"] == "high"

    def test_high_risk_large_exotherm(self, evaluator: ProcessScaleUp) -> None:
        r = evaluator.runaway_risk(200.0, 150.0, 100.0, 400.0)
        assert r["runaway_risk"] == "high"

    def test_severe_risk_very_large_rise(self, evaluator: ProcessScaleUp) -> None:
        r = evaluator.runaway_risk(500.0, 300.0, 50.0, 500.0)
        assert r["runaway_risk"] == "severe"

    def test_severe_risk_very_large_enthalpy(self, evaluator: ProcessScaleUp) -> None:
        r = evaluator.runaway_risk(400.0, 250.0, 50.0, 500.0)
        assert r["runaway_risk"] == "severe"

    def test_high_severe_triggers_hazop_limitation(self, evaluator: ProcessScaleUp) -> None:
        r = evaluator.runaway_risk(300.0, 150.0, 100.0, 400.0)
        if r["runaway_risk"] in ("high", "severe"):
            limitations_text = " ".join(r["limitations"])
            assert "HAZOP" in limitations_text or "relief" in limitations_text.lower()

    def test_no_cooling_adds_limitation(self, evaluator: ProcessScaleUp) -> None:
        r = evaluator.runaway_risk(10.0, 5.0, 0.0, 300.0)
        limitations_text = " ".join(r["limitations"])
        assert "no_heat_removal_specified" in limitations_text

    def test_cooling_provided_no_removal_limitation(self, evaluator: ProcessScaleUp) -> None:
        r = evaluator.runaway_risk(10.0, 5.0, 50.0, 300.0)
        limitations_text = " ".join(r["limitations"])
        assert "no_heat_removal_specified" not in limitations_text

    def test_includes_linear_scale_caveat(self, evaluator: ProcessScaleUp) -> None:
        r = evaluator.runaway_risk(50.0, 20.0, 30.0, 350.0)
        assert LINEAR_SCALE_NOT_VALID in r["limitations"]

    def test_zero_temp_raises(self, evaluator: ProcessScaleUp) -> None:
        with pytest.raises(ValueError, match="process_temp_k"):
            evaluator.runaway_risk(100.0, 50.0, 10.0, 0.0)

    def test_negative_temp_raises(self, evaluator: ProcessScaleUp) -> None:
        with pytest.raises(ValueError, match="process_temp_k"):
            evaluator.runaway_risk(100.0, 50.0, 10.0, -100.0)

    def test_negative_heat_removal_raises(self, evaluator: ProcessScaleUp) -> None:
        with pytest.raises(ValueError, match="heat_removal_capacity_kw"):
            evaluator.runaway_risk(100.0, 50.0, -1.0, 300.0)

    def test_schema_version(self, evaluator: ProcessScaleUp) -> None:
        r = evaluator.runaway_risk(50.0, 20.0, 30.0, 350.0)
        assert r["schema_version"] == "1.0"

    def test_exact_boundary_adiabatic_rise_50(self, evaluator: ProcessScaleUp) -> None:
        r = evaluator.runaway_risk(140.0, 50.0, 0.0, 400.0)
        assert r["runaway_risk"] in ("moderate", "high")


# ---------------------------------------------------------------------------
# separation_feasibility
# ---------------------------------------------------------------------------


class TestSeparationFeasibility:
    def test_distillation_feasible_normal_alpha(self, evaluator: ProcessScaleUp) -> None:
        r = evaluator.separation_feasibility(
            "distillation", relative_volatility=2.0, feed_composition=0.5, product_purity=0.95
        )
        assert r["feasible"] is True

    def test_distillation_infeasible_low_alpha(self, evaluator: ProcessScaleUp) -> None:
        r = evaluator.separation_feasibility(
            "distillation", relative_volatility=1.02, feed_composition=0.5, product_purity=0.95
        )
        assert r["feasible"] is False

    def test_distillation_infeasible_low_alpha_high_purity(self, evaluator: ProcessScaleUp) -> None:
        r = evaluator.separation_feasibility(
            "distillation", relative_volatility=1.3, feed_composition=0.5, product_purity=0.995
        )
        assert r["feasible"] is False

    def test_distillation_feasible_high_alpha_low_purity(self, evaluator: ProcessScaleUp) -> None:
        r = evaluator.separation_feasibility(
            "distillation", relative_volatility=3.0, feed_composition=0.5, product_purity=0.90
        )
        assert r["feasible"] is True

    def test_alpha_below_1_05_infeasible(self, evaluator: ProcessScaleUp) -> None:
        r = evaluator.separation_feasibility(
            "distillation", relative_volatility=1.049, feed_composition=0.5, product_purity=0.9
        )
        assert r["feasible"] is False
        limitations_text = " ".join(r["limitations"])
        assert "infeasible" in limitations_text.lower() or "azeotropic" in limitations_text.lower()

    def test_missing_alpha_raises(self, evaluator: ProcessScaleUp) -> None:
        with pytest.raises(ValueError, match="relative_volatility"):
            evaluator.separation_feasibility("distillation", feed_composition=0.5, product_purity=0.95)

    def test_zero_alpha_raises(self, evaluator: ProcessScaleUp) -> None:
        with pytest.raises(ValueError, match="relative_volatility"):
            evaluator.separation_feasibility(
                "distillation", relative_volatility=0.0, feed_composition=0.5, product_purity=0.95
            )

    def test_feed_composition_out_of_range_raises(self, evaluator: ProcessScaleUp) -> None:
        with pytest.raises(ValueError, match="feed_composition"):
            evaluator.separation_feasibility(
                "distillation", relative_volatility=2.0, feed_composition=0.0, product_purity=0.95
            )
        with pytest.raises(ValueError, match="feed_composition"):
            evaluator.separation_feasibility(
                "distillation", relative_volatility=2.0, feed_composition=1.0, product_purity=0.95
            )

    def test_product_purity_out_of_range_raises(self, evaluator: ProcessScaleUp) -> None:
        with pytest.raises(ValueError, match="product_purity"):
            evaluator.separation_feasibility(
                "distillation", relative_volatility=2.0, feed_composition=0.5, product_purity=0.0
            )
        with pytest.raises(ValueError, match="product_purity"):
            evaluator.separation_feasibility(
                "distillation", relative_volatility=2.0, feed_composition=0.5, product_purity=1.0
            )

    def test_unknown_method_returns_feasible_with_limitation(self, evaluator: ProcessScaleUp) -> None:
        r = evaluator.separation_feasibility("crystallization")
        assert r["feasible"] is True
        assert any("method_not_characterized" in lim for lim in r["limitations"])

    def test_extraction_method_feasible(self, evaluator: ProcessScaleUp) -> None:
        r = evaluator.separation_feasibility("extraction")
        assert r["feasible"] is True

    def test_includes_linear_scale_caveat(self, evaluator: ProcessScaleUp) -> None:
        r = evaluator.separation_feasibility(
            "distillation", relative_volatility=2.0, feed_composition=0.5, product_purity=0.95
        )
        assert LINEAR_SCALE_NOT_VALID in r["limitations"]

    def test_schema_version(self, evaluator: ProcessScaleUp) -> None:
        r = evaluator.separation_feasibility(
            "distillation", relative_volatility=2.0, feed_composition=0.5, product_purity=0.95
        )
        assert r["schema_version"] == "1.0"


# ---------------------------------------------------------------------------
# ProcessScaleUp class metadata
# ---------------------------------------------------------------------------


class TestProcessScaleUpMetadata:
    def test_schema_version_attribute(self) -> None:
        assert ProcessScaleUp.schema_version == "1.0"

    def test_method_id_attribute(self) -> None:
        assert ProcessScaleUp.method_id == "chemistry-process@0.1.0"

    def test_instances_share_class_attrs(self) -> None:
        a = ProcessScaleUp()
        b = ProcessScaleUp()
        assert a.schema_version == b.schema_version
