"""HTTP router: estimation accuracy tracking endpoints.

PSK-gated (admin-only). Surfaces:

  - GET /admin/estimation/report       — aggregated estimation accuracy report
  - GET /admin/estimation/suspect      — list suspect completions
  - GET /admin/estimation/calibrations — per-work-type calibration parameters

All endpoints require the daemon PSK (admin middleware enforces it on every
``/admin/*`` path).
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Query
from pydantic import BaseModel

from general_ludd.review.estimation_tracker import (
    EstimationCalibration,
    EstimationTracker,
)

logger = logging.getLogger(__name__)


def _get_tracker(app: FastAPI) -> EstimationTracker:
    from general_ludd.review.estimation_tracker import default_estimation_tracker

    tracker: EstimationTracker | None = getattr(
        app.state, "_estimation_tracker", None
    )
    if tracker is not None:
        return tracker
    tracker = default_estimation_tracker()
    app.state._estimation_tracker = tracker
    return tracker


class EstimationReportResponse(BaseModel):
    total_estimates: int
    total_suspect: int
    by_work_type: dict[str, dict[str, object]]
    calibrations: dict[str, dict[str, object]]
    overall_accuracy: float
    trend: str
    generated_at: str


class SuspectCompletion(BaseModel):
    todo_id: str
    work_type: str
    cost_variance: float
    time_variance: float
    loc_variance: float
    accuracy: str
    is_suspect: bool
    suspect_reasons: list[str]


class CalibrationInfo(BaseModel):
    work_type: str
    cost_multiplier: float
    time_multiplier: float
    loc_multiplier: float
    sample_count: int
    last_adjusted: str | None


def _calibration_to_dict(c: EstimationCalibration) -> dict[str, object]:
    return {
        "work_type": c.work_type,
        "cost_multiplier": c.cost_multiplier,
        "time_multiplier": c.time_multiplier,
        "loc_multiplier": c.loc_multiplier,
        "sample_count": c.sample_count,
        "last_adjusted": c.last_adjusted.isoformat() if c.last_adjusted else None,
    }


def register(app: FastAPI, daemon_state: dict[str, object]) -> None:
    @app.get("/admin/estimation/report", response_model=EstimationReportResponse)
    async def get_estimation_report() -> EstimationReportResponse:
        """Return the aggregated estimation accuracy report."""
        tracker = _get_tracker(app)
        report = tracker.generate_report()
        return EstimationReportResponse(
            total_estimates=report.total_estimates,
            total_suspect=report.total_suspect,
            by_work_type=report.by_work_type,
            calibrations={
                k: _calibration_to_dict(v) for k, v in report.calibrations.items()
            },
            overall_accuracy=report.overall_accuracy,
            trend=report.trend,
            generated_at=report.generated_at.isoformat(),
        )

    @app.get("/admin/estimation/suspect", response_model=list[SuspectCompletion])
    async def get_suspect_completions() -> list[SuspectCompletion]:
        """List all tasks flagged as suspect completions."""
        tracker = _get_tracker(app)
        suspect = tracker.get_suspect_tasks()
        return [
            SuspectCompletion(
                todo_id=v.todo_id,
                work_type=v.work_type,
                cost_variance=v.cost_variance,
                time_variance=v.time_variance,
                loc_variance=v.loc_variance,
                accuracy=v.accuracy.value if hasattr(v.accuracy, "value") else str(v.accuracy),
                is_suspect=v.is_suspect,
                suspect_reasons=v.suspect_reasons,
            )
            for v in suspect
        ]

    @app.get("/admin/estimation/calibrations", response_model=None)
    async def get_calibrations(
        work_type: str | None = Query(default=None),
    ) -> CalibrationInfo | dict[str, object]:
        """Return per-work-type calibration parameters."""
        tracker = _get_tracker(app)
        if work_type is not None:
            cal = tracker.get_calibration(work_type)
            if cal is None:
                return {"work_type": work_type, "found": False}
            return CalibrationInfo(
                work_type=cal.work_type,
                cost_multiplier=cal.cost_multiplier,
                time_multiplier=cal.time_multiplier,
                loc_multiplier=cal.loc_multiplier,
                sample_count=cal.sample_count,
                last_adjusted=cal.last_adjusted.isoformat() if cal.last_adjusted else None,
            )

        report = tracker.generate_report()
        return {
            "calibrations": {
                k: _calibration_to_dict(v) for k, v in report.calibrations.items()
            },
        }
