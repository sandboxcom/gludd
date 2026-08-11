"""Tests for spec MATE-001 §6 (validation) and MATE-AT-008 (validation and
uncertainty).

Covers the three public functions in
:mod:`general_ludd.materials.simulation.validation`:

  - :func:`validate_against_experiment` — compare simulation vs experimental
    datasets, compute per-point prediction error, surface outliers (never hide
    them).
  - :func:`uncertainty_bounds` — propagate input uncertainty to an output bound
    via worst-case (linear) or RSS (statistical) stacking.
  - :func:`sensitivity_analysis` — vary a decision-driving input across its
    uncertainty range and check whether candidate rank changes
    deterministically.

Mirrors the verdict-dict style of ``test_materials_strength`` /
``test_materials_tolerance``: every result carries ``state``, ``equation_id``,
``inputs`` (with units), and ``assumptions``.
"""

from __future__ import annotations

import statistics

import pytest

from general_ludd.materials.simulation.validation import (
    STATE_FAIL_CLOSED,
    STATE_OK,
    STATE_OUTLIERS,
    STATE_RANK_CHANGED,
    STATE_RANK_STABLE,
    sensitivity_analysis,
    uncertainty_bounds,
    validate_against_experiment,
)

# ─── validate_against_experiment ───────────────────────────────────────────────


class TestValidateAgainstExperiment:
    def test_prediction_error_reported_for_matched_datasets(self):
        sim = [100.0, 200.0, 300.0]
        exp = [101.0, 198.0, 305.0]
        out = validate_against_experiment(sim, exp, unit="MPa")
        assert out["state"] in (STATE_OK, STATE_OUTLIERS)
        assert out["n_points"] == 3
        assert out["unit"] == "MPa"
        # Convention: e_i = sim_i - exp_i.
        expected_errors = [-1.0, 2.0, -5.0]
        assert out["mean_error"] == pytest.approx(statistics.mean(expected_errors), abs=1e-9)
        assert out["rms_error"] == pytest.approx((sum(e * e for e in expected_errors) / 3) ** 0.5, abs=1e-9)
        assert out["max_abs_error"] == pytest.approx(5.0, abs=1e-9)
        assert len(out["per_point_errors"]) == 3
        assert out["per_point_errors"][0] == pytest.approx(-1.0, abs=1e-9)
        assert out["equation_id"].startswith("prediction_error")
        assert "nominal_tolerance" in out["inputs"]

    def test_outliers_are_not_hidden(self):
        # Point 3 is a clear outlier (10x the others).
        sim = [100.0, 100.0, 100.0, 100.0, 100.0]
        exp = [101.0, 99.0, 100.5, 99.5, 200.0]
        out = validate_against_experiment(sim, exp, unit="MPa", outlier_z_threshold=2.0)
        assert out["state"] == STATE_OUTLIERS
        assert len(out["outliers"]) >= 1
        # The outlier index must be reported so callers cannot hide it.
        idxs = [o["index"] for o in out["outliers"]]
        assert 4 in idxs
        assert all("z_score" in o and "sim" in o and "exp" in o and "abs_error" in o for o in out["outliers"])

    def test_length_mismatch_fails_closed(self):
        out = validate_against_experiment([1.0, 2.0, 3.0], [1.0, 2.0], unit="MPa")
        assert out["state"] == STATE_FAIL_CLOSED
        assert out["n_points"] == 0
        assert out["mean_error"] == 0.0
        assert out["outliers"] == []

    def test_empty_dataset_fails_closed(self):
        out = validate_against_experiment([], [], unit="MPa")
        assert out["state"] == STATE_FAIL_CLOSED
        assert out["n_points"] == 0

    def test_tolerance_breach_marks_state_even_without_z_outlier(self):
        # Every point within z-threshold but a single point exceeds a tight tolerance.
        sim = [100.0, 100.0, 100.0]
        exp = [100.1, 100.1, 105.0]
        out = validate_against_experiment(sim, exp, unit="MPa", tolerance=2.0, outlier_z_threshold=10.0)
        # State must reflect tolerance breach even when no statistical outlier fires.
        assert out["state"] in (STATE_OUTLIERS,)
        assert out["inputs"]["nominal_tolerance"] == 2.0


# ─── uncertainty_bounds ────────────────────────────────────────────────────────


class TestUncertaintyBounds:
    def test_rss_bounds_are_tighter_than_worst_case(self):
        uncertainties = [0.3, 0.4]
        rss = uncertainty_bounds(nominal=10.0, uncertainties=uncertainties, method="rss", unit="MPa")
        wc = uncertainty_bounds(nominal=10.0, uncertainties=uncertainties, method="worst_case", unit="MPa")
        assert rss["state"] == STATE_OK
        assert wc["state"] == STATE_OK
        assert rss["upper"] == pytest.approx(10.0 + (0.3**2 + 0.4**2) ** 0.5, abs=1e-9)
        assert rss["lower"] == pytest.approx(10.0 - (0.3**2 + 0.4**2) ** 0.5, abs=1e-9)
        assert wc["upper"] == pytest.approx(10.7, abs=1e-9)
        assert wc["lower"] == pytest.approx(9.3, abs=1e-9)
        assert rss["band"] < wc["band"]
        assert rss["equation_id"] != wc["equation_id"]

    def test_negative_uncertainty_fails_closed(self):
        out = uncertainty_bounds(nominal=10.0, uncertainties=[0.1, -0.2], method="rss", unit="MPa")
        assert out["state"] == STATE_FAIL_CLOSED
        assert out["upper"] == out["lower"]
        assert out["band"] == 0.0


