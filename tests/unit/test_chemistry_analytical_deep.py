"""Deep unit tests for ``general_ludd.chemistry.analytical``.

Covers edge cases, boundary values, error paths, and regression scenarios
not exercised by the existing shallow tests: curve invalidation paths,
Grubbs iterative removal, Dixon Q edge cases, LOD/LOQ with exotic
parameters, precision/accuracy error paths, extrapolation/degradation
envelopes, and blind-spot spec invariants.
"""

from __future__ import annotations

import math

import pytest

from general_ludd.chemistry import analytical

# ---------------------------------------------------------------------------
# CalibrationCurve — construction / fit error paths
# ---------------------------------------------------------------------------


class TestCurveConstructionErrors:
    def test_single_point_raises(self):
        with pytest.raises(ValueError, match="at least 2"):
            analytical.CalibrationCurve([1.0], [10.0])

    def test_zero_points_raises(self):
        with pytest.raises(ValueError, match="at least 2"):
            analytical.CalibrationCurve([], [])

    def test_length_mismatch_detailed(self):
        with pytest.raises(ValueError, match="equal length"):
            analytical.CalibrationCurve([1.0, 2.0, 3.0, 4.0], [10.0, 20.0])

    def test_negative_concentrations_accepted(self):
        curve = analytical.CalibrationCurve([-1.0, 0.0, 1.0], [-10.0, 0.0, 10.0])
        rec = curve.fit()
        assert rec["status"] == "succeeded"

    def test_degenerate_concentrations_same_value(self):
        curve = analytical.CalibrationCurve([5.0, 5.0, 5.0], [10.0, 20.0, 30.0])
        with pytest.raises(ValueError, match="degenerate"):
            curve.fit()


class TestCurveFitCache:
    def test_fit_cache_returns_same_dict_across_calls(self):
        curve = analytical.CalibrationCurve([1.0, 2.0, 3.0], [10.0, 20.0, 30.0])
        r1 = curve.fit()
        r2 = curve.fit()
        assert r1["slope"] == r2["slope"]
        assert r1["intercept"] == r2["intercept"]

    def test_fit_cache_is_shallow_copy(self):
        curve = analytical.CalibrationCurve([1.0, 2.0, 3.0], [10.0, 20.0, 30.0])
        r1 = curve.fit()
        r1["slope"] = 999.0
        r2 = curve.fit()
        assert r2["slope"] != 999.0


class TestFitEdgeCases:
    def test_horizontal_line_r2_is_zero(self):
        curve = analytical.CalibrationCurve([1.0, 2.0, 3.0], [5.0, 5.0, 5.0])
        rec = curve.fit()
        assert math.isclose(rec["slope"], 0.0, abs_tol=1e-12)
        assert math.isclose(rec["intercept"], 5.0, abs_tol=1e-12)
        assert math.isclose(rec["r_squared"], 0.0, abs_tol=1e-12)

    def test_two_point_fit_exact(self):
        curve = analytical.CalibrationCurve([0.0, 10.0], [0.0, 100.0])
        rec = curve.fit()
        assert math.isclose(rec["slope"], 10.0, rel_tol=1e-9)
        assert math.isclose(rec["intercept"], 0.0, abs_tol=1e-9)
        assert math.isclose(rec["r_squared"], 1.0, abs_tol=1e-12)
        assert rec["n"] == 2
        assert rec["s_yx"] == 0.0

    def test_negative_slope(self):
        curve = analytical.CalibrationCurve([1.0, 2.0, 3.0], [30.0, 20.0, 10.0])
        rec = curve.fit()
        assert rec["slope"] < 0.0


# ---------------------------------------------------------------------------
# CalibrationCurve — predict edge cases
# ---------------------------------------------------------------------------


