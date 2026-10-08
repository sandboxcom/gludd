"""Pure aggregation for bounded task-estimation observations."""

from __future__ import annotations

import statistics
from collections import defaultdict
from collections.abc import Mapping, Sequence

from general_ludd.review.estimation_types import (
    EstimateAccuracy,
    EstimateVariance,
    EstimationCalibration,
    EstimationReport,
)


def build_estimation_report(
    variances: Sequence[EstimateVariance],
    calibrations: Mapping[str, EstimationCalibration],
    *,
    trend: str,
) -> EstimationReport:
    """Aggregate immutable report data from a bounded variance history."""
    report = EstimationReport(
        total_estimates=len(variances),
        total_suspect=sum(1 for variance in variances if variance.is_suspect),
    )
    by_type: dict[str, list[EstimateVariance]] = defaultdict(list)
    for variance in variances:
        by_type[variance.work_type].append(variance)
    for work_type, grouped in sorted(by_type.items()):
        accurate = sum(
            1 for variance in grouped if variance.accuracy == EstimateAccuracy.ACCURATE
        )
        costs = [
            abs(variance.cost_variance)
            for variance in grouped
            if abs(variance.cost_variance) < 100
        ]
        times = [
            abs(variance.time_variance)
            for variance in grouped
            if abs(variance.time_variance) < 100
        ]
        report.by_work_type[work_type] = {
            "total": len(grouped),
            "accurate": accurate,
            "accuracy_rate": accurate / max(len(grouped), 1),
            "suspect": sum(1 for variance in grouped if variance.is_suspect),
            "mean_cost_variance": statistics.mean(costs) if costs else 0.0,
            "mean_time_variance": statistics.mean(times) if times else 0.0,
        }
    report.calibrations = dict(calibrations)
    if report.total_estimates:
        report.overall_accuracy = (
            sum(data["accurate"] for data in report.by_work_type.values())
            / report.total_estimates
        )
    report.trend = trend
    return report
