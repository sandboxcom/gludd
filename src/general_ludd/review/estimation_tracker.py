"""Task estimation accuracy tracker — close the feedback loop on inaccurate predictions.

When a task completes with metrics wildly different from its estimate (cost,
time, lines of code), the completion is flagged as SUSPECT. If the task IS
actually complete, the tracker adjusts its estimation model to improve
future predictions — a self-correcting feedback loop.

Key behaviors:
  1. On task creation: record the estimate (cost, time, LOC, complexity)
  2. On task completion: compare estimate vs actual; flag suspect if variance > threshold
  3. Self-correction: update estimation parameters when honest completions show
     the model was wrong (the task was actually done, just differently than predicted)
  4. Reporting: per-work-type accuracy, trend detection, calibration

Thresholds (configurable):
  - COST_VARIANCE_THRESHOLD: 0.5 (50% — actual differs from estimate by >50%)
  - TIME_VARIANCE_THRESHOLD: 0.5
  - LOC_VARIANCE_THRESHOLD: 0.5
  - MIN_SAMPLES_FOR_CORRECTION: 5 (need 5 completed tasks before adjusting model)
"""

from __future__ import annotations

import statistics
from datetime import UTC, datetime
from typing import Any

from general_ludd.review.estimation_reporting import build_estimation_report
from general_ludd.review.estimation_types import (
    EstimateAccuracy,
    EstimateVariance,
    EstimationCalibration,
    EstimationReport,
    TaskActual,
    TaskEstimate,
)

__all__ = (
    "EstimateAccuracy",
    "EstimateVariance",
    "EstimationCalibration",
    "EstimationReport",
    "EstimationTracker",
    "TaskActual",
    "TaskEstimate",
    "default_estimation_tracker",
)