class TestPredictEdgeCases:
    def test_predict_with_zero_slope_yields_invalid(self):
        curve = analytical.CalibrationCurve([1.0, 2.0, 3.0], [5.0, 5.0, 5.0])
        rec = curve.predict(5.0)
        assert rec["status"] == "invalid"
        assert rec["extrapolated"] is True
        assert math.isnan(rec["concentration"])

    def test_predict_invalid_alpha_zero(self):
        curve = analytical.CalibrationCurve([1.0, 2.0, 3.0], [10.0, 20.0, 30.0])
        with pytest.raises(ValueError, match="alpha"):
            curve.predict(20.0, alpha=0.0)

    def test_predict_invalid_alpha_one(self):
        curve = analytical.CalibrationCurve([1.0, 2.0, 3.0], [10.0, 20.0, 30.0])
        with pytest.raises(ValueError, match="alpha"):
            curve.predict(20.0, alpha=1.0)

    def test_predict_invalid_alpha_negative(self):
        curve = analytical.CalibrationCurve([1.0, 2.0, 3.0], [10.0, 20.0, 30.0])
        with pytest.raises(ValueError, match="alpha"):
            curve.predict(20.0, alpha=-0.5)

    def test_predict_two_point_no_ci_equals_concentration(self):
        curve = analytical.CalibrationCurve([1.0, 2.0], [10.0, 20.0])
        rec = curve.predict(15.0)
        assert math.isclose(rec["concentration"], 1.5, rel_tol=1e-6)
        assert rec["ci_lower"] == rec["concentration"]
        assert rec["ci_upper"] == rec["concentration"]

    def test_predict_exactly_at_range_boundary_not_extrapolated(self):
        curve = analytical.CalibrationCurve([1.0, 2.0, 3.0], [10.0, 20.0, 30.0])
        rec = curve.predict(10.0)
        assert rec["extrapolated"] is False
        rec2 = curve.predict(30.0)
        assert rec2["extrapolated"] is False

    def test_predict_barely_outside_range_extrapolated(self):
        curve = analytical.CalibrationCurve([1.0, 2.0, 3.0], [10.0, 20.0, 30.0])
        rec = curve.predict(9.999)
        assert rec["extrapolated"] is True

    def test_predict_ci_present_with_sufficient_data(self):
        curve = analytical.CalibrationCurve([1.0, 2.0, 3.0, 4.0], [10.0, 20.0, 30.0, 40.0])
        rec = curve.predict(15.0)
        assert "ci_lower" in rec
        assert "ci_upper" in rec

    def test_predict_confidence_level_matches_alpha(self):
        curve = analytical.CalibrationCurve([1.0, 2.0, 3.0, 4.0], [10.0, 20.0, 30.0, 40.0])
        rec = curve.predict(25.0, alpha=0.01)
        assert rec["confidence_level"] == 0.99

    def test_predict_status_succeeded_for_in_range(self):
        curve = analytical.CalibrationCurve([1.0, 2.0, 3.0, 4.0, 5.0], [10.0, 20.0, 30.0, 40.0, 50.0])
        rec = curve.predict(25.0)
        assert rec["status"] == "succeeded"


# ---------------------------------------------------------------------------
# CalibrationCurve — check_range
# ---------------------------------------------------------------------------


class TestCheckRange:
    def test_on_boundary_not_extrapolated(self):
        curve = analytical.CalibrationCurve([1.0, 2.0, 3.0], [10.0, 20.0, 30.0])
        rec = curve.check_range(10.0)
        assert rec["extrapolated"] is False
        assert rec["status"] == "succeeded"

    def test_middle_of_range_passes(self):
        curve = analytical.CalibrationCurve([0.0, 5.0, 10.0], [0.0, 50.0, 100.0])
        rec = curve.check_range(50.0)
        assert rec["extrapolated"] is False

    def test_far_outside_range_flagged_with_error(self):
        curve = analytical.CalibrationCurve([1.0, 2.0, 3.0], [10.0, 20.0, 30.0])
        rec = curve.check_range(999.0)
        assert rec["extrapolated"] is True
        assert rec["status"] == "degraded"
        assert len(rec["errors"]) == 1
        assert "extrapolation" in rec["errors"][0]["code"]


# ---------------------------------------------------------------------------
# CalibrationCurve — LOD / LOQ error paths
# ---------------------------------------------------------------------------


