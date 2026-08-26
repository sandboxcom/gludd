"""Unit tests for ``general_ludd.chemistry.analytical`` and
``general_ludd.chemistry.validation`` (Phase D — CHEM-015, CHEM-019).

Covers CHEM-015 (analytical chemistry: calibrations, quantitation, method
validation, out-of-range detection) and CHEM-019 (result validation status)
from ``docs/specs/FEATURE_CHEMISTRY_EXPERT.md`` §8.3 and §10.

Maps to acceptance criteria CHEM-AT-015 / CHEM-AT-016 (calibration round-trip,
LOD/LOQ, precision, recovery, outlier policy, extrapolation flagging) and
CHEM-AT-019 (every reported value carries a validation status; only
``validated`` supports execution-facing artifacts).

Modules are imported through their installed package paths so coverage and
runtime import behavior match the application boundary.
"""

from __future__ import annotations

import math

from general_ludd.chemistry import analytical, validation

# ---------------------------------------------------------------------------
# CHEM-015 CalibrationCurve — linear regression
# ---------------------------------------------------------------------------


class TestCalibrationFit:
    def test_perfect_line_recovers_slope_intercept(self):
        # y = 2x + 1 over x in {1,2,3,4,5}
        concs = [1.0, 2.0, 3.0, 4.0, 5.0]
        resps = [3.0, 5.0, 7.0, 9.0, 11.0]
        curve = analytical.CalibrationCurve(concs, resps)
        rec = curve.fit()
        assert rec["status"] == "succeeded"
        assert math.isclose(rec["slope"], 2.0, rel_tol=1e-9)
        assert math.isclose(rec["intercept"], 1.0, rel_tol=1e-9)
        assert math.isclose(rec["r_squared"], 1.0, abs_tol=1e-12)

    def test_noisy_line_r2_below_one_but_high(self):
        # y ≈ 2x + 1 with small noise; R² should be > 0.99 but < 1
        concs = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0]
        resps = [3.02, 4.99, 7.05, 8.97, 11.03, 12.98, 15.04, 16.99]
        curve = analytical.CalibrationCurve(concs, resps)
        rec = curve.fit()
        assert 0.99 < rec["r_squared"] < 1.0
        assert rec["n"] == 8

    def test_fit_records_residuals_and_range(self):
        concs = [0.0, 1.0, 2.0, 3.0]
        resps = [0.0, 10.0, 20.0, 30.0]
        curve = analytical.CalibrationCurve(concs, resps)
        rec = curve.fit()
        assert "residuals" in rec
        assert len(rec["residuals"]) == 4
        assert rec["range_low"] == 0.0
        assert rec["range_high"] == 3.0
        assert rec["response_low"] == 0.0
        assert rec["response_high"] == 30.0

    def test_fit_rejects_length_mismatch(self):
        try:
            analytical.CalibrationCurve([1.0, 2.0, 3.0], [1.0, 2.0])
            raise AssertionError("expected ValueError on length mismatch")
        except ValueError:
            pass


# ---------------------------------------------------------------------------
# CHEM-015 CalibrationCurve — prediction + extrapolation flag
# ---------------------------------------------------------------------------


class TestCalibrationPredict:
    def test_predict_in_range(self):
        concs = [1.0, 2.0, 3.0, 4.0, 5.0]
        resps = [10.0, 20.0, 30.0, 40.0, 50.0]
        curve = analytical.CalibrationCurve(concs, resps)
        rec = curve.predict(25.0)
        assert rec["status"] == "succeeded"
        assert math.isclose(rec["concentration"], 2.5, rel_tol=1e-6)
        assert rec["in_range"] is True
        assert rec["extrapolated"] is False

    def test_predict_outside_range_flagged_extrapolation(self):
        # response 60 corresponds to concentration 6, beyond calibration max (5)
        concs = [1.0, 2.0, 3.0, 4.0, 5.0]
        resps = [10.0, 20.0, 30.0, 40.0, 50.0]
        curve = analytical.CalibrationCurve(concs, resps)
        rec = curve.predict(60.0)
        # extrapolation must be flagged; status may not be "succeeded" for quantitative use
        assert rec["extrapolated"] is True
        assert rec["in_range"] is False
        assert rec["status"] != "succeeded"

    def test_predict_below_range_flagged(self):
        concs = [1.0, 2.0, 3.0]
        resps = [10.0, 20.0, 30.0]
        curve = analytical.CalibrationCurve(concs, resps)
        rec = curve.predict(5.0)  # would be conc 0.5, below range
        assert rec["extrapolated"] is True

    def test_predict_returns_confidence_interval(self):
        concs = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0]
        # slight noise so SE != 0 and CI is finite
        resps = [10.1, 19.9, 30.2, 39.8, 50.1, 59.9, 70.2, 79.8]
        curve = analytical.CalibrationCurve(concs, resps)
        rec = curve.predict(40.0)
        assert "ci_lower" in rec and "ci_upper" in rec
        assert rec["ci_lower"] < rec["concentration"] < rec["ci_upper"]


