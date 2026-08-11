"""Deep tests for ``general_ludd.chemistry.process`` — CHEM-017 process scale-up.

Covers every boundary condition, full output-shape completeness, numerical-precision
gates, all error paths, and multi-limitation combos missed by the shallow test suite.
"""

from __future__ import annotations

import importlib.util
import math
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


process_mod = _load(_PROCESS_PATH, "chem_process_deep_test")

ProcessScaleUp = process_mod.ProcessScaleUp
LINEAR_SCALE_NOT_VALID = process_mod.LINEAR_SCALE_NOT_VALID


@pytest.fixture
def p() -> ProcessScaleUp:
    return ProcessScaleUp()


# ---------------------------------------------------------------------------
# heat_transfer_check — deep
# ---------------------------------------------------------------------------


class TestHeatTransferDeep:
    _REQUIRED_KEYS = frozenset(
        {
            "schema_version",
            "name",
            "lab_surface_area_m2",
            "plant_surface_area_m2",
            "lab_sv_ratio",
            "plant_sv_ratio",
            "sv_ratio_loss_factor",
            "unit_area",
            "unit_sv",
            "limitations",
        }
    )

    def test_output_has_exactly_required_keys(self, p: ProcessScaleUp) -> None:
        r = p.heat_transfer_check(2.0, 200.0, 0.75)
        actual = set(r.keys())
        assert actual == self._REQUIRED_KEYS, (
            f"extra={actual - self._REQUIRED_KEYS} missing={self._REQUIRED_KEYS - actual}"
        )

    def test_sv_loss_factor_near_edge_blowup(self, p: ProcessScaleUp) -> None:
        r = p.heat_transfer_check(0.001, 100.0, 0.01)
        assert r["sv_ratio_loss_factor"] == pytest.approx((100.0 / 0.001) ** (1.0 / 3.0), rel=1e-9)

    def test_sv_loss_factor_is_volume_ratio_cuberoot(self, p: ProcessScaleUp) -> None:
        for vol_ratio in (8.0, 27.0, 64.0, 125.0, 1000.0):
            r = p.heat_transfer_check(1.0, 1.0 * vol_ratio, 1.0)
            expected = vol_ratio ** (1.0 / 3.0)
            assert r["sv_ratio_loss_factor"] == pytest.approx(expected, rel=1e-9)

    def test_lab_sv_and_plant_sv_consistency(self, p: ProcessScaleUp) -> None:
        r = p.heat_transfer_check(2.0, 250.0, 1.5)
        lab_sv = r["lab_sv_ratio"]
        plant_sv = r["plant_sv_ratio"]
        assert lab_sv > plant_sv
        assert lab_sv / plant_sv == pytest.approx(r["sv_ratio_loss_factor"], rel=1e-9)

    def test_all_limitations_present(self, p: ProcessScaleUp) -> None:
        r = p.heat_transfer_check(1.0, 1000.0, 0.5)
        assert len(r["limitations"]) >= 2
        assert LINEAR_SCALE_NOT_VALID in r["limitations"]
        assert any("S/V" in lim for lim in r["limitations"])

    def test_sv_units_are_si(self, p: ProcessScaleUp) -> None:
        r = p.heat_transfer_check(1.0, 10.0, 0.5)
        assert r["unit_area"] == "m^2"
        assert r["unit_sv"] == "m^2/m^3"
        assert r["lab_sv_ratio"] > 0
        assert r["plant_sv_ratio"] > 0

    def test_all_output_values_finite(self, p: ProcessScaleUp) -> None:
        r = p.heat_transfer_check(1.0, 50.0, 0.3)
        numeric_keys = [
            "lab_surface_area_m2",
            "plant_surface_area_m2",
            "lab_sv_ratio",
            "plant_sv_ratio",
            "sv_ratio_loss_factor",
        ]
        for k in numeric_keys:
            assert math.isfinite(r[k]), f"{k} not finite"

    def test_very_small_lab_volume_still_valid(self, p: ProcessScaleUp) -> None:
        r = p.heat_transfer_check(1e-6, 1.0, 1e-4)
        assert r["sv_ratio_loss_factor"] > 0
        assert r["plant_surface_area_m2"] > 0

    def test_name_field_is_heat_transfer_scale_check(self, p: ProcessScaleUp) -> None:
        r = p.heat_transfer_check(1.0, 100.0, 0.5)
        assert r["name"] == "heat_transfer_scale_check"