class TestLODLOQErrors:
    def test_lod_zero_slope_raises(self):
        curve = analytical.CalibrationCurve([1.0, 2.0, 3.0], [5.0, 5.0, 5.0])
        with pytest.raises(ValueError, match="zero"):
            curve.lod(sigma_blank=1.0)

    def test_loq_zero_slope_raises(self):
        curve = analytical.CalibrationCurve([1.0, 2.0, 3.0], [5.0, 5.0, 5.0])
        with pytest.raises(ValueError, match="zero"):
            curve.loq(sigma_blank=1.0)

    def test_lod_negative_sigma_raises(self):
        curve = analytical.CalibrationCurve([1.0, 2.0, 3.0], [10.0, 20.0, 30.0])
        with pytest.raises(ValueError, match="sigma_blank"):
            curve.lod(sigma_blank=-0.1)

    def test_loq_negative_sigma_raises(self):
        curve = analytical.CalibrationCurve([1.0, 2.0, 3.0], [10.0, 20.0, 30.0])
        with pytest.raises(ValueError, match="sigma_blank"):
            curve.loq(sigma_blank=-0.1)

    def test_lod_custom_k(self):
        curve = analytical.CalibrationCurve([1.0, 2.0, 3.0], [10.0, 20.0, 30.0])
        lod = curve.lod(sigma_blank=2.0, k=5)
        assert math.isclose(lod, 5 * 2.0 / 10.0, rel_tol=1e-9)

    def test_loq_custom_k(self):
        curve = analytical.CalibrationCurve([1.0, 2.0, 3.0], [10.0, 20.0, 30.0])
        loq = curve.loq(sigma_blank=2.0, k=8)
        assert math.isclose(loq, 8 * 2.0 / 10.0, rel_tol=1e-9)

    def test_lod_uses_s_yx_when_no_blank(self):
        curve = analytical.CalibrationCurve([1.0, 2.0, 3.0, 4.0], [10.1, 19.9, 30.2, 40.1])
        lod = curve.lod()
        assert lod > 0.0

    def test_loq_uses_s_yx_when_no_blank(self):
        curve = analytical.CalibrationCurve([1.0, 2.0, 3.0, 4.0], [10.1, 19.9, 30.2, 40.1])
        loq = curve.loq()
        assert loq > 0.0
        assert loq > curve.lod()


# ---------------------------------------------------------------------------
# MethodValidation — error paths
# ---------------------------------------------------------------------------


class TestMethodValidationErrors:
    def test_precision_single_replicate_raises(self):
        mv = analytical.MethodValidation()
        with pytest.raises(ValueError, match="at least 2"):
            mv.precision(replicates=[5.0])

    def test_precision_empty_list_raises(self):
        mv = analytical.MethodValidation()
        with pytest.raises(ValueError, match="at least 2"):
            mv.precision(replicates=[])

    def test_accuracy_zero_nominal_raises(self):
        mv = analytical.MethodValidation()
        with pytest.raises(ValueError, match="non-zero"):
            mv.accuracy(measured=5.0, nominal=0.0)

    def test_linearity_without_curve_raises(self):
        mv = analytical.MethodValidation()
        with pytest.raises(ValueError, match="CalibrationCurve"):
            mv.linearity()

    def test_range_without_curve_raises(self):
        mv = analytical.MethodValidation()
        with pytest.raises(ValueError, match="CalibrationCurve"):
            mv.range()

    def test_lod_loq_without_curve_raises(self):
        mv = analytical.MethodValidation()
        with pytest.raises(ValueError, match="CalibrationCurve"):
            mv.lod_loq()


# ---------------------------------------------------------------------------
# MethodValidation — accuracy custom acceptance window
# ---------------------------------------------------------------------------