# ---------------------------------------------------------------------------
# CHEM-015 LOD / LOQ
# ---------------------------------------------------------------------------


class TestDetectionLimits:
    def test_lod_is_3_sigma_over_slope(self):
        # slope=10, sigma_blank=1.5 -> LOD = 3*1.5/10 = 0.45
        concs = [1.0, 2.0, 3.0, 4.0, 5.0]
        resps = [10.0, 20.0, 30.0, 40.0, 50.0]
        curve = analytical.CalibrationCurve(concs, resps)
        lod = curve.lod(sigma_blank=1.5)
        assert math.isclose(lod, 0.45, rel_tol=1e-6)

    def test_loq_is_10_sigma_over_slope(self):
        concs = [1.0, 2.0, 3.0, 4.0, 5.0]
        resps = [10.0, 20.0, 30.0, 40.0, 50.0]
        curve = analytical.CalibrationCurve(concs, resps)
        loq = curve.loq(sigma_blank=1.5)
        assert math.isclose(loq, 1.5, rel_tol=1e-6)

    def test_lod_uses_default_sigma_from_residuals_when_none(self):
        # If sigma_blank is None, derive sigma from the regression residuals (s_yx).
        # With a noisy line the LOD should be strictly positive.
        concs = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0]
        resps = [10.1, 19.9, 30.2, 39.8, 50.1, 59.9, 70.2, 79.8]
        curve = analytical.CalibrationCurve(concs, resps)
        lod = curve.lod()
        assert lod > 0.0


# ---------------------------------------------------------------------------
# CHEM-015 MethodValidation — precision, accuracy, linearity, etc.
# ---------------------------------------------------------------------------


class TestMethodValidationPrecision:
    def test_precision_returns_rsd(self):
        # replicates of an analyte at ~10 mg/L
        repls = [10.1, 9.9, 10.0, 10.2, 9.8]
        mv = analytical.MethodValidation()
        rec = mv.precision(replicates=repls)
        assert rec["name"] == "precision"
        assert rec["unit"] == "%RSD"
        # mean=10.0, sample std with ddof=1 ~ 0.158 -> RSD ~1.58%
        assert 0.5 < rec["value"] < 3.0

    def test_precision_zero_variance_returns_zero(self):
        mv = analytical.MethodValidation()
        rec = mv.precision(replicates=[5.0, 5.0, 5.0])
        assert math.isclose(rec["value"], 0.0, abs_tol=1e-12)

    def test_precision_rejects_single_replicate(self):
        mv = analytical.MethodValidation()
        try:
            mv.precision(replicates=[5.0])
            raise AssertionError("expected ValueError on single replicate")
        except ValueError:
            pass


class TestMethodValidationAccuracy:
    def test_accuracy_recovery_near_100(self):
        mv = analytical.MethodValidation()
        rec = mv.accuracy(measured=9.95, nominal=10.0)
        assert rec["name"] == "accuracy"
        assert rec["unit"] == "%recovery"
        assert math.isclose(rec["value"], 99.5, rel_tol=1e-6)

    def test_accuracy_low_recovery_flagged(self):
        mv = analytical.MethodValidation()
        rec = mv.accuracy(measured=7.0, nominal=10.0)
        # 70% recovery is outside typical 80-120% acceptance -> flagged
        assert rec["value"] < 80.0
        assert "acceptance" in rec
        assert rec["acceptance"] == "fail"