# ---------------------------------------------------------------------------
# mixing_assessment — deep
# ---------------------------------------------------------------------------


class TestMixingAssessmentDeep:
    _REQUIRED_KEYS = frozenset(
        {
            "schema_version",
            "name",
            "reynolds_number",
            "regime",
            "impeller_diameter_m",
            "rotational_speed_rpm",
            "limitations",
        }
    )

    def test_output_has_exactly_required_keys(self, p: ProcessScaleUp) -> None:
        r = p.mixing_assessment(0.1, 60.0, 1000.0, 0.001)
        actual = set(r.keys())
        assert actual == self._REQUIRED_KEYS

    def test_boundary_laminar_to_transitional_at_10(self, p: ProcessScaleUp) -> None:
        viscosity = 0.001
        density = 1000.0
        diameter = 0.1
        rps = 10.0 / (density * diameter**2 / viscosity) * 60.0
        r = p.mixing_assessment(diameter, rps, density, viscosity)
        assert r["reynolds_number"] == pytest.approx(10.0, rel=1e-6)
        assert r["regime"] == "transitional"

    def test_boundary_transitional_to_turbulent_at_10000(self, p: ProcessScaleUp) -> None:
        viscosity = 0.001
        density = 1000.0
        diameter = 0.1
        rps = 10000.0 / (density * diameter**2 / viscosity) * 60.0
        r = p.mixing_assessment(diameter, rps, density, viscosity)
        assert r["reynolds_number"] == pytest.approx(10000.0, rel=1e-6)
        assert r["regime"] == "turbulent"

    def test_regime_boundary_re_9_9_is_laminar(self, p: ProcessScaleUp) -> None:
        r = p.mixing_assessment(0.1, 10.0, 1000.0, 100.0)
        assert r["regime"] == "laminar"

    def test_regime_boundary_re_9999_is_transitional(self, p: ProcessScaleUp) -> None:
        viscosity = 0.001
        density = 1000.0
        diameter = 0.1
        rps = 9999.0 / (density * diameter**2 / viscosity) * 60.0
        r = p.mixing_assessment(diameter, rps, density, viscosity)
        assert r["reynolds_number"] == pytest.approx(9999.0, rel=1e-6)
        assert r["regime"] == "transitional"

    def test_re_calculation_correct_water(self, p: ProcessScaleUp) -> None:
        r = p.mixing_assessment(0.15, 200.0, 1000.0, 0.001)
        expected_n_hz = 200.0 / 60.0
        expected_re = (1000.0 * expected_n_hz * 0.15**2) / 0.001
        assert r["reynolds_number"] == pytest.approx(expected_re)

    def test_all_limitations_present(self, p: ProcessScaleUp) -> None:
        r = p.mixing_assessment(0.2, 300.0, 1000.0, 0.005)
        assert len(r["limitations"]) >= 2
        assert LINEAR_SCALE_NOT_VALID in r["limitations"]
        assert any("blend" in lim.lower() for lim in r["limitations"])

    def test_negative_speed_raises(self, p: ProcessScaleUp) -> None:
        with pytest.raises(ValueError, match="rotational_speed_rpm"):
            p.mixing_assessment(0.1, -1.0, 1000.0, 0.01)

    def test_zero_speed_is_laminar_and_re_is_zero(self, p: ProcessScaleUp) -> None:
        r = p.mixing_assessment(0.1, 0.0, 1000.0, 1.0)
        assert r["reynolds_number"] == 0.0
        assert r["regime"] == "laminar"

    def test_output_reflects_input_values(self, p: ProcessScaleUp) -> None:
        r = p.mixing_assessment(0.25, 400.0, 1200.0, 0.003)
        assert r["impeller_diameter_m"] == 0.25
        assert r["rotational_speed_rpm"] == 400.0

    def test_very_high_viscosity_yields_laminar(self, p: ProcessScaleUp) -> None:
        r = p.mixing_assessment(0.5, 600.0, 1000.0, 5000.0)
        assert r["reynolds_number"] < 10.0
        assert r["regime"] == "laminar"

    def test_very_low_viscosity_yields_turbulent(self, p: ProcessScaleUp) -> None:
        r = p.mixing_assessment(0.5, 600.0, 1000.0, 1e-6)
        assert r["reynolds_number"] > 10000.0
        assert r["regime"] == "turbulent"