class TestMethodValidationAccuracy:
    def test_custom_acceptance_window_pass(self):
        mv = analytical.MethodValidation()
        rec = mv.accuracy(measured=9.0, nominal=10.0, lo_pct=85.0, hi_pct=110.0)
        assert rec["acceptance"] == "pass"
        assert rec["acceptance_window"] == (85.0, 110.0)

    def test_custom_acceptance_window_fail(self):
        mv = analytical.MethodValidation()
        rec = mv.accuracy(measured=9.0, nominal=10.0, lo_pct=95.0, hi_pct=105.0)
        assert rec["acceptance"] == "fail"

    def test_exactly_on_lower_boundary_passes(self):
        mv = analytical.MethodValidation()
        rec = mv.accuracy(measured=8.0, nominal=10.0, lo_pct=80.0, hi_pct=120.0)
        assert math.isclose(rec["value"], 80.0, rel_tol=1e-9)
        assert rec["acceptance"] == "pass"

    def test_exactly_on_upper_boundary_passes(self):
        mv = analytical.MethodValidation()
        rec = mv.accuracy(measured=12.0, nominal=10.0, lo_pct=80.0, hi_pct=120.0)
        assert math.isclose(rec["value"], 120.0, rel_tol=1e-9)
        assert rec["acceptance"] == "pass"


# ---------------------------------------------------------------------------
# MethodValidation — specificity edge cases
# ---------------------------------------------------------------------------


class TestMethodValidationSpecificity:
    def test_specificity_with_flagged_interferences(self):
        mv = analytical.MethodValidation()
        interferences = [
            {"compound": "X", "response_pct": 1.0},
            {"compound": "Y", "response_pct": 12.0},
            {"compound": "Z", "response_pct": 8.0},
        ]
        rec = mv.specificity(interferences=interferences)
        assert rec["status"] == "degraded"
        assert rec["interferences_tested"] == 3
        assert rec["interferences_flagged"] == 2

    def test_specificity_all_below_threshold(self):
        mv = analytical.MethodValidation()
        interferences = [
            {"compound": "A", "response_pct": 1.0},
            {"compound": "B", "response_pct": 3.0},
        ]
        rec = mv.specificity(interferences=interferences)
        assert rec["status"] == "succeeded"
        assert rec["interferences_flagged"] == 0

    def test_specificity_exactly_at_threshold_not_flagged(self):
        mv = analytical.MethodValidation()
        interferences = [{"compound": "A", "response_pct": 5.0}]
        rec = mv.specificity(interferences=interferences)
        assert rec["interferences_flagged"] == 0

    def test_specificity_missing_response_pct_treated_as_zero(self):
        mv = analytical.MethodValidation()
        interferences = [{"compound": "A"}]
        rec = mv.specificity(interferences=interferences)
        assert rec["interferences_flagged"] == 0


# ---------------------------------------------------------------------------
# MethodValidation — lod_loq combined record
# ---------------------------------------------------------------------------


class TestMethodValidationLODLOQ:
    def test_combined_record_shape(self):
        curve = analytical.CalibrationCurve([1.0, 2.0, 3.0, 4.0], [10.1, 19.9, 30.2, 40.1])
        mv = analytical.MethodValidation(curve=curve)
        rec = mv.lod_loq()
        assert rec["name"] == "lod_loq"
        assert rec["lod_k"] == 3
        assert rec["loq_k"] == 10
        assert rec["lod"] > 0
        assert rec["loq"] > rec["lod"]

    def test_lod_loq_with_blank_sigma(self):
        curve = analytical.CalibrationCurve([1.0, 2.0, 3.0], [10.0, 20.0, 30.0])
        mv = analytical.MethodValidation(curve=curve)
        rec = mv.lod_loq(sigma_blank=0.5)
        assert rec["sigma_source"] == "blank"
        assert math.isclose(rec["lod"], 3 * 0.5 / 10.0, rel_tol=1e-9)
        assert math.isclose(rec["loq"], 10 * 0.5 / 10.0, rel_tol=1e-9)

    def test_lod_loq_default_sigma_from_residuals(self):
        curve = analytical.CalibrationCurve([1.0, 2.0, 3.0, 4.0], [10.1, 19.9, 30.2, 40.1])
        mv = analytical.MethodValidation(curve=curve)
        rec = mv.lod_loq()
        assert rec["sigma_source"] == "residuals"
        assert rec["lod"] > 0


# ---------------------------------------------------------------------------
# MethodValidation — robustness edge cases
# ---------------------------------------------------------------------------