class EstimationTracker:
    """Tracks task estimation accuracy and self-corrects over time.

    Usage:
        tracker = EstimationTracker()

        # At task creation:
        tracker.record_estimate(TaskEstimate(
            todo_id="TODO-001", work_type="code",
            estimated_cost_usd=0.50, estimated_time_minutes=30,
            estimated_loc=200,
        ))

        # At task completion:
        variance = tracker.record_completion(TaskActual(
            todo_id="TODO-001", actual_cost_usd=0.12,
            actual_time_minutes=8, actual_loc=45,
            exit_code=0,
        ))

        if variance.is_suspect:
            print(f"SUSPECT completion: {variance.suspect_reasons}")

        # Get corrected estimate for next task:
        corrected = tracker.get_corrected_estimate("code", cost=0.50, time=30, loc=200)
    """

    def __init__(
        self,
        cost_threshold: float = 0.5,
        time_threshold: float = 0.5,
        loc_threshold: float = 0.5,
        min_samples: int = 5,
        max_history: int = 1000,
    ) -> None:
        """Initialize thresholds, calibration state, and bounded history."""
        self._cost_threshold = cost_threshold
        self._time_threshold = time_threshold
        self._loc_threshold = loc_threshold
        self._min_samples = min_samples
        if isinstance(max_history, bool) or not isinstance(max_history, int) or max_history <= 0:
            raise ValueError("max_history must be a positive integer")
        self._max_history = min(max_history, 1000)

        self._estimates: dict[str, TaskEstimate] = {}
        self._actuals: dict[str, TaskActual] = {}
        self._variances: list[EstimateVariance] = []
        self._calibrations: dict[str, EstimationCalibration] = {}
        self._history: list[tuple[TaskEstimate, TaskActual]] = []

    # ---- Recording ----

    def record_estimate(self, estimate: TaskEstimate) -> None:
        """Record a task estimate at creation time."""
        # Re-inserting an identifier makes it the newest pending observation.
        # Plain dicts retain insertion order on every supported Python version.
        self._estimates.pop(estimate.todo_id, None)
        self._estimates[estimate.todo_id] = estimate
        self._trim_mapping(self._estimates)
        # Init calibration if not exists
        if estimate.work_type not in self._calibrations:
            self._calibrations[estimate.work_type] = EstimationCalibration(
                work_type=estimate.work_type
            )

    def record_completion(
        self,
        actual: TaskActual,
        *,
        cost_only: bool = False,
    ) -> EstimateVariance:
        """Record actual completion metrics and return variance analysis.

        Returns an EstimateVariance indicating whether the completion is
        suspect (metrics wildly different from estimate). ``cost_only`` is the
        runtime shadow path: it classifies only the provider-reported cost and
        never updates calibration used by corrected estimates.
        """
        self._actuals.pop(actual.todo_id, None)
        self._actuals[actual.todo_id] = actual
        self._trim_mapping(self._actuals)
        estimate = self._estimates.get(actual.todo_id)

        if estimate is None:
            return EstimateVariance(
                todo_id=actual.todo_id,
                work_type="unknown",
                cost_variance=0.0,
                time_variance=0.0,
                loc_variance=0.0,
                accuracy=EstimateAccuracy.ACCURATE,
                is_suspect=False,
            )

        cost_var = self._compute_variance(estimate.estimated_cost_usd, actual.actual_cost_usd)
        time_var = self._compute_variance(estimate.estimated_time_minutes, actual.actual_time_minutes)
        loc_var = self._compute_variance(estimate.estimated_loc, actual.actual_loc)

        if cost_only:
            accuracy = self._classify_cost_accuracy(cost_var)
            is_suspect, reasons = self._determine_cost_suspect(
                cost_var,
                actual.exit_code,
            )
        else:
            accuracy = self._classify_accuracy(cost_var, time_var, loc_var)
            is_suspect, reasons = self._determine_suspect(
                cost_var, time_var, loc_var, accuracy, actual.exit_code
            )

        variance = EstimateVariance(
            todo_id=actual.todo_id,
            work_type=estimate.work_type,
            cost_variance=cost_var,
            time_variance=time_var,
            loc_variance=loc_var,
            accuracy=accuracy,
            is_suspect=is_suspect,
            suspect_reasons=reasons,
        )
        self._variances.append(variance)
        if len(self._variances) > self._max_history:
            self._variances = self._variances[-self._max_history:]
        self._history.append((estimate, actual))

        # Trim history
        if len(self._history) > self._max_history:
            self._history = self._history[-self._max_history:]

        # Self-correct if not suspect
        if not is_suspect and not cost_only:
            self._update_calibration(estimate, actual)

        return variance

    def get_corrected_estimate(
        self, work_type: str, cost: float, time: float, loc: int
    ) -> tuple[float, float, int]:
        """Apply learned calibration to produce a corrected estimate.

        Returns (corrected_cost, corrected_time, corrected_loc).
        """
        cal = self._calibrations.get(work_type)
        if cal is None or cal.sample_count < self._min_samples:
            return cost, time, loc
        corrected_cost = cost * cal.cost_multiplier
        corrected_time = time * cal.time_multiplier
        corrected_loc = int(loc * cal.loc_multiplier)
        return corrected_cost, corrected_time, corrected_loc

    def get_variance(self, todo_id: str) -> EstimateVariance | None:
        """Get the variance analysis for a completed task."""
        for v in reversed(self._variances):
            if v.todo_id == todo_id:
                return v
        return None

    def discard_estimate(self, todo_id: str) -> None:
        """Discard one pending estimate after its observation window closes."""
        self._estimates.pop(todo_id, None)

    @property
    def pending_count(self) -> int:
        """Return the bounded number of estimates awaiting completion."""
        return len(self._estimates)

    @property
    def completed_count(self) -> int:
        """Return the bounded number of completed variance observations."""
        return len(self._variances)

    @property
    def max_history(self) -> int:
        """Return the effective hard-capped observation bound."""
        return self._max_history

    def get_suspect_tasks(self) -> list[EstimateVariance]:
        """Return all tasks flagged as suspect."""
        return [v for v in self._variances if v.is_suspect]

    def generate_report(self) -> EstimationReport:
        """Generate an aggregated estimation accuracy report."""
        return build_estimation_report(
            self._variances,
            self._calibrations,
            trend=self._compute_trend(),
        )

    def get_calibration(self, work_type: str) -> EstimationCalibration | None:
        """Get calibration data for a work type."""
        return self._calibrations.get(work_type)

    # ---- internal ----

    @staticmethod
    def _compute_variance(estimated: float, actual: float) -> float:
        denom = max(abs(estimated), 0.01)
        return (actual - estimated) / denom

    def _classify_accuracy(
        self, cost_var: float, time_var: float, loc_var: float
    ) -> EstimateAccuracy:
        abs_cost = abs(cost_var)
        abs_time = abs(time_var)
        abs_loc = abs(loc_var)
        max_var = max(abs_cost, abs_time, abs_loc)

        if max_var <= self._cost_threshold:
            return EstimateAccuracy.ACCURATE
        if cost_var > self._cost_threshold or time_var > self._time_threshold:
            return EstimateAccuracy.OVER_ESTIMATE
        return EstimateAccuracy.UNDER_ESTIMATE

    def _classify_cost_accuracy(self, cost_var: float) -> EstimateAccuracy:
        """Classify one shadow observation without consulting other dimensions."""
        if abs(cost_var) <= self._cost_threshold:
            return EstimateAccuracy.ACCURATE
        if cost_var > 0:
            return EstimateAccuracy.UNDER_ESTIMATE
        return EstimateAccuracy.OVER_ESTIMATE

    def _determine_cost_suspect(
        self,
        cost_var: float,
        exit_code: int,
    ) -> tuple[bool, list[str]]:
        """Flag cost-only drift while keeping it out of calibration/routing."""
        reasons: list[str] = []
        if exit_code != 0:
            reasons.append(f"Non-zero exit code ({exit_code})")
        if abs(cost_var) > self._cost_threshold:
            reasons.append(
                f"Cost variance {cost_var:+.1%} exceeds shadow threshold "
                f"{self._cost_threshold:.1%}"
            )
        return bool(reasons), reasons

    def _determine_suspect(
        self,
        cost_var: float,
        time_var: float,
        loc_var: float,
        accuracy: EstimateAccuracy,
        exit_code: int,
    ) -> tuple[bool, list[str]]:
        reasons: list[str] = []

        if exit_code != 0:
            reasons.append(f"Non-zero exit code ({exit_code})")

        if abs(cost_var) > 2.0:
            reasons.append(
                f"Cost variance {cost_var:+.1%} — actual differs from estimate by >200%"
            )
        if abs(time_var) > 2.0:
            reasons.append(
                f"Time variance {time_var:+.1%} — actual differs from estimate by >200%"
            )
        if abs(loc_var) > 3.0:
            reasons.append(
                f"LOC variance {loc_var:+.1%} — actual differs from estimate by >300%"
            )

        # Flag as suspect if vastly under-estimated (task completed too easily)
        # or if any dimension is >5x off
        if cost_var < -0.8 and time_var < -0.8 and loc_var < -0.8:
            reasons.append(
                "All metrics significantly under-estimate — possible incomplete work"
            )

        # Flag if zero cost but estimate was significant
        if cost_var < -0.95 and time_var < -0.95:
            reasons.append("Near-zero cost/time — possible skipped execution")

        return len(reasons) > 0, reasons

    def _update_calibration(self, estimate: TaskEstimate, actual: TaskActual) -> None:
        """Update calibration parameters using exponential moving average.

        Only called for non-suspect completions — honest data points that
        reflect genuine estimation error, not fraudulent completions.
        """
        cal = self._calibrations.get(estimate.work_type)
        if cal is None:
            cal = EstimationCalibration(work_type=estimate.work_type)
            self._calibrations[estimate.work_type] = cal

        cal.sample_count += 1
        cal.last_adjusted = datetime.now(UTC)

        denom = max(abs(estimate.estimated_cost_usd), 0.01)
        cost_ratio = actual.actual_cost_usd / denom
        if cal.sample_count == 1:
            cal.mean_cost_error = cost_ratio
        else:
            cal.mean_cost_error = cal.mean_cost_error * 0.9 + cost_ratio * 0.1

        denom = max(abs(estimate.estimated_time_minutes), 1.0)
        time_ratio = actual.actual_time_minutes / denom
        if cal.sample_count == 1:
            cal.mean_time_error = time_ratio
        else:
            cal.mean_time_error = cal.mean_time_error * 0.9 + time_ratio * 0.1

        denom = max(abs(estimate.estimated_loc), 1)
        loc_ratio = actual.actual_loc / denom
        if cal.sample_count == 1:
            cal.mean_loc_error = loc_ratio
        else:
            cal.mean_loc_error = cal.mean_loc_error * 0.9 + loc_ratio * 0.1

        if cal.sample_count >= self._min_samples:
            cal.cost_multiplier = cal.mean_cost_error
            cal.time_multiplier = cal.mean_time_error
            cal.loc_multiplier = cal.mean_loc_error

    def _compute_trend(self) -> str:
        """Determine if estimation accuracy is improving, degrading, or stable."""
        if len(self._variances) < self._min_samples * 2:
            return "insufficient_data"

        recent = self._variances[-self._min_samples:]
        older = self._variances[-self._min_samples * 2:-self._min_samples]

        recent_error = statistics.mean(
            [max(abs(v.cost_variance), abs(v.time_variance)) for v in recent]
        ) if recent else 0
        older_error = statistics.mean(
            [max(abs(v.cost_variance), abs(v.time_variance)) for v in older]
        ) if older else 0

        if recent_error < older_error * 0.8:
            return "improving"
        if recent_error > older_error * 1.2:
            return "degrading"
        return "stable"

    def reset(self) -> None:
        """Reset all tracking data (for testing)."""
        self._estimates.clear()
        self._actuals.clear()
        self._variances.clear()
        self._calibrations.clear()
        self._history.clear()

    def _trim_mapping(self, values: dict[str, Any]) -> None:
        """Keep insertion-ordered tracker maps within the shared hard bound."""
        while len(values) > self._max_history:
            values.pop(next(iter(values)))


def default_estimation_tracker() -> EstimationTracker:
    """Return a fresh tracker using the application defaults."""
    return EstimationTracker()