# ─── sensitivity_analysis ──────────────────────────────────────────────────────


class TestSensitivityAnalysis:
    def test_rank_changes_when_input_varied_across_uncertainty(self):
        # A and B are nominally close; varying within uncertainty flips the leader.
        candidates = [
            {"id": "A", "nominal_score": 100.0, "uncertainty": 5.0},
            {"id": "B", "nominal_score": 98.0, "uncertainty": 1.0},
        ]
        out = sensitivity_analysis(
            candidates,
            varying_input="nominal_score",
            uncertainty_range=(0.0, 1.0),
            n_samples=11,
        )
        assert out["state"] == STATE_RANK_CHANGED
        assert out["nominal_ranking"][0] == "A"
        # At least one sampled rank must differ from nominal.
        assert any(r != ["A", "B"] for r in out["sampled_rankings"])
        assert len(out["sampled_rankings"]) == 11
        assert out["equation_id"].startswith("sensitivity")

    def test_rank_stable_when_uncertainties_do_not_overlap(self):
        candidates = [
            {"id": "A", "nominal_score": 100.0, "uncertainty": 0.5},
            {"id": "B", "nominal_score": 50.0, "uncertainty": 0.5},
        ]
        out = sensitivity_analysis(
            candidates,
            varying_input="nominal_score",
            uncertainty_range=(0.0, 1.0),
            n_samples=5,
        )
        assert out["state"] == STATE_RANK_STABLE
        assert all(r == ["A", "B"] for r in out["sampled_rankings"])

    def test_results_are_deterministic(self):
        candidates = [
            {"id": "A", "nominal_score": 100.0, "uncertainty": 5.0},
            {"id": "B", "nominal_score": 98.0, "uncertainty": 1.0},
            {"id": "C", "nominal_score": 95.0, "uncertainty": 2.0},
        ]
        a = sensitivity_analysis(candidates, varying_input="nominal_score", uncertainty_range=(0.0, 1.0), n_samples=9)
        b = sensitivity_analysis(candidates, varying_input="nominal_score", uncertainty_range=(0.0, 1.0), n_samples=9)
        assert a == b


# ─── validate_against_experiment — deep edge cases ────────────────────────────


class TestValidateAgainstExperimentDeep:
    def test_non_numeric_simulation_fails_closed(self):
        out = validate_against_experiment(["a", "b", "c"], [1.0, 2.0, 3.0], unit="MPa")
        assert out["state"] == STATE_FAIL_CLOSED
        assert out["n_points"] == 0
        assert out["per_point_errors"] == []

    def test_non_numeric_experiment_fails_closed(self):
        out = validate_against_experiment([1.0, 2.0], [1.0, None], unit="MPa")
        assert out["state"] == STATE_FAIL_CLOSED

    def test_all_identical_values_no_outliers(self):
        sim = [100.0, 100.0, 100.0, 100.0]
        exp = [100.0, 100.0, 100.0, 100.0]
        out = validate_against_experiment(sim, exp, unit="N")
        assert out["state"] == STATE_OK
        assert out["rms_error"] == pytest.approx(0.0)
        assert out["outliers"] == []

    def test_mad_zero_handled_gracefully(self):
        sim = [10.0, 10.0, 10.0]
        exp = [10.0, 11.0, 9.0]
        out = validate_against_experiment(sim, exp, unit="mm")
        assert out["n_points"] == 3

    def test_custom_outlier_z_threshold_defaults_to_2_5(self):
        sim = [100.0, 101.0, 102.0, 103.0, 200.0]
        exp = [101.0, 102.0, 101.0, 105.0, 110.0]
        out = validate_against_experiment(sim, exp, unit="MPa")
        assert out["state"] == STATE_OUTLIERS
        assert len(out["outliers"]) >= 1

    def test_relaxed_z_threshold_hides_mild_outlier(self):
        sim = [100.0, 100.0]
        exp = [100.0, 110.0]
        out = validate_against_experiment(sim, exp, unit="MPa", outlier_z_threshold=20.0)
        assert out["state"] == STATE_OK

    def test_tolerance_only_breach_flags_outlier(self):
        sim = [100.0, 100.0]
        exp = [100.0, 110.0]
        out = validate_against_experiment(sim, exp, unit="MPa", tolerance=5.0, outlier_z_threshold=999.0)
        assert out["state"] == STATE_OUTLIERS
        assert any(o["reason"] == "tolerance" for o in out["outliers"])

    def test_empty_unit_raises_value_error(self):
        import pytest

        with pytest.raises(ValueError):
            validate_against_experiment([1.0], [1.0], unit="")
        with pytest.raises(ValueError):
            validate_against_experiment([1.0], [1.0], unit="   ")