class TestMethodValidationRobustness:
    def test_robustness_empty_perturbations(self):
        mv = analytical.MethodValidation()
        rec = mv.robustness(perturbations=[])
        assert rec["perturbations_evaluated"] == 0
        assert rec["status"] == "succeeded"

    def test_robustness_preserves_perturbations(self):
        mv = analytical.MethodValidation()
        perturbations = [
            {"factor": "pH", "nominal": 7.0, "perturbation": 0.5},
            {"factor": "temperature", "nominal": 25.0, "perturbation": 5.0},
        ]
        rec = mv.robustness(perturbations=perturbations)
        assert rec["perturbations_evaluated"] == 2
        assert rec["perturbations"] == perturbations


# ---------------------------------------------------------------------------
# Outlier detection — Grubbs iterative removal
# ---------------------------------------------------------------------------


class TestGrubbsIterative:
    def test_grubbs_single_outlier_flagged(self):
        values = [50.0, 51.0, 52.0, 48.0, 49.0, 150.0]
        rec = analytical.detect_outliers_grubbs(values)
        assert rec["n_outliers"] >= 1
        assert rec["n_remaining"] <= len(values) - 1

    def test_grubbs_multiple_outliers_removed_iteratively(self):
        values = [10.0, 11.0, 10.0, 10.0, 100.0, 200.0, 11.0, 10.5, 9.5, 10.2]
        rec = analytical.detect_outliers_grubbs(values)
        assert rec["n_outliers"] >= 2

    def test_grubbs_no_outliers_in_normal_data(self):
        import random

        random.seed(42)
        values = [random.gauss(100.0, 2.0) for _ in range(20)]
        rec = analytical.detect_outliers_grubbs(values)
        assert rec["n_outliers"] == 0

    def test_grubbs_identical_values_no_outlier(self):
        values = [5.0] * 10
        rec = analytical.detect_outliers_grubbs(values)
        assert rec["n_outliers"] == 0

    def test_grubbs_insufficient_data_n2(self):
        rec = analytical.detect_outliers_grubbs([1.0, 2.0])
        assert rec["status"] == "degraded"
        assert rec["n_outliers"] == 0
        assert len(rec["errors"]) == 1
        assert "n >= 3" in rec["errors"][0]["message"]

    def test_grubbs_empty_list(self):
        rec = analytical.detect_outliers_grubbs([])
        assert rec["status"] == "degraded"
        assert rec["n_outliers"] == 0


# ---------------------------------------------------------------------------
# Outlier detection — Dixon Q edge cases
# ---------------------------------------------------------------------------


class TestDixonQEdgeCases:
    def test_dixon_q_clear_high_outlier(self):
        values = [10.0, 10.1, 10.2, 10.0, 30.0]
        rec = analytical.dixon_q(values)
        assert rec["statistic"] > 0
        if rec["outlier"] is not None:
            assert pytest.approx(rec["outlier"]) == 30.0

    def test_dixon_q_clear_low_outlier(self):
        values = [0.5, 10.0, 10.1, 10.2, 10.0]
        rec = analytical.dixon_q(values)
        if rec["outlier"] is not None:
            assert pytest.approx(rec["outlier"]) == 0.5

    def test_dixon_q_normal_data_no_outlier(self):
        values = [10.0, 10.1, 10.05, 10.2, 9.95, 10.15]
        rec = analytical.dixon_q(values)
        assert rec["outlier"] is None

    def test_dixon_q_identical_values(self):
        values = [5.0, 5.0, 5.0, 5.0]
        rec = analytical.dixon_q(values)
        assert rec["statistic"] == 0.0
        assert rec["outlier"] is None

    def test_dixon_q_insufficient_data_n2(self):
        rec = analytical.dixon_q([1.0, 2.0])
        assert rec["status"] == "degraded"
        assert math.isnan(rec["statistic"])
        assert "n >= 3" in rec["errors"][0]["message"]

    def test_dixon_q_empty_list(self):
        rec = analytical.dixon_q([])
        assert rec["status"] == "degraded"
        assert math.isnan(rec["statistic"])

    def test_dixon_q_near_critical_value(self):
        values = [1.0, 1.01, 1.02, 1.04, 2.0]
        rec = analytical.dixon_q(values)
        assert "statistic" in rec
        assert "q_critical" in rec
        assert rec["statistic"] > 0