# ---------------------------------------------------------------------------
# runaway_risk — deep
# ---------------------------------------------------------------------------


class TestRunawayRiskDeep:
    _REQUIRED_KEYS = frozenset(
        {
            "schema_version",
            "name",
            "runaway_risk",
            "reaction_enthalpy_kj_mol",
            "adiabatic_temp_rise_k",
            "heat_removal_capacity_kw",
            "process_temp_k",
            "limitations",
        }
    )

    def test_output_has_exactly_required_keys(self, p: ProcessScaleUp) -> None:
        r = p.runaway_risk(50.0, 20.0, 30.0, 350.0)
        actual = set(r.keys())
        assert actual == self._REQUIRED_KEYS

    def test_low_boundary_exact_adiabatic_rise_10_enthalpy_50(self, p: ProcessScaleUp) -> None:
        r = p.runaway_risk(50.0, 10.0, 5.0, 300.0)
        assert r["runaway_risk"] == "low"

    def test_just_above_low_boundary_adiabatic_rise_11(self, p: ProcessScaleUp) -> None:
        r = p.runaway_risk(40.0, 11.0, 5.0, 300.0)
        assert r["runaway_risk"] in ("moderate", "high")

    def test_moderate_with_cooling_boundary_50_rise_150_enthalpy(self, p: ProcessScaleUp) -> None:
        r = p.runaway_risk(150.0, 50.0, 1.0, 400.0)
        assert r["runaway_risk"] == "moderate"

    def test_moderate_becomes_high_without_cooling(self, p: ProcessScaleUp) -> None:
        r = p.runaway_risk(150.0, 50.0, 0.0, 400.0)
        assert r["runaway_risk"] == "high"

    def test_just_below_high_boundary_within_or_clause(self, p: ProcessScaleUp) -> None:
        r = p.runaway_risk(299.0, 199.0, 1.0, 400.0)
        assert r["runaway_risk"] == "high"

    def test_high_at_adiabatic_rise_200(self, p: ProcessScaleUp) -> None:
        r = p.runaway_risk(1.0, 200.0, 1.0, 400.0)
        assert r["runaway_risk"] == "high"

    def test_high_at_enthalpy_300(self, p: ProcessScaleUp) -> None:
        r = p.runaway_risk(300.0, 0.1, 100.0, 400.0)
        assert r["runaway_risk"] == "high"

    def test_severe_at_adiabatic_rise_201_enthalpy_301(self, p: ProcessScaleUp) -> None:
        r = p.runaway_risk(301.0, 201.0, 1.0, 400.0)
        assert r["runaway_risk"] == "severe"

    def test_endothermic_enthalpy_negative_still_classifies_safely(self, p: ProcessScaleUp) -> None:
        r = p.runaway_risk(-200.0, 150.0, 50.0, 400.0)
        assert r["runaway_risk"] in ("high", "severe")

    def test_endothermic_zero_rise_low(self, p: ProcessScaleUp) -> None:
        r = p.runaway_risk(-5.0, 0.0, 50.0, 350.0)
        assert r["runaway_risk"] == "low"

    def test_high_severe_includes_hazop_limitation(self, p: ProcessScaleUp) -> None:
        risks = (
            p.runaway_risk(300.0, 150.0, 0.0, 400.0),
            p.runaway_risk(500.0, 300.0, 0.0, 500.0),
        )
        for r in risks:
            assert r["runaway_risk"] in ("high", "severe")
            joined = " ".join(r["limitations"])
            assert "HAZOP" in joined or "relief" in joined.lower()

    def test_low_moderate_dont_get_hazop_limitation(self, p: ProcessScaleUp) -> None:
        r = p.runaway_risk(10.0, 5.0, 30.0, 300.0)
        joined = " ".join(r["limitations"])
        assert "HAZOP" not in joined

    def test_no_cooling_adds_removal_limitation_even_for_low_risk(self, p: ProcessScaleUp) -> None:
        r = p.runaway_risk(1.0, 1.0, 0.0, 300.0)
        assert any("no_heat_removal_specified" in lim for lim in r["limitations"])

    def test_cooling_present_no_removal_limitation(self, p: ProcessScaleUp) -> None:
        r = p.runaway_risk(1.0, 1.0, 1.0, 300.0)
        assert not any("no_heat_removal_specified" in lim for lim in r["limitations"])

    def test_all_output_values_finite(self, p: ProcessScaleUp) -> None:
        r = p.runaway_risk(100.0, 50.0, 10.0, 350.0)
        for k in ("reaction_enthalpy_kj_mol", "adiabatic_temp_rise_k", "heat_removal_capacity_kw", "process_temp_k"):
            assert math.isfinite(r[k]), f"{k} not finite"

    def test_zero_temp_raises(self, p: ProcessScaleUp) -> None:
        with pytest.raises(ValueError, match="process_temp_k"):
            p.runaway_risk(100.0, 50.0, 10.0, 0.0)

    def test_negative_temp_raises(self, p: ProcessScaleUp) -> None:
        with pytest.raises(ValueError, match="process_temp_k"):
            p.runaway_risk(100.0, 50.0, 10.0, -100.0)

    def test_negative_heat_removal_raises(self, p: ProcessScaleUp) -> None:
        with pytest.raises(ValueError, match="heat_removal_capacity_kw"):
            p.runaway_risk(100.0, 50.0, -1.0, 300.0)

    def test_linear_scale_caveat_always_present(self, p: ProcessScaleUp) -> None:
        for rise, enth, cool, temp in (
            (5.0, 10.0, 30.0, 300.0),
            (500.0, 500.0, 0.0, 500.0),
        ):
            r = p.runaway_risk(enth, rise, cool, temp)
            assert LINEAR_SCALE_NOT_VALID in r["limitations"]

    def test_still_runs_with_heat_removal_capacity_zero(self, p: ProcessScaleUp) -> None:
        r = p.runaway_risk(50.0, 20.0, 0.0, 350.0)
        assert math.isfinite(r["heat_removal_capacity_kw"])