class TestMethodValidationLinearityRange:
    def test_linearity_uses_calibration_curve(self):
        concs = [1.0, 2.0, 3.0, 4.0, 5.0]
        resps = [10.0, 20.0, 30.0, 40.0, 50.0]
        curve = analytical.CalibrationCurve(concs, resps)
        mv = analytical.MethodValidation(curve=curve)
        rec = mv.linearity()
        assert rec["name"] == "linearity"
        assert math.isclose(rec["value"], 1.0, abs_tol=1e-12)

    def test_range_returns_calibration_bounds(self):
        concs = [1.0, 5.0]
        resps = [10.0, 50.0]
        curve = analytical.CalibrationCurve(concs, resps)
        mv = analytical.MethodValidation(curve=curve)
        rec = mv.range()
        assert rec["name"] == "range"
        assert rec["low"] == 10.0
        assert rec["high"] == 50.0


class TestMethodValidationSpecificityRobustness:
    def test_specificity_record_shape(self):
        mv = analytical.MethodValidation()
        rec = mv.specificity(interferences=[])
        assert rec["name"] == "specificity"
        assert rec["status"] in {"succeeded", "degraded", "provisional"}

    def test_robustness_record_shape(self):
        mv = analytical.MethodValidation()
        rec = mv.robustness(perturbations=[{"factor": "flow", "delta_pct": 5.0}])
        assert rec["name"] == "robustness"
        assert "perturbations_evaluated" in rec


# ---------------------------------------------------------------------------
# CHEM-015 Outlier detection (Grubbs / Dixon stubs)
# ---------------------------------------------------------------------------


class TestOutlierDetection:
    def test_grubbs_flags_obvious_outlier(self):
        # 10 values near 100, one at 200
        values = [100.0] * 10 + [200.0]
        rec = analytical.detect_outliers_grubbs(values)
        assert rec["name"] == "outlier_grubbs"
        # the test should identify at least one outlier
        assert rec["n_outliers"] >= 1
        assert 200.0 in rec["outliers"]

    def test_grubbs_no_outlier_for_clean_data(self):
        values = [10.0, 10.1, 9.9, 10.05, 9.95]
        rec = analytical.detect_outliers_grubbs(values)
        assert rec["n_outliers"] == 0
        assert rec["outliers"] == []

    def test_dixon_q_returns_record(self):
        values = [1.0, 1.05, 1.1, 1.0, 5.0]
        rec = analytical.dixon_q(values)
        assert rec["name"] == "outlier_dixon_q"
        assert "statistic" in rec
        assert "outlier" in rec


# ---------------------------------------------------------------------------
# CHEM-015 Blank subtraction
# ---------------------------------------------------------------------------


class TestBlankSubtraction:
    def test_subtract_blank(self):
        rec = analytical.subtract_blank(response=15.0, blank=2.0)
        assert math.isclose(rec["value"], 13.0, rel_tol=1e-9)
        assert rec["blank_subtracted"] is True

    def test_subtract_blank_recorded_in_signal(self):
        rec = analytical.subtract_blank(response=10.0, blank=0.0)
        assert math.isclose(rec["value"], 10.0, rel_tol=1e-9)
        assert rec["blank"] == 0.0


# ---------------------------------------------------------------------------
# CHEM-015 check_range — extrapolation guard
# ---------------------------------------------------------------------------


class TestCheckRange:
    def test_in_range_passes(self):
        concs = [1.0, 2.0, 3.0, 4.0, 5.0]
        resps = [10.0, 20.0, 30.0, 40.0, 50.0]
        curve = analytical.CalibrationCurve(concs, resps)
        rec = curve.check_range(25.0)
        assert rec["status"] == "succeeded"
        assert rec["extrapolated"] is False

    def test_outside_range_flagged(self):
        concs = [1.0, 2.0, 3.0]
        resps = [10.0, 20.0, 30.0]
        curve = analytical.CalibrationCurve(concs, resps)
        rec = curve.check_range(100.0)
        assert rec["extrapolated"] is True
        assert rec["status"] != "succeeded"


# ---------------------------------------------------------------------------
# CHEM-019 Validation status enum + validate_result framework
# ---------------------------------------------------------------------------


