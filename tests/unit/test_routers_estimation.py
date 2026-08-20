"""Deep tests for estimation router endpoints.

Covers:
  - GET /admin/estimation/report       (empty, populated, structure)
  - GET /admin/estimation/suspect      (empty list, populated, sort order)
  - GET /admin/estimation/calibrations (all, single work_type, missing)
  - _get_tracker reuse + creation
  - _calibration_to_dict completeness
  - Response model validation (EstimationReportResponse, SuspectCompletion, CalibrationInfo)
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from general_ludd.review.estimation_tracker import (
    EstimationCalibration,
    EstimationTracker,
    TaskActual,
    TaskEstimate,
)
from general_ludd.routers.estimation import (
    CalibrationInfo,
    EstimationReportResponse,
    SuspectCompletion,
    _calibration_to_dict,
    register,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_calibration(
    work_type: str = "code",
    cost_multiplier: float = 1.2,
    time_multiplier: float = 0.9,
    loc_multiplier: float = 1.1,
    sample_count: int = 12,
    last_adjusted: datetime | None = datetime(2026, 8, 11, tzinfo=UTC),
) -> EstimationCalibration:
    return EstimationCalibration(
        work_type=work_type,
        cost_multiplier=cost_multiplier,
        time_multiplier=time_multiplier,
        loc_multiplier=loc_multiplier,
        sample_count=sample_count,
        last_adjusted=last_adjusted,
    )


def _make_app() -> FastAPI:
    app = FastAPI()
    register(app, {})
    return app


# ---------------------------------------------------------------------------
# 1. _get_tracker / wiring
# ---------------------------------------------------------------------------


class TestTrackerResolution:
    def test_get_tracker_creates_default_when_none_set(self):
        app = _make_app()
        resp = TestClient(app).get("/admin/estimation/report")
        assert resp.status_code == 200
        assert resp.json()["total_estimates"] == 0

    def test_get_tracker_reuses_existing_on_state(self):
        app = _make_app()
        t1 = EstimationTracker()
        t1.record_estimate(
            TaskEstimate(
                todo_id="T",
                work_type="code",
                estimated_cost_usd=1,
                estimated_time_minutes=10,
                estimated_loc=50,
            )
        )
        app.state._estimation_tracker = t1
        resp = TestClient(app).get("/admin/estimation/report")
        data = resp.json()
        assert data["total_estimates"] == 0  # no completions yet
        assert data["total_suspect"] == 0


# ---------------------------------------------------------------------------
# 2. Report endpoint
# ---------------------------------------------------------------------------


class TestReportEndpoint:
    def test_empty_tracker_returns_zeros(self):
        client = TestClient(_make_app())
        resp = client.get("/admin/estimation/report")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_estimates"] == 0
        assert data["total_suspect"] == 0
        assert data["overall_accuracy"] == 1.0
        assert data["by_work_type"] == {}
        assert data["calibrations"] == {}
        assert data["trend"] == "insufficient_data"
        assert "generated_at" in data

    def test_populated_report_structure(self):
        app = _make_app()
        tracker = EstimationTracker()
        tracker.record_estimate(
            TaskEstimate(
                todo_id="A",
                work_type="code",
                estimated_cost_usd=10,
                estimated_time_minutes=20,
                estimated_loc=100,
            )
        )
        tracker.record_estimate(
            TaskEstimate(
                todo_id="B",
                work_type="review",
                estimated_cost_usd=5,
                estimated_time_minutes=10,
                estimated_loc=50,
            )
        )
        tracker.record_completion(
            TaskActual(
                todo_id="A",
                actual_cost_usd=10,
                actual_time_minutes=20,
                actual_loc=100,
                exit_code=0,
            )
        )
        tracker.record_completion(
            TaskActual(
                todo_id="B",
                actual_cost_usd=30,
                actual_time_minutes=60,
                actual_loc=300,
                exit_code=0,
            )
        )
        app.state._estimation_tracker = tracker
        resp = TestClient(app).get("/admin/estimation/report")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_estimates"] == 2
        assert "code" in data["by_work_type"]
        assert "review" in data["by_work_type"]
        assert isinstance(data["overall_accuracy"], float)
        assert isinstance(data["generated_at"], str)

    def test_report_with_calibrations_included(self):
        app = _make_app()
        tracker = EstimationTracker()
        dt = datetime(2026, 8, 10, tzinfo=UTC)
        cal = _make_calibration("ml", cost_multiplier=1.5, sample_count=8, last_adjusted=dt)
        tracker._calibrations["ml"] = cal
        app.state._estimation_tracker = tracker
        resp = TestClient(app).get("/admin/estimation/report")
        data = resp.json()
        assert "ml" in data["calibrations"]
        c = data["calibrations"]["ml"]
        assert c["work_type"] == "ml"
        assert c["cost_multiplier"] == 1.5
        assert c["sample_count"] == 8
        assert isinstance(c["last_adjusted"], str)


# ---------------------------------------------------------------------------
# 3. Suspect endpoint
# ---------------------------------------------------------------------------


class TestSuspectEndpoint:
    def test_no_suspect_tasks_returns_empty_list(self):
        client = TestClient(_make_app())
        resp = client.get("/admin/estimation/suspect")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_returns_all_suspect_tasks(self):
        app = _make_app()
        tracker = EstimationTracker()
        tracker.record_estimate(
            TaskEstimate(
                todo_id="HONEST",
                work_type="code",
                estimated_cost_usd=10,
                estimated_time_minutes=10,
                estimated_loc=10,
            )
        )
        tracker.record_estimate(
            TaskEstimate(
                todo_id="CHEAT1",
                work_type="code",
                estimated_cost_usd=10,
                estimated_time_minutes=10,
                estimated_loc=10,
            )
        )
        tracker.record_completion(
            TaskActual(
                todo_id="HONEST",
                actual_cost_usd=10,
                actual_time_minutes=10,
                actual_loc=10,
                exit_code=0,
            )
        )
        tracker.record_completion(
            TaskActual(
                todo_id="CHEAT1",
                actual_cost_usd=10,
                actual_time_minutes=10,
                actual_loc=10,
                exit_code=1,
            )
        )
        app.state._estimation_tracker = tracker
        resp = TestClient(app).get("/admin/estimation/suspect")
        assert resp.status_code == 200
        items = resp.json()
        assert len(items) == 1
        assert items[0]["todo_id"] == "CHEAT1"
        assert items[0]["is_suspect"] is True
        assert items[0]["work_type"] == "code"

    def test_suspect_completion_fields_present(self):
        app = _make_app()
        tracker = EstimationTracker()
        tracker.record_estimate(
            TaskEstimate(
                todo_id="BAD",
                work_type="review",
                estimated_cost_usd=100,
                estimated_time_minutes=60,
                estimated_loc=500,
            )
        )
        tracker.record_completion(
            TaskActual(
                todo_id="BAD",
                actual_cost_usd=1,
                actual_time_minutes=1,
                actual_loc=5,
                exit_code=0,
            )
        )
        app.state._estimation_tracker = tracker
        resp = TestClient(app).get("/admin/estimation/suspect")
        items = resp.json()
        assert len(items) >= 1
        item = items[0]
        for key in (
            "todo_id",
            "work_type",
            "cost_variance",
            "time_variance",
            "loc_variance",
            "accuracy",
            "is_suspect",
            "suspect_reasons",
        ):
            assert key in item

    def test_multiple_suspect_tasks(self):
        app = _make_app()
        tracker = EstimationTracker()
        for i in range(5):
            tracker.record_estimate(
                TaskEstimate(
                    todo_id=f"B{i}",
                    work_type="code",
                    estimated_cost_usd=10,
                    estimated_time_minutes=10,
                    estimated_loc=10,
                )
            )
        for i in range(5):
            tracker.record_completion(
                TaskActual(
                    todo_id=f"B{i}",
                    actual_cost_usd=10,
                    actual_time_minutes=10,
                    actual_loc=10,
                    exit_code=1,
                )
            )
        app.state._estimation_tracker = tracker
        resp = TestClient(app).get("/admin/estimation/suspect")
        assert len(resp.json()) == 5


# ---------------------------------------------------------------------------
# 4. Calibrations endpoint
# ---------------------------------------------------------------------------


class TestCalibrationsEndpoint:
    def test_all_calibrations_when_no_work_type(self):
        app = _make_app()
        tracker = EstimationTracker()
        tracker._calibrations["code"] = _make_calibration("code", cost_multiplier=1.2)
        tracker._calibrations["docs"] = _make_calibration("docs", cost_multiplier=0.8)
        app.state._estimation_tracker = tracker
        resp = TestClient(app).get("/admin/estimation/calibrations")
        assert resp.status_code == 200
        data = resp.json()
        assert "calibrations" in data
        assert "code" in data["calibrations"]
        assert "docs" in data["calibrations"]
        assert data["calibrations"]["code"]["cost_multiplier"] == 1.2
        assert data["calibrations"]["docs"]["cost_multiplier"] == 0.8

    def test_single_work_type_found(self):
        app = _make_app()
        tracker = EstimationTracker()
        dt = datetime(2026, 8, 10, tzinfo=UTC)
        cal = _make_calibration("code", cost_multiplier=1.5, sample_count=20, last_adjusted=dt)
        tracker._calibrations["code"] = cal
        app.state._estimation_tracker = tracker
        resp = TestClient(app).get("/admin/estimation/calibrations?work_type=code")
        assert resp.status_code == 200
        data = resp.json()
        assert data["work_type"] == "code"
        assert data["cost_multiplier"] == 1.5
        assert data["sample_count"] == 20
        assert isinstance(data["last_adjusted"], str)

    def test_single_work_type_not_found(self):
        app = _make_app()
        tracker = EstimationTracker()
        tracker._calibrations["code"] = _make_calibration("code")
        app.state._estimation_tracker = tracker
        resp = TestClient(app).get("/admin/estimation/calibrations?work_type=nonexistent")
        assert resp.status_code == 200
        assert resp.json() == {"work_type": "nonexistent", "found": False}

    def test_last_adjusted_null_serialized_as_null(self):
        app = _make_app()
        tracker = EstimationTracker()
        cal = _make_calibration("empty", last_adjusted=None, sample_count=0)
        tracker._calibrations["empty"] = cal
        app.state._estimation_tracker = tracker
        resp = TestClient(app).get("/admin/estimation/calibrations?work_type=empty")
        assert resp.status_code == 200
        assert resp.json()["last_adjusted"] is None

    def test_accuracy_string_value_serialized(self):
        app = _make_app()
        tracker = EstimationTracker()
        tracker.record_estimate(
            TaskEstimate(
                todo_id="ACC",
                work_type="code",
                estimated_cost_usd=10,
                estimated_time_minutes=10,
                estimated_loc=10,
            )
        )
        tracker.record_completion(
            TaskActual(
                todo_id="ACC",
                actual_cost_usd=50,
                actual_time_minutes=10,
                actual_loc=10,
                exit_code=0,
            )
        )
        app.state._estimation_tracker = tracker
        resp = TestClient(app).get("/admin/estimation/suspect")
        items = resp.json()
        assert len(items) >= 1
        assert items[0]["accuracy"] in {"accurate", "over", "under", "suspect"}


# ---------------------------------------------------------------------------
# 5. _calibration_to_dict
# ---------------------------------------------------------------------------


class TestCalibrationToDict:
    def test_converts_all_fields(self):
        dt = datetime(2026, 8, 11, tzinfo=UTC)
        cal = EstimationCalibration(
            work_type="code",
            cost_multiplier=1.5,
            time_multiplier=0.8,
            loc_multiplier=1.0,
            sample_count=42,
            last_adjusted=dt,
        )
        d = _calibration_to_dict(cal)
        assert d["work_type"] == "code"
        assert d["cost_multiplier"] == 1.5
        assert d["time_multiplier"] == 0.8
        assert d["loc_multiplier"] == 1.0
        assert d["sample_count"] == 42
        assert d["last_adjusted"] == dt.isoformat()

    def test_last_adjusted_none_serialized_as_none(self):
        cal = EstimationCalibration(
            work_type="docs",
            cost_multiplier=1.0,
            time_multiplier=1.0,
            loc_multiplier=1.0,
            sample_count=0,
            last_adjusted=None,
        )
        d = _calibration_to_dict(cal)
        assert d["last_adjusted"] is None

    def test_output_keys_exhaustive(self):
        dt = datetime(2026, 8, 11, tzinfo=UTC)
        cal = EstimationCalibration(work_type="test", last_adjusted=dt)
        d = _calibration_to_dict(cal)
        assert set(d.keys()) == {
            "work_type",
            "cost_multiplier",
            "time_multiplier",
            "loc_multiplier",
            "sample_count",
            "last_adjusted",
        }


# ---------------------------------------------------------------------------
# 6. Pydantic model validation
# ---------------------------------------------------------------------------


class TestResponseModels:
    def test_estimation_report_response_validates_empty(self):
        m = EstimationReportResponse(
            total_estimates=0,
            total_suspect=0,
            by_work_type={},
            calibrations={},
            overall_accuracy=1.0,
            trend="stable",
            generated_at="2026-08-11T00:00:00+00:00",
        )
        assert m.total_estimates == 0
        assert m.generated_at == "2026-08-11T00:00:00+00:00"

    def test_suspect_completion_model(self):
        s = SuspectCompletion(
            todo_id="T-ABC",
            work_type="code",
            cost_variance=-0.98,
            time_variance=3.5,
            loc_variance=-0.5,
            accuracy="suspect",
            is_suspect=True,
            suspect_reasons=["Non-zero exit code (1)", "Near-zero cost/time"],
        )
        assert s.todo_id == "T-ABC"
        assert s.is_suspect is True
        assert len(s.suspect_reasons) == 2

    def test_calibration_info_model(self):
        c = CalibrationInfo(
            work_type="ml",
            cost_multiplier=1.5,
            time_multiplier=0.9,
            loc_multiplier=1.2,
            sample_count=15,
            last_adjusted="2026-08-11T00:00:00+00:00",
        )
        assert c.work_type == "ml"
        assert c.cost_multiplier == 1.5

    def test_calibration_info_last_adjusted_nullable(self):
        c = CalibrationInfo(
            work_type="docs",
            cost_multiplier=1.0,
            time_multiplier=1.0,
            loc_multiplier=1.0,
            sample_count=0,
            last_adjusted=None,
        )
        assert c.last_adjusted is None

    def test_suspect_completion_accuracy_accepts_accuracy_string(self):
        for val in ("accurate", "over", "under", "suspect"):
            s = SuspectCompletion(
                todo_id="T",
                work_type="code",
                cost_variance=0.0,
                time_variance=0.0,
                loc_variance=0.0,
                accuracy=val,
                is_suspect=False,
                suspect_reasons=[],
            )
            assert s.accuracy == val


# ---------------------------------------------------------------------------
# 7. Parameterised edge cases
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "work_type_param",
    [
        "codereview",
        "docs",
        "testing",
        "research",
    ],
)
def test_calibrations_work_type_query_variety(work_type_param):
    app = _make_app()
    tracker = EstimationTracker()
    tracker._calibrations[work_type_param] = _make_calibration(work_type_param)
    app.state._estimation_tracker = tracker
    resp = TestClient(app).get(f"/admin/estimation/calibrations?work_type={work_type_param}")
    assert resp.status_code == 200
    assert resp.json()["work_type"] == work_type_param


def test_variance_with_accuracy_enum_uses_value_when_hasattr():
    app = _make_app()
    tracker = EstimationTracker()
    tracker.record_estimate(
        TaskEstimate(
            todo_id="E",
            work_type="code",
            estimated_cost_usd=10,
            estimated_time_minutes=10,
            estimated_loc=10,
        )
    )
    tracker.record_completion(
        TaskActual(
            todo_id="E",
            actual_cost_usd=40,
            actual_time_minutes=40,
            actual_loc=40,
            exit_code=0,
        )
    )
    app.state._estimation_tracker = tracker
    resp = TestClient(app).get("/admin/estimation/suspect")
    items = resp.json()
    assert len(items) >= 1
    assert items[0]["accuracy"] in {"accurate", "over", "under", "suspect"}