# ─── uncertainty_bounds — deep edge cases ─────────────────────────────────────


class TestUncertaintyBoundsDeep:
    def test_empty_uncertainties_returns_zero_band(self):
        out = uncertainty_bounds(nominal=100.0, uncertainties=[], method="rss", unit="MPa")
        assert out["state"] == STATE_OK
        assert out["band"] == 0.0
        assert out["upper"] == pytest.approx(100.0)
        assert out["lower"] == pytest.approx(100.0)

    def test_non_numeric_uncertainties_fails_closed(self):
        out = uncertainty_bounds(nominal=100.0, uncertainties=[1.0, "nope"], method="rss", unit="MPa")
        assert out["state"] == STATE_FAIL_CLOSED
        assert out["band"] == 0.0

    def test_infinite_uncertainty_fails_closed(self):
        import math

        out = uncertainty_bounds(nominal=100.0, uncertainties=[1.0, math.inf], method="rss", unit="MPa")
        assert out["state"] == STATE_FAIL_CLOSED

    def test_nan_uncertainty_fails_closed(self):
        import math

        out = uncertainty_bounds(nominal=100.0, uncertainties=[1.0, math.nan], method="rss", unit="MPa")
        assert out["state"] == STATE_FAIL_CLOSED

    def test_unknown_method_fails_closed(self):
        out = uncertainty_bounds(nominal=100.0, uncertainties=[1.0, 2.0], method="monte_carlo", unit="MPa")
        assert out["state"] == STATE_FAIL_CLOSED
        assert "unknown method" in out["assumptions"][0]

    def test_zero_nominal_with_uncertainties_still_ok(self):
        out = uncertainty_bounds(nominal=0.0, uncertainties=[1.0, 2.0], method="rss", unit="N")
        assert out["state"] == STATE_OK
        assert out["band"] > 0.0
        assert out["upper"] > 0.0 > out["lower"]

    def test_worst_case_band_equals_sum_of_abs(self):
        out = uncertainty_bounds(nominal=50.0, uncertainties=[3.0, 4.0, 1.0], method="worst_case", unit="mm")
        assert out["band"] == pytest.approx(8.0)
        assert out["upper"] == pytest.approx(58.0)
        assert out["lower"] == pytest.approx(42.0)

    def test_default_unit_is_empty_string(self):
        out = uncertainty_bounds(nominal=10.0, uncertainties=[0.5], method="rss")
        assert out["unit"] == ""


# ─── sensitivity_analysis — deep edge cases ───────────────────────────────────


class TestSensitivityAnalysisDeep:
    def test_single_candidate_never_changes_rank(self):
        candidates = [{"id": "A", "nominal_score": 100.0, "uncertainty": 10.0}]
        out = sensitivity_analysis(candidates, varying_input="nominal_score", uncertainty_range=(0.0, 1.0), n_samples=5)
        assert out["state"] == STATE_RANK_STABLE
        assert out["nominal_ranking"] == ["A"]

    def test_swapped_range_low_gt_high_is_normalized(self):
        candidates = [
            {"id": "A", "nominal_score": 100.0, "uncertainty": 5.0},
            {"id": "B", "nominal_score": 98.0, "uncertainty": 1.0},
        ]
        out = sensitivity_analysis(
            candidates, varying_input="nominal_score", uncertainty_range=(1.0, 0.0), n_samples=11
        )
        assert out["state"] in (STATE_RANK_CHANGED, STATE_RANK_STABLE)
        assert len(out["sampled_rankings"]) == 11

    def test_n_samples_zero_defaults_to_one(self):
        candidates = [{"id": "A", "nominal_score": 100.0, "uncertainty": 5.0}]
        out = sensitivity_analysis(candidates, varying_input="nominal_score", uncertainty_range=(0.0, 1.0), n_samples=0)
        assert out["n_samples"] == 1
        assert len(out["sampled_rankings"]) == 1

    def test_no_uncertainty_field_defaults_to_zero(self):
        candidates = [
            {"id": "A", "nominal_score": 100.0},
            {"id": "B", "nominal_score": 50.0},
        ]
        out = sensitivity_analysis(candidates, varying_input="nominal_score", uncertainty_range=(0.0, 1.0), n_samples=5)
        assert all(r == ["A", "B"] for r in out["sampled_rankings"])

    def test_identical_scores_stable_by_original_order(self):
        candidates = [
            {"id": "X", "nominal_score": 100.0, "uncertainty": 2.0},
            {"id": "Y", "nominal_score": 100.0, "uncertainty": 2.0},
        ]
        out = sensitivity_analysis(candidates, varying_input="nominal_score", uncertainty_range=(0.0, 1.0), n_samples=5)
        sampled = out["sampled_rankings"]
        assert all(r == ["X", "Y"] for r in sampled), f"got {sampled}"