class TestValidationStatus:
    def test_statuses_present(self):
        assert validation.ValidationStatus.VALIDATED == "validated"
        assert validation.ValidationStatus.PROVISIONAL == "provisional"
        assert validation.ValidationStatus.INVALID == "invalid"
        assert validation.ValidationStatus.NOT_APPLICABLE == "not_applicable"

    def test_only_validated_supports_execution(self):
        assert validation.supports_execution("validated") is True
        assert validation.supports_execution("provisional") is False
        assert validation.supports_execution("invalid") is False
        assert validation.supports_execution("not_applicable") is False


class TestValidateResult:
    def test_mass_conservation_passes(self):
        result = {
            "mass_in": 100.0,
            "mass_out": 100.0,
            "checks": ["mass_conservation"],
        }
        rec = validation.validate_result(result)
        assert rec["status"] == "validated"
        checks = {c["check"]: c for c in rec["verification"]}
        assert checks["mass_conservation"]["status"] == "pass"

    def test_mass_conservation_fails_invalid(self):
        result = {
            "mass_in": 100.0,
            "mass_out": 95.0,
            "checks": ["mass_conservation"],
        }
        rec = validation.validate_result(result)
        assert rec["status"] == "invalid"
        checks = {c["check"]: c for c in rec["verification"]}
        assert checks["mass_conservation"]["status"] == "fail"

    def test_charge_conservation(self):
        result = {
            "charge_in": 0.0,
            "charge_out": 0.0,
            "checks": ["charge_conservation"],
        }
        rec = validation.validate_result(result)
        assert rec["status"] == "validated"

    def test_charge_imbalance_invalid(self):
        result = {
            "charge_in": 1.0,
            "charge_out": 0.0,
            "checks": ["charge_conservation"],
        }
        rec = validation.validate_result(result)
        assert rec["status"] == "invalid"

    def test_unit_consistency(self):
        result = {
            "values": [
                {"name": "v1", "value": 1.0, "unit": "mg/L"},
                {"name": "v2", "value": 2.0, "unit": "mg/L"},
            ],
            "checks": ["unit_consistency"],
        }
        rec = validation.validate_result(result)
        assert rec["status"] == "validated"

    def test_unit_inconsistency_invalid(self):
        result = {
            "values": [
                {"name": "v1", "value": 1.0, "unit": "mg/L"},
                {"name": "v2", "value": 2.0, "unit": "mol/L"},
            ],
            "checks": ["unit_consistency"],
        }
        rec = validation.validate_result(result)
        assert rec["status"] == "invalid"

    def test_convergence_pass(self):
        result = {
            "converged": True,
            "iterations": 42,
            "checks": ["convergence"],
        }
        rec = validation.validate_result(result)
        assert rec["status"] == "validated"

    def test_convergence_fail_invalid(self):
        result = {
            "converged": False,
            "iterations": 1000,
            "checks": ["convergence"],
        }
        rec = validation.validate_result(result)
        assert rec["status"] == "invalid"

    def test_limiting_case_zero_input(self):
        # Zero input should produce zero output (limiting case check)
        result = {
            "limiting_case": "zero_input",
            "input_zero": True,
            "output_zero": True,
            "checks": ["limiting_case"],
        }
        rec = validation.validate_result(result)
        assert rec["status"] == "validated"

    def test_limiting_case_zero_input_nonzero_output_invalid(self):
        result = {
            "limiting_case": "zero_input",
            "input_zero": True,
            "output_zero": False,
            "checks": ["limiting_case"],
        }
        rec = validation.validate_result(result)
        assert rec["status"] == "invalid"

    def test_tolerance_window_for_mass(self):
        # 0.5% mass imbalance within tolerance -> pass
        result = {
            "mass_in": 100.0,
            "mass_out": 99.7,
            "tolerance_pct": 1.0,
            "checks": ["mass_conservation"],
        }
        rec = validation.validate_result(result)
        assert rec["status"] == "validated"

    def test_provisional_when_warnings_only(self):
        # A check that is not failed but carries a warning -> provisional
        result = {
            "converged": True,
            "warnings": ["slow convergence"],
            "checks": ["convergence"],
        }
        rec = validation.validate_result(result)
        assert rec["status"] == "provisional"

    def test_validate_result_records_method_and_schema(self):
        result = {"mass_in": 1.0, "mass_out": 1.0, "checks": ["mass_conservation"]}
        rec = validation.validate_result(result)
        assert rec.get("schema_version")
        assert rec.get("method_id")
