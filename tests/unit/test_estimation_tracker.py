"""Tests for estimation_tracker.py — estimation accuracy, suspect detection,
self-correction, and reporting."""

from __future__ import annotations

import pytest

from general_ludd.review.estimation_tracker import (
    EstimateAccuracy,
    EstimateVariance,
    EstimationReport,
    EstimationTracker,
    TaskActual,
    TaskEstimate,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_estimate(
    todo_id: str = "T-001",
    work_type: str = "code",
    cost: float = 1.0,
    time: float = 20.0,
    loc: int = 100,
    complexity: str = "medium",
) -> TaskEstimate:
    return TaskEstimate(
        todo_id=todo_id,
        work_type=work_type,
        estimated_cost_usd=cost,
        estimated_time_minutes=time,
        estimated_loc=loc,
        complexity=complexity,
    )


def _make_actual(
    todo_id: str = "T-001",
    cost: float = 1.0,
    time: float = 20.0,
    loc: int = 100,
    exit_code: int = 0,
) -> TaskActual:
    return TaskActual(
        todo_id=todo_id,
        actual_cost_usd=cost,
        actual_time_minutes=time,
        actual_loc=loc,
        exit_code=exit_code,
    )


# ---------------------------------------------------------------------------
# 1. EstimateRecording
# ---------------------------------------------------------------------------

class TestEstimateRecording:
    def test_record_estimate_stores_data_correctly(self):
        tracker = EstimationTracker()
        est = _make_estimate(todo_id="T-001", work_type="code", cost=1.5, time=15.0, loc=80)
        tracker.record_estimate(est)
        assert tracker._estimates["T-001"] is est
        assert tracker._estimates["T-001"].estimated_cost_usd == 1.5
        assert tracker._estimates["T-001"].estimated_time_minutes == 15.0
        assert tracker._estimates["T-001"].estimated_loc == 80

    def test_initializes_calibration_on_record_estimate(self):
        tracker = EstimationTracker()
        tracker.record_estimate(_make_estimate(work_type="code"))
        cal = tracker.get_calibration("code")
        assert cal is not None
        assert cal.work_type == "code"
        assert cal.sample_count == 0

    def test_record_completion_with_matching_estimate_returns_variance(self):
        tracker = EstimationTracker()
        tracker.record_estimate(_make_estimate(cost=1.0, time=20.0, loc=100))
        variance = tracker.record_completion(_make_actual(cost=1.0, time=20.0, loc=100))
        assert isinstance(variance, EstimateVariance)
        assert variance.todo_id == "T-001"
        assert variance.work_type == "code"
        assert abs(variance.cost_variance) < 1e-9
        assert abs(variance.time_variance) < 1e-9
        assert abs(variance.loc_variance) < 1e-9
        assert variance.is_suspect is False

    def test_record_completion_no_prior_estimate_handles_gracefully(self):
        tracker = EstimationTracker()
        variance = tracker.record_completion(_make_actual())
        assert isinstance(variance, EstimateVariance)
        assert variance.todo_id == "T-001"
        assert variance.work_type == "unknown"
        assert variance.cost_variance == 0.0
        assert variance.time_variance == 0.0
        assert variance.loc_variance == 0.0
        assert variance.is_suspect is False
        assert variance.accuracy == EstimateAccuracy.ACCURATE

    def test_multiple_estimates_different_work_types(self):
        tracker = EstimationTracker()
        tracker.record_estimate(_make_estimate(todo_id="A", work_type="code"))
        tracker.record_estimate(_make_estimate(todo_id="B", work_type="review"))
        tracker.record_estimate(_make_estimate(todo_id="C", work_type="docs"))
        assert len(tracker._estimates) == 3
        assert tracker._estimates["A"].work_type == "code"
        assert tracker._estimates["B"].work_type == "review"
        assert tracker._estimates["C"].work_type == "docs"
        assert tracker.get_calibration("code") is not None
        assert tracker.get_calibration("review") is not None
        assert tracker.get_calibration("docs") is not None

    def test_get_variance_returns_correct_record(self):
        tracker = EstimationTracker()
        tracker.record_estimate(_make_estimate(todo_id="T-A"))
        tracker.record_completion(_make_actual(todo_id="T-A"))
        v = tracker.get_variance("T-A")
        assert v is not None
        assert v.todo_id == "T-A"

    def test_get_variance_unknown_id_returns_none(self):
        tracker = EstimationTracker()
        assert tracker.get_variance("nonesuch") is None

    def test_get_suspect_tasks_filters_correctly(self):
        tracker = EstimationTracker()
        tracker.record_estimate(_make_estimate(todo_id="OK", cost=10, time=10, loc=10))
        tracker.record_estimate(_make_estimate(todo_id="BAD", cost=10, time=10, loc=10))
        tracker.record_completion(_make_actual(todo_id="OK", cost=10, time=10, loc=10))
        tracker.record_completion(_make_actual(todo_id="BAD", cost=10, time=10, loc=10, exit_code=1))
        suspects = tracker.get_suspect_tasks()
        assert len(suspects) == 1
        assert suspects[0].todo_id == "BAD"

    def test_record_estimate_overwrites_existing(self):
        tracker = EstimationTracker()
        e1 = _make_estimate(todo_id="T-001", cost=1.0)
        e2 = _make_estimate(todo_id="T-001", cost=5.0)
        tracker.record_estimate(e1)
        tracker.record_estimate(e2)
        assert tracker._estimates["T-001"].estimated_cost_usd == 5.0


# ---------------------------------------------------------------------------
# 2. VarianceComputation
# ---------------------------------------------------------------------------

class TestVarianceComputation:
    def test_exact_match_cost_var_zero(self):
        assert EstimationTracker._compute_variance(10.0, 10.0) == 0.0

    def test_over_estimate_cost_var_negative_half(self):
        var = EstimationTracker._compute_variance(10.0, 5.0)
        assert var == -0.5

    def test_under_estimate_cost_var_one(self):
        var = EstimationTracker._compute_variance(10.0, 20.0)
        assert var == 1.0

    def test_zero_estimate_division_by_zero_protected(self):
        var = EstimationTracker._compute_variance(0.0, 5.0)
        assert var == pytest.approx(5.0 / 0.01)

    def test_zero_estimate_zero_actual(self):
        var = EstimationTracker._compute_variance(0.0, 0.0)
        assert var == 0.0

    def test_negative_estimate(self):
        var = EstimationTracker._compute_variance(-10.0, -5.0)
        assert var == 0.5

    def test_very_small_estimate_not_zero(self):
        var = EstimationTracker._compute_variance(0.005, 0.010)
        assert var == pytest.approx((0.010 - 0.005) / 0.01)


# ---------------------------------------------------------------------------
# 3. SuspectDetection
# ---------------------------------------------------------------------------

class TestSuspectDetection:
    def test_all_metrics_within_threshold_not_suspect(self):
        tracker = EstimationTracker()
        tracker.record_estimate(_make_estimate(cost=10, time=10, loc=10))
        variance = tracker.record_completion(_make_actual(cost=12, time=12, loc=12))
        assert variance.is_suspect is False
        assert variance.suspect_reasons == []

    def test_cost_3x_off_suspect(self):
        tracker = EstimationTracker()
        tracker.record_estimate(_make_estimate(cost=10, time=10, loc=10))
        variance = tracker.record_completion(_make_actual(cost=31, time=10, loc=10))
        assert variance.is_suspect is True
        assert any("Cost variance" in r for r in variance.suspect_reasons)

    def test_time_3x_off_suspect(self):
        tracker = EstimationTracker()
        tracker.record_estimate(_make_estimate(cost=10, time=10, loc=10))
        variance = tracker.record_completion(_make_actual(cost=10, time=31, loc=10))
        assert variance.is_suspect is True
        assert any("Time variance" in r for r in variance.suspect_reasons)

    def test_loc_4x_off_suspect(self):
        tracker = EstimationTracker()
        tracker.record_estimate(_make_estimate(cost=10, time=10, loc=10))
        variance = tracker.record_completion(_make_actual(cost=10, time=10, loc=41))
        assert variance.is_suspect is True
        assert any("LOC variance" in r for r in variance.suspect_reasons)

    def test_all_metrics_vastly_under_estimate_suspect(self):
        tracker = EstimationTracker()
        tracker.record_estimate(_make_estimate(cost=100, time=100, loc=100))
        variance = tracker.record_completion(_make_actual(cost=10, time=10, loc=10))
        assert variance.is_suspect is True
        assert len(variance.suspect_reasons) >= 1

    def test_near_zero_cost_suspect(self):
        tracker = EstimationTracker()
        tracker.record_estimate(_make_estimate(cost=100, time=100, loc=100))
        variance = tracker.record_completion(_make_actual(cost=1, time=1, loc=100))
        assert variance.is_suspect is True
        assert any("Near-zero cost" in r or "Cost variance" in r for r in variance.suspect_reasons)

    def test_non_zero_exit_code_suspect_regardless_of_metrics(self):
        tracker = EstimationTracker()
        tracker.record_estimate(_make_estimate(cost=10, time=10, loc=10))
        variance = tracker.record_completion(_make_actual(cost=10, time=10, loc=10, exit_code=1))
        assert variance.is_suspect is True
        assert any("Non-zero exit code" in r for r in variance.suspect_reasons)


# ---------------------------------------------------------------------------
# 4. SelfCorrection
# ---------------------------------------------------------------------------

class TestSelfCorrection:
    def test_first_honest_sample_seeds_calibration_without_zero_bias(self):
        tracker = EstimationTracker(min_samples=1)
        tracker.record_estimate(_make_estimate(cost=10, time=20, loc=100))

        tracker.record_completion(_make_actual(cost=20, time=40, loc=200))

        calibration = tracker.get_calibration("code")
        assert calibration is not None
        assert calibration.mean_cost_error == pytest.approx(2.0)
        assert calibration.mean_time_error == pytest.approx(2.0)
        assert calibration.mean_loc_error == pytest.approx(2.0)
        assert tracker.get_corrected_estimate("code", 10, 20, 100) == pytest.approx(
            (20.0, 40.0, 200)
        )

    def test_calibration_multipliers_set_after_5_honest_completions(self):
        tracker = EstimationTracker()
        tracker.record_estimate(_make_estimate(cost=10, time=20, loc=100))
        for _ in range(4):
            tracker.record_completion(_make_actual(cost=20, time=40, loc=200))
        cal = tracker.get_calibration("code")
        assert cal is not None
        assert cal.sample_count == 4
        assert cal.cost_multiplier == 1.0
        tracker.record_completion(_make_actual(cost=20, time=40, loc=200))
        assert cal.sample_count == 5
        assert cal.cost_multiplier != 1.0

    def test_get_corrected_estimate_after_calibration(self):
        tracker = EstimationTracker()
        tracker.record_estimate(_make_estimate(cost=10, time=20, loc=100))
        for _ in range(5):
            tracker.record_completion(_make_actual(cost=20, time=40, loc=200))
        corrected_cost, corrected_time, corrected_loc = tracker.get_corrected_estimate(
            "code", cost=10.0, time=20.0, loc=100
        )
        correction = tracker.get_calibration("code").cost_multiplier
        assert corrected_cost == pytest.approx(10.0 * correction)
        assert corrected_time == pytest.approx(20.0 * correction)
        assert isinstance(corrected_loc, int)

    def test_suspect_completions_do_not_update_calibration(self):
        tracker = EstimationTracker()
        tracker.record_estimate(_make_estimate(cost=10, time=20, loc=100))
        for _ in range(5):
            tracker.record_completion(_make_actual(cost=10, time=20, loc=100))
        cal_before = tracker.get_calibration("code").sample_count
        tracker.record_completion(_make_actual(cost=10, time=20, loc=100, exit_code=1))
        assert tracker.get_calibration("code").sample_count == cal_before

    def test_calibration_converges_toward_correct_values(self):
        tracker = EstimationTracker()
        tracker.record_estimate(_make_estimate(cost=10, time=20, loc=100))
        for _ in range(30):
            tracker.record_completion(_make_actual(cost=20, time=40, loc=200))
        cal = tracker.get_calibration("code")
        assert cal.cost_multiplier == pytest.approx(2.0, rel=0.1)
        assert cal.time_multiplier == pytest.approx(2.0, rel=0.1)
        assert cal.loc_multiplier == pytest.approx(2.0, rel=0.1)

    def test_different_work_types_calibrated_independently(self):
        tracker = EstimationTracker()
        tracker.record_estimate(_make_estimate(todo_id="C1", work_type="code", cost=10, time=10, loc=10))
        tracker.record_estimate(_make_estimate(todo_id="R1", work_type="review", cost=10, time=10, loc=10))
        for _ in range(8):
            tracker.record_completion(_make_actual(todo_id="C1", cost=20, time=20, loc=20))
            tracker.record_completion(_make_actual(todo_id="R1", cost=5, time=5, loc=5))
        code_cal = tracker.get_calibration("code")
        review_cal = tracker.get_calibration("review")
        assert code_cal.cost_multiplier > 1.0
        assert review_cal.cost_multiplier < 1.0
        assert code_cal.cost_multiplier != review_cal.cost_multiplier

    def test_calibration_respects_min_samples_threshold(self):
        tracker = EstimationTracker(min_samples=3)
        tracker.record_estimate(_make_estimate(cost=10, time=10, loc=10))
        for _ in range(2):
            tracker.record_completion(_make_actual(cost=20, time=20, loc=20))
        cal = tracker.get_calibration("code")
        assert cal.sample_count == 2
        assert cal.cost_multiplier == 1.0
        tracker.record_completion(_make_actual(cost=20, time=20, loc=20))
        assert cal.sample_count == 3
        assert cal.cost_multiplier != 1.0


# ---------------------------------------------------------------------------
# 5. ReportGeneration
# ---------------------------------------------------------------------------

class TestReportGeneration:
    def test_empty_tracker_produces_zero_count_report(self):
        tracker = EstimationTracker()
        report = tracker.generate_report()
        assert isinstance(report, EstimationReport)
        assert report.total_estimates == 0
        assert report.total_suspect == 0
        assert report.overall_accuracy == 1.0
        assert report.by_work_type == {}
        assert report.trend == "insufficient_data"

    def test_report_aggregates_by_work_type(self):
        tracker = EstimationTracker()
        tracker.record_estimate(_make_estimate(todo_id="C1", work_type="code"))
        tracker.record_estimate(_make_estimate(todo_id="R1", work_type="review"))
        tracker.record_completion(_make_actual(todo_id="C1"))
        tracker.record_completion(_make_actual(todo_id="R1", exit_code=1))
        report = tracker.generate_report()
        assert "code" in report.by_work_type
        assert "review" in report.by_work_type
        assert report.by_work_type["code"]["total"] == 1
        assert report.by_work_type["review"]["total"] == 1
        assert report.by_work_type["review"]["suspect"] == 1
        assert report.by_work_type["code"]["suspect"] == 0

    def test_overall_accuracy_computed_correctly(self):
        tracker = EstimationTracker()
        for i in range(4):
            tracker.record_estimate(_make_estimate(todo_id=f"T{i}", cost=10, time=10, loc=10))
        tracker.record_completion(_make_actual(todo_id="T0", cost=10, time=10, loc=10))
        tracker.record_completion(_make_actual(todo_id="T1", cost=10, time=10, loc=10))
        tracker.record_completion(_make_actual(todo_id="T2", cost=30, time=30, loc=30))
        tracker.record_completion(_make_actual(todo_id="T3", cost=30, time=30, loc=30))
        report = tracker.generate_report()
        assert report.overall_accuracy == pytest.approx(2 / 4)

    def test_trend_improving(self):
        tracker = EstimationTracker()
        for i in range(10):
            tracker.record_estimate(_make_estimate(todo_id=f"T{i}", cost=10, time=10, loc=10))
        for i in range(5):
            tracker.record_completion(_make_actual(todo_id=f"T{i}", cost=50, time=50, loc=50))
        for i in range(5, 10):
            tracker.record_completion(_make_actual(todo_id=f"T{i}", cost=12, time=12, loc=12))
        report = tracker.generate_report()
        assert report.trend == "improving"

    def test_trend_degrading(self):
        tracker = EstimationTracker()
        for i in range(10):
            tracker.record_estimate(_make_estimate(todo_id=f"T{i}", cost=10, time=10, loc=10))
        for i in range(5):
            tracker.record_completion(_make_actual(todo_id=f"T{i}", cost=11, time=11, loc=11))
        for i in range(5, 10):
            tracker.record_completion(_make_actual(todo_id=f"T{i}", cost=50, time=50, loc=50))
        report = tracker.generate_report()
        assert report.trend == "degrading"

    def test_trend_stable(self):
        tracker = EstimationTracker()
        for i in range(10):
            tracker.record_estimate(_make_estimate(todo_id=f"T{i}", cost=10, time=10, loc=10))
        for i in range(10):
            tracker.record_completion(_make_actual(todo_id=f"T{i}", cost=12, time=12, loc=12))
        report = tracker.generate_report()
        assert report.trend == "stable"

    def test_trend_insufficient_data(self):
        tracker = EstimationTracker()
        tracker.record_estimate(_make_estimate())
        tracker.record_completion(_make_actual())
        report = tracker.generate_report()
        assert report.trend == "insufficient_data"


# ---------------------------------------------------------------------------
# 6. CorrectedEstimate
# ---------------------------------------------------------------------------

class TestCorrectedEstimate:
    def test_get_corrected_estimate_raw_values_without_calibration(self):
        tracker = EstimationTracker()
        corrected = tracker.get_corrected_estimate("code", cost=1.5, time=25.0, loc=120)
        assert corrected == (1.5, 25.0, 120)

    def test_after_calibration_returns_adjusted_values(self):
        tracker = EstimationTracker()
        tracker.record_estimate(_make_estimate(cost=5, time=10, loc=50))
        for _ in range(8):
            tracker.record_completion(_make_actual(cost=10, time=20, loc=100))
        corrected = tracker.get_corrected_estimate("code", cost=5, time=10, loc=50)
        assert corrected[0] > 5.0
        assert corrected[1] > 10.0
        assert corrected[2] > 50

    def test_different_work_types_get_different_corrections(self):
        tracker = EstimationTracker()
        tracker.record_estimate(_make_estimate(todo_id="C1", work_type="code", cost=10, time=10, loc=10))
        tracker.record_estimate(_make_estimate(todo_id="R1", work_type="review", cost=10, time=10, loc=10))
        for _ in range(5):
            tracker.record_completion(_make_actual(todo_id="C1", cost=20, time=20, loc=20))
            tracker.record_completion(_make_actual(todo_id="R1", cost=5, time=5, loc=5))
        code_corrected = tracker.get_corrected_estimate("code", cost=10, time=10, loc=10)
        review_corrected = tracker.get_corrected_estimate("review", cost=10, time=10, loc=10)
        assert code_corrected[0] > review_corrected[0]


# ---------------------------------------------------------------------------
# 7. HistoryBound
# ---------------------------------------------------------------------------

class TestHistoryBound:
    def test_history_trimmed_when_exceeding_max(self):
        tracker = EstimationTracker(max_history=3)
        for i in range(5):
            tracker.record_estimate(_make_estimate(todo_id=f"T{i}"))
            tracker.record_completion(_make_actual(todo_id=f"T{i}"))
        assert len(tracker._history) == 3

    def test_old_entries_discarded_recent_preserved(self):
        tracker = EstimationTracker(max_history=3)
        for i in range(5):
            tracker.record_estimate(_make_estimate(todo_id=f"T{i}"))
            tracker.record_completion(_make_actual(todo_id=f"T{i}"))
        ids_in_history = {e.todo_id for e, _ in tracker._history}
        assert ids_in_history == {"T2", "T3", "T4"}
        assert "T0" not in ids_in_history
        assert "T1" not in ids_in_history


# ---------------------------------------------------------------------------
# 8. Reset
# ---------------------------------------------------------------------------

class TestReset:
    def test_reset_clears_all_internal_state(self):
        tracker = EstimationTracker()
        tracker.record_estimate(_make_estimate())
        tracker.record_completion(_make_actual())
        assert len(tracker._estimates) > 0
        assert len(tracker._actuals) > 0
        assert len(tracker._variances) > 0
        assert len(tracker._calibrations) > 0
        assert len(tracker._history) > 0
        tracker.reset()
        assert len(tracker._estimates) == 0
        assert len(tracker._actuals) == 0
        assert len(tracker._variances) == 0
        assert len(tracker._calibrations) == 0
        assert len(tracker._history) == 0

    def test_after_reset_corrected_estimate_returns_raw_values(self):
        tracker = EstimationTracker()
        tracker.record_estimate(_make_estimate(cost=5, time=10, loc=50))
        for _ in range(8):
            tracker.record_completion(_make_actual(cost=10, time=20, loc=100))
        assert tracker.get_corrected_estimate("code", cost=5, time=10, loc=50)[0] > 5.0
        tracker.reset()
        corrected = tracker.get_corrected_estimate("code", cost=5, time=10, loc=50)
        assert corrected == (5.0, 10.0, 50)
