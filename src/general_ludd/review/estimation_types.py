"""Typed estimation observations, variances, calibration, and reports."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


class EstimateAccuracy(StrEnum):
    """Classification of actual work against its recorded estimate."""

    ACCURATE = "accurate"
    OVER_ESTIMATE = "over"
    UNDER_ESTIMATE = "under"
    SUSPECT = "suspect"


@dataclass
class TaskEstimate:
    """Estimate recorded at task creation time."""

    todo_id: str
    work_type: str
    estimated_cost_usd: float
    estimated_time_minutes: float
    estimated_loc: int
    complexity: str = "medium"
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass
class TaskActual:
    """Actual metrics recorded at task completion."""

    todo_id: str
    actual_cost_usd: float
    actual_time_minutes: float
    actual_loc: int
    exit_code: int
    completed_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass
class EstimateVariance:
    """Variance between estimate and actual."""

    todo_id: str
    work_type: str
    cost_variance: float
    time_variance: float
    loc_variance: float
    accuracy: EstimateAccuracy
    is_suspect: bool
    suspect_reasons: list[str] = field(default_factory=list)


@dataclass
class EstimationCalibration:
    """Per-work-type calibration parameters that self-adjust over time."""

    work_type: str
    cost_multiplier: float = 1.0
    time_multiplier: float = 1.0
    loc_multiplier: float = 1.0
    sample_count: int = 0
    last_adjusted: datetime | None = None
    mean_cost_error: float = 0.0
    mean_time_error: float = 0.0
    mean_loc_error: float = 0.0


@dataclass
class EstimationReport:
    """Aggregated estimation accuracy report."""

    total_estimates: int = 0
    total_suspect: int = 0
    by_work_type: dict[str, dict[str, Any]] = field(default_factory=dict)
    calibrations: dict[str, EstimationCalibration] = field(default_factory=dict)
    overall_accuracy: float = 1.0
    trend: str = "stable"
    generated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
