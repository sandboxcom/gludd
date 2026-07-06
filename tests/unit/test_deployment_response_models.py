"""Tests that all 6 Pydantic response models are wired into their endpoints.

Covers:
  - DeploymentHealthListResponse  (GET  /admin/deployments/health)
  - IncidentListResponse          (GET  /admin/deployments/incidents)
  - MisconfigCheckResponse        (POST /admin/deployments/misconfig-check)
  - SuspectCompletion             (GET  /admin/estimation/suspect)
  - CalibrationInfo               (GET  /admin/estimation/calibrations)
  - PendingResponse               (GET  /admin/review/pending)
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from general_ludd.models.deployment_health import (
    DeploymentHealthChecker,
    SelfHealingRouter,
)
from general_ludd.review.estimation_tracker import (
    EstimationTracker,
    TaskActual,
    TaskEstimate,
)


def _make_deployment_app() -> tuple[TestClient, FastAPI]:
    app = FastAPI()
    checker = DeploymentHealthChecker()
    router = SelfHealingRouter(health_checker=checker)
    app.state._deployment_health_router = router
    from general_ludd.routers.deployments import register

    register(app, {})
    return TestClient(app), app


def _make_estimation_app() -> tuple[TestClient, FastAPI]:
    app = FastAPI()
    tracker = EstimationTracker()
    app.state._estimation_tracker = tracker
    from general_ludd.routers.estimation import register

    register(app, {})
    return TestClient(app), app


class _StubHumanGate:
    """Minimal HumanGate stand-in for /admin/review/pending."""

    def __init__(self) -> None:
        self._pending: dict[str, Any] = {"thread-a": {}, "thread-b": {}}

    @property
    def pending_thread_ids(self) -> list[str]:
        return list(self._pending.keys())

    @property
    def pending_count(self) -> int:
        return len(self._pending)

    @property
    def available(self) -> bool:
        return True

    @property
    def enabled(self) -> bool:
        return True


def _make_review_app() -> tuple[TestClient, FastAPI]:
    app = FastAPI()
    from general_ludd.routers.review import register

    register(app, {"human_gate": _StubHumanGate()})
    return TestClient(app), app


# =========================================================================
# 1. DeploymentHealthListResponse  — GET /admin/deployments/health
# =========================================================================


class TestDeploymentHealthListResponse:
    def test_returns_empty_list_when_no_deployments(self) -> None:
        client, _ = _make_deployment_app()
        resp = client.get("/admin/deployments/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["deployments"] == []
        assert data["total"] == 0

    def test_returns_deployment_list_with_correct_fields(self) -> None:
        client, app = _make_deployment_app()
        checker = app.state._deployment_health_router.health_checker
        checker.record_success("dep-1")
        checker.record_failure("dep-2", "oom")

        resp = client.get("/admin/deployments/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2
        assert len(data["deployments"]) == 2

        dep_ids = {d["deployment_id"] for d in data["deployments"]}
        assert dep_ids == {"dep-1", "dep-2"}

        for d in data["deployments"]:
            assert "healthy" in d
            assert "consecutive_failures" in d
            assert "last_error" in d
            assert "last_check" in d
            assert isinstance(d["last_check"], float)

    def test_503_when_no_health_router_wired(self) -> None:
        app = FastAPI()
        from general_ludd.routers.deployments import register

        register(app, {})
        client = TestClient(app)
        resp = client.get("/admin/deployments/health")
        assert resp.status_code == 503


# =========================================================================
# 2. IncidentListResponse  — GET /admin/deployments/incidents
# =========================================================================


class TestIncidentListResponse:
    def test_returns_empty_incident_list_when_no_incidents(self) -> None:
        client, _ = _make_deployment_app()
        resp = client.get("/admin/deployments/incidents")
        assert resp.status_code == 200
        data = resp.json()
        assert data["incidents"] == []
        assert data["total"] == 0

    def test_returns_incidents_with_correct_fields(self) -> None:
        client, app = _make_deployment_app()
        checker = app.state._deployment_health_router.health_checker
        checker.record_failure("dep-x", "timeout", kind="timeout")
        checker.record_failure("dep-y", "crash")

        resp = client.get("/admin/deployments/incidents")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2
        assert len(data["incidents"]) == 2

        for inc in data["incidents"]:
            assert "deployment_id" in inc
            assert "timestamp" in inc
            assert "kind" in inc
            assert "detail" in inc
            assert "was_remediated" in inc
            assert isinstance(inc["was_remediated"], bool)

    def test_respects_limit_query_param(self) -> None:
        client, app = _make_deployment_app()
        checker = app.state._deployment_health_router.health_checker
        for i in range(10):
            checker.record_failure("dep-z", f"error-{i}")

        resp = client.get("/admin/deployments/incidents?limit=3")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 3
        assert len(data["incidents"]) == 3


# =========================================================================
# 3. MisconfigCheckResponse  — POST /admin/deployments/misconfig-check
# =========================================================================


class TestMisconfigCheckResponse:
    def test_returns_findings_and_remediations_in_typed_shape(self) -> None:
        client, _ = _make_deployment_app()
        resp = client.post(
            "/admin/deployments/misconfig-check",
            json={
                "deployment": {"engine": "vllm", "gpu_memory_utilization": 0.99},
                "gpu_type": "a100_80",
                "gpu_count": 1,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "findings" in data
        assert "remediations" in data
        assert "has_critical" in data
        assert isinstance(data["has_critical"], bool)

        for f in data["findings"]:
            assert "rule_id" in f
            assert "severity" in f
            assert "engine" in f
            assert "message" in f
            assert "remediation" in f
            assert "evidence" in f

        for r in data["remediations"]:
            assert "rule_id" in r
            assert "format" in r
            assert "config_patch" in r
            assert "requires_restart" in r
            assert "notes" in r

    def test_malformed_deployment_returns_200_with_critical(self) -> None:
        client, _ = _make_deployment_app()
        resp = client.post(
            "/admin/deployments/misconfig-check",
            json={"deployment": []},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["has_critical"] is True
        assert any(
            f["rule_id"] == "malformed-deployment" for f in data["findings"]
        )

    def test_findings_and_remediations_are_paired(self) -> None:
        client, _ = _make_deployment_app()
        resp = client.post(
            "/admin/deployments/misconfig-check",
            json={"deployment": {"engine": "bogus"}},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["findings"]) == len(data["remediations"])


# =========================================================================
# 4. SuspectCompletion  — GET /admin/estimation/suspect
# =========================================================================


class TestSuspectCompletionResponse:
    def test_returns_empty_list_when_no_tasks(self) -> None:
        client, _ = _make_estimation_app()
        resp = client.get("/admin/estimation/suspect")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert data == []

    def test_returns_suspect_completions_with_correct_fields(self) -> None:
        client, app = _make_estimation_app()
        tracker: EstimationTracker = app.state._estimation_tracker

        tracker.record_estimate(
            TaskEstimate(
                todo_id="T-001",
                work_type="code",
                estimated_cost_usd=1.0,
                estimated_time_minutes=20.0,
                estimated_loc=100,
            )
        )
        tracker.record_completion(
            TaskActual(
                todo_id="T-001",
                actual_cost_usd=10.0,
                actual_time_minutes=200.0,
                actual_loc=1000,
                exit_code=0,
            )
        )

        resp = client.get("/admin/estimation/suspect")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) >= 1

        item = data[0]
        assert "todo_id" in item
        assert "work_type" in item
        assert "cost_variance" in item
        assert "time_variance" in item
        assert "loc_variance" in item
        assert "accuracy" in item
        assert "is_suspect" in item
        assert "suspect_reasons" in item
        assert isinstance(item["is_suspect"], bool)
        assert isinstance(item["suspect_reasons"], list)


# =========================================================================
# 5. CalibrationInfo  — GET /admin/estimation/calibrations
# =========================================================================


class TestCalibrationInfoResponse:
    def test_lists_all_calibrations(self) -> None:
        client, app = _make_estimation_app()
        tracker: EstimationTracker = app.state._estimation_tracker
        tracker.record_estimate(
            TaskEstimate(
                todo_id="T-001", work_type="code",
                estimated_cost_usd=1.0, estimated_time_minutes=10.0, estimated_loc=50,
            )
        )

        resp = client.get("/admin/estimation/calibrations")
        assert resp.status_code == 200
        data = resp.json()
        assert "calibrations" in data

    def test_returns_single_calibration_by_work_type(self) -> None:
        client, app = _make_estimation_app()
        tracker: EstimationTracker = app.state._estimation_tracker
        tracker.record_estimate(
            TaskEstimate(
                todo_id="T-001", work_type="code",
                estimated_cost_usd=1.0, estimated_time_minutes=10.0, estimated_loc=50,
            )
        )

        resp = client.get("/admin/estimation/calibrations?work_type=code")
        assert resp.status_code == 200
        data = resp.json()
        assert "work_type" in data
        assert "cost_multiplier" in data
        assert "time_multiplier" in data
        assert "loc_multiplier" in data
        assert "sample_count" in data
        assert "last_adjusted" in data
        assert isinstance(data["sample_count"], int)

    def test_unknown_work_type_returns_not_found(self) -> None:
        client, _ = _make_estimation_app()
        resp = client.get("/admin/estimation/calibrations?work_type=nonexistent")
        assert resp.status_code == 200
        data = resp.json()
        assert data["found"] is False
        assert data["work_type"] == "nonexistent"


# =========================================================================
# 6. PendingResponse  — GET /admin/review/pending
# =========================================================================


class TestPendingResponse:
    def test_returns_pending_gates_with_correct_shape(self) -> None:
        client, _ = _make_review_app()
        resp = client.get("/admin/review/pending")
        assert resp.status_code == 200
        data = resp.json()
        assert "pending" in data
        assert "count" in data
        assert "available" in data
        assert "enabled" in data
        assert isinstance(data["count"], int)
        assert isinstance(data["available"], bool)
        assert isinstance(data["enabled"], bool)
        assert isinstance(data["pending"], list)

        for gate in data["pending"]:
            assert "thread_id" in gate

    def test_503_when_no_human_gate(self) -> None:
        app = FastAPI()
        from general_ludd.routers.review import register

        register(app, {})
        client = TestClient(app)
        resp = client.get("/admin/review/pending")
        assert resp.status_code == 503