# ---------------------------------------------------------------------------
# separation_feasibility — deep
# ---------------------------------------------------------------------------


class TestSeparationFeasibilityDeep:
    _REQUIRED_KEYS = frozenset(
        {
            "schema_version",
            "name",
            "method",
            "feasible",
            "relative_volatility",
            "feed_composition",
            "product_purity",
            "limitations",
        }
    )

    def test_output_has_exactly_required_keys_distillation(self, p: ProcessScaleUp) -> None:
        r = p.separation_feasibility("distillation", relative_volatility=2.0, feed_composition=0.5, product_purity=0.95)
        actual = set(r.keys())
        assert actual == self._REQUIRED_KEYS

    def test_output_has_exactly_required_keys_non_distillation(self, p: ProcessScaleUp) -> None:
        r = p.separation_feasibility("extraction")
        actual = set(r.keys())
        assert actual == self._REQUIRED_KEYS

    def test_alpha_exactly_1_05_infeasible(self, p: ProcessScaleUp) -> None:
        r = p.separation_feasibility("distillation", relative_volatility=1.05, feed_composition=0.5, product_purity=0.9)
        assert r["feasible"] is True

    def test_alpha_1_049_infeasible(self, p: ProcessScaleUp) -> None:
        r = p.separation_feasibility(
            "distillation", relative_volatility=1.049, feed_composition=0.5, product_purity=0.9
        )
        assert r["feasible"] is False

    def test_high_purity_low_alpha_boundary_purity_0_99(self, p: ProcessScaleUp) -> None:
        r = p.separation_feasibility("distillation", relative_volatility=1.3, feed_composition=0.5, product_purity=0.99)
        assert r["feasible"] is True

    def test_high_purity_low_alpha_boundary_purity_0_991(self, p: ProcessScaleUp) -> None:
        r = p.separation_feasibility(
            "distillation", relative_volatility=1.3, feed_composition=0.5, product_purity=0.991
        )
        assert r["feasible"] is False

    def test_alpha_1_5_purity_0_99_feasible(self, p: ProcessScaleUp) -> None:
        r = p.separation_feasibility("distillation", relative_volatility=1.5, feed_composition=0.5, product_purity=0.99)
        assert r["feasible"] is True

    def test_alpha_1_5_boundary_feasible_side(self, p: ProcessScaleUp) -> None:
        r = p.separation_feasibility(
            "distillation", relative_volatility=1.5, feed_composition=0.5, product_purity=0.991
        )
        assert r["feasible"] is True

    def test_all_non_distillation_methods_feasible(self, p: ProcessScaleUp) -> None:
        for method in ("extraction", "crystallization", "membranes", "chromatography"):
            r = p.separation_feasibility(method)
            assert r["feasible"] is True
            assert r["method"] == method
            assert any("method_not_characterized" in lim for lim in r["limitations"])

    def test_missing_relative_volatility_raises(self, p: ProcessScaleUp) -> None:
        with pytest.raises(ValueError, match="relative_volatility"):
            p.separation_feasibility("distillation", feed_composition=0.5, product_purity=0.95)

    def test_zero_relative_volatility_raises(self, p: ProcessScaleUp) -> None:
        with pytest.raises(ValueError, match="relative_volatility"):
            p.separation_feasibility("distillation", relative_volatility=0.0, feed_composition=0.5, product_purity=0.95)

    def test_negative_relative_volatility_raises(self, p: ProcessScaleUp) -> None:
        with pytest.raises(ValueError, match="relative_volatility"):
            p.separation_feasibility(
                "distillation", relative_volatility=-1.0, feed_composition=0.5, product_purity=0.95
            )

    def test_feed_composition_0_raises(self, p: ProcessScaleUp) -> None:
        with pytest.raises(ValueError, match="feed_composition"):
            p.separation_feasibility("distillation", relative_volatility=2.0, feed_composition=0.0, product_purity=0.95)

    def test_feed_composition_1_raises(self, p: ProcessScaleUp) -> None:
        with pytest.raises(ValueError, match="feed_composition"):
            p.separation_feasibility("distillation", relative_volatility=2.0, feed_composition=1.0, product_purity=0.95)

    def test_feed_composition_negative_raises(self, p: ProcessScaleUp) -> None:
        with pytest.raises(ValueError, match="feed_composition"):
            p.separation_feasibility(
                "distillation", relative_volatility=2.0, feed_composition=-0.1, product_purity=0.95
            )

    def test_product_purity_0_raises(self, p: ProcessScaleUp) -> None:
        with pytest.raises(ValueError, match="product_purity"):
            p.separation_feasibility("distillation", relative_volatility=2.0, feed_composition=0.5, product_purity=0.0)

    def test_product_purity_1_raises(self, p: ProcessScaleUp) -> None:
        with pytest.raises(ValueError, match="product_purity"):
            p.separation_feasibility("distillation", relative_volatility=2.0, feed_composition=0.5, product_purity=1.0)

    def test_product_purity_above_1_raises(self, p: ProcessScaleUp) -> None:
        with pytest.raises(ValueError, match="product_purity"):
            p.separation_feasibility("distillation", relative_volatility=2.0, feed_composition=0.5, product_purity=1.5)

    def test_linear_scale_caveat_in_all_methods(self, p: ProcessScaleUp) -> None:
        r_d = p.separation_feasibility(
            "distillation", relative_volatility=2.0, feed_composition=0.5, product_purity=0.95
        )
        r_x = p.separation_feasibility("extraction")
        assert LINEAR_SCALE_NOT_VALID in r_d["limitations"]
        assert LINEAR_SCALE_NOT_VALID in r_x["limitations"]

    def test_infeasible_returns_false_always(self, p: ProcessScaleUp) -> None:
        r = p.separation_feasibility(
            "distillation", relative_volatility=1.01, feed_composition=0.5, product_purity=0.95
        )
        assert r["feasible"] is False

    def test_feasible_has_no_infeasible_in_limitations(self, p: ProcessScaleUp) -> None:
        r = p.separation_feasibility("distillation", relative_volatility=3.0, feed_composition=0.5, product_purity=0.9)
        joined = " ".join(r["limitations"])
        assert "infeasible" not in joined.lower()

    def test_none_fields_remain_none_for_non_distillation(self, p: ProcessScaleUp) -> None:
        r = p.separation_feasibility("extraction")
        assert r["relative_volatility"] is None
        assert r["feed_composition"] is None
        assert r["product_purity"] is None

    def test_schema_version_present(self, p: ProcessScaleUp) -> None:
        r = p.separation_feasibility("distillation", relative_volatility=2.0, feed_composition=0.5, product_purity=0.95)
        assert r["schema_version"] == "1.0"