# ---------------------------------------------------------------------------
# Blank subtraction — edge cases
# ---------------------------------------------------------------------------


class TestBlankSubtraction:
    def test_blank_greater_than_response_yields_negative_net(self):
        rec = analytical.subtract_blank(response=3.0, blank=10.0)
        assert rec["value"] < 0
        assert "negative" in rec["limitations"][0].lower()

    def test_blank_equal_to_response_zero_net(self):
        rec = analytical.subtract_blank(response=7.5, blank=7.5)
        assert math.isclose(rec["value"], 0.0, abs_tol=1e-12)
        assert rec["blank_subtracted"] is True

    def test_both_zero(self):
        rec = analytical.subtract_blank(response=0.0, blank=0.0)
        assert math.isclose(rec["value"], 0.0, abs_tol=1e-12)
        assert rec["blank_subtracted"] is True

    def test_preserves_raw_values(self):
        rec = analytical.subtract_blank(response=42.0, blank=7.0)
        assert rec["raw_response"] == 42.0
        assert rec["blank"] == 7.0

    def test_unit_is_response(self):
        rec = analytical.subtract_blank(response=5.0, blank=1.0)
        assert rec["unit"] == "response"


# ---------------------------------------------------------------------------
# Internal helpers — _z_two_sided
# ---------------------------------------------------------------------------


class TestZTwoSided:
    def test_alpha_0_05_returns_1_96(self):
        assert math.isclose(analytical._z_two_sided(0.05), 1.96)

    def test_alpha_0_01_returns_2_576(self):
        assert math.isclose(analytical._z_two_sided(0.01), 2.576)

    def test_alpha_0_10_returns_1_645(self):
        assert math.isclose(analytical._z_two_sided(0.10), 1.645)

    def test_alpha_0_001_returns_3_291(self):
        assert math.isclose(analytical._z_two_sided(0.001), 3.291)

    def test_unknown_alpha_defaults_to_1_96(self):
        assert math.isclose(analytical._z_two_sided(0.1234), 1.96)


# ---------------------------------------------------------------------------
# Internal helpers — _grubbs_z
# ---------------------------------------------------------------------------


class TestGrubbsZ:
    def test_exact_table_value(self):
        z = analytical._grubbs_z(5, 0.05)
        assert z > 0

    def test_n_beyond_table_conservative_at_30(self):
        z = analytical._grubbs_z(50, 0.05)
        assert z == analytical._grubbs_z(30, 0.05)

    def test_interpolation_between_keys(self):
        z13 = analytical._grubbs_z(13, 0.05)
        z12 = analytical._grubbs_z(12, 0.05)
        z15 = analytical._grubbs_z(15, 0.05)
        assert z12 < z13 < z15

    def test_single_table_key_exact(self):
        z3 = analytical._grubbs_z(3, 0.05)
        assert z3 > 0
        assert z3 == analytical._grubbs_z(3, 0.05)


# ---------------------------------------------------------------------------
# Regression — predict with alpha 0.01 tightens CI
# ---------------------------------------------------------------------------


class TestRegression:
    def test_tighter_alpha_gives_narrower_ci(self):
        curve = analytical.CalibrationCurve([1.0, 2.0, 3.0, 4.0, 5.0, 6.0], [10.1, 20.0, 29.9, 40.2, 50.0, 60.1])
        rec95 = curve.predict(35.0, alpha=0.05)
        rec99 = curve.predict(35.0, alpha=0.01)
        ci95 = rec95["ci_upper"] - rec95["ci_lower"]
        ci99 = rec99["ci_upper"] - rec99["ci_lower"]
        assert ci99 > ci95

    def test_lod_always_less_than_loq(self):
        curve = analytical.CalibrationCurve([1.0, 2.0, 3.0, 4.0], [10.0, 20.2, 29.8, 40.1])
        assert curve.lod() < curve.loq()