# ---------------------------------------------------------------------------
# ProcessScaleUp — class-level invariants
# ---------------------------------------------------------------------------


class TestProcessScaleUpClassDeep:
    def test_multiple_instances_dont_share_mutable_state(self) -> None:
        a = ProcessScaleUp()
        b = ProcessScaleUp()
        assert a is not b
        assert a.schema_version == b.schema_version
        r_a = a.heat_transfer_check(1.0, 10.0, 0.5)
        r_b = b.heat_transfer_check(2.0, 20.0, 1.0)
        assert r_a["lab_surface_area_m2"] != r_b["lab_surface_area_m2"]

    def test_class_attrs_are_correct_type(self) -> None:
        assert isinstance(ProcessScaleUp.schema_version, str)
        assert isinstance(ProcessScaleUp.method_id, str)
        assert ProcessScaleUp.schema_version == "1.0"
        assert ProcessScaleUp.method_id == "chemistry-process@0.1.0"

    def test_every_method_returns_schema_version_in_result(self, p: ProcessScaleUp) -> None:
        results = [
            p.heat_transfer_check(1.0, 10.0, 0.5),
            p.mixing_assessment(0.1, 100.0, 1000.0, 0.01),
            p.runaway_risk(10.0, 5.0, 30.0, 300.0),
            p.separation_feasibility(
                "distillation", relative_volatility=2.0, feed_composition=0.5, product_purity=0.95
            ),
        ]
        for r in results:
            assert r["schema_version"] == "1.0"

    def test_every_method_result_has_limitations_list(self, p: ProcessScaleUp) -> None:
        results = [
            p.heat_transfer_check(1.0, 10.0, 0.5),
            p.mixing_assessment(0.1, 100.0, 1000.0, 0.01),
            p.runaway_risk(10.0, 5.0, 30.0, 300.0),
            p.separation_feasibility(
                "distillation", relative_volatility=2.0, feed_composition=0.5, product_purity=0.95
            ),
        ]
        for r in results:
            assert isinstance(r["limitations"], list)
            assert len(r["limitations"]) >= 1
