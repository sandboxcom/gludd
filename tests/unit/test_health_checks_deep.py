"""Deep health-check and observability tests.

Covers:
  - /healthz response schema validation
  - /readyz degraded states (startup degraded, event-loop done/cancelled, no task)
  - /healthz-as-livez liveness (no dedicated /livez; /healthz IS the liveness probe)
  - DeploymentHealthChecker registration and composition
  - DeploymentHealthChecker timeout / auto-recovery
  - Health metric collection (incidents, statuses, metrics endpoint)
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time

import pytest
from httpx import ASGITransport, AsyncClient

from general_ludd.daemon import _check_degraded, create_daemon_app
from general_ludd.models.deployment_health import (
    DeploymentHealthChecker,
    DeploymentIncident,
    DeploymentIncidentLog,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def app():
    return create_daemon_app(tick_interval=0.01)


async def _make_client(app):
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


async def _finished_task():
    async def _noop() -> None:
        return None

    task = asyncio.create_task(_noop())
    await task
    return task


async def _cancelled_task():
    async def _sleep_forever() -> None:
        await asyncio.sleep(9999)

    task = asyncio.create_task(_sleep_forever())
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    return task


# ---------------------------------------------------------------------------
# /healthz schema validation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_healthz_healthy_schema(app):
    async with await _make_client(app) as client:
        resp = await client.get("/healthz")

    assert resp.status_code == 200
    data = resp.json()

    assert "status" in data
    assert data["status"] == "healthy"
    assert "no_auth" in data
    assert "require_auth" in data
    assert "allow_no_auth" in data
    assert "auth_degraded" in data
    assert "budget_exhausted" in data

    for k in ("no_auth", "require_auth", "allow_no_auth", "auth_degraded", "budget_exhausted"):
        assert isinstance(data[k], bool), f"{k} must be bool, got {type(data[k])}"


@pytest.mark.asyncio
async def test_healthz_degraded_schema(app):
    app.state._degraded = "db_connection_failed"

    async with await _make_client(app) as client:
        resp = await client.get("/healthz")

    assert resp.status_code == 200
    data = resp.json()

    assert data["status"] == "degraded"
    assert "reason" in data
    assert data["reason"] == "db_connection_failed"
    for k in ("no_auth", "require_auth", "allow_no_auth", "auth_degraded", "budget_exhausted"):
        assert isinstance(data[k], bool)


@pytest.mark.asyncio
async def test_healthz_has_no_sensitive_budget_fields(app):
    app.state._degraded = None

    async with await _make_client(app) as client:
        resp = await client.get("/healthz")

    data = resp.json()
    for sensitive in ("daily_spend", "daily_limit", "daily_pct", "per_todo_limit"):
        assert sensitive not in data, f"/healthz must not leak {sensitive}"


# ---------------------------------------------------------------------------
# /readyz degraded states
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_readyz_healthy(app):
    # H.3: /readyz returns 200 only once the event loop task is running;
    # a bare app with no task is 503 daemon_not_initialized.
    async def _run_forever() -> None:
        while True:
            await asyncio.sleep(3600)

    task = asyncio.create_task(_run_forever())
    app.state._event_loop_task = task
    try:
        async with await _make_client(app) as client:
            resp = await client.get("/readyz")
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    assert resp.status_code == 200
    assert resp.json() == {"status": "ready"}


@pytest.mark.asyncio
async def test_readyz_503_on_startup_degraded(app):
    app.state._degraded = "secrets_backend_unreachable"

    async with await _make_client(app) as client:
        resp = await client.get("/readyz")

    assert resp.status_code == 503
    assert resp.json()["status"] == "degraded"
    assert resp.json()["reason"] == "secrets_backend_unreachable"


@pytest.mark.asyncio
async def test_readyz_503_on_event_loop_done(app):
    task = await _finished_task()
    app.state._event_loop_task = task

    async with await _make_client(app) as client:
        resp = await client.get("/readyz")

    assert resp.status_code == 503
    assert resp.json()["status"] == "not_ready"
    assert resp.json()["reason"] == "event_loop_done"


@pytest.mark.asyncio
async def test_readyz_503_on_event_loop_cancelled(app):
    task = await _cancelled_task()
    app.state._event_loop_task = task

    async with await _make_client(app) as client:
        resp = await client.get("/readyz")

    assert resp.status_code == 503
    assert resp.json()["status"] == "not_ready"
    assert resp.json()["reason"] == "event_loop_cancelled"


@pytest.mark.asyncio
async def test_readyz_503_when_no_event_loop_task_and_e2e(app, monkeypatch):
    monkeypatch.setenv("GLUDD_E2E_ACTIVE", "1")
    app.state._event_loop_task_auto = True

    async with await _make_client(app) as client:
        resp = await client.get("/readyz")

    assert resp.status_code == 503
    assert resp.json()["reason"] == "daemon_not_initialized"


# ---------------------------------------------------------------------------
# /healthz as liveness probe (no dedicated /livez endpoint)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_healthz_liveness_always_200(app):
    """Liveness: /healthz returns 200 even when degraded (process is alive)."""
    app.state._degraded = "catastrophic_fake_but_process_alive"

    async with await _make_client(app) as client:
        resp = await client.get("/healthz")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_healthz_liveness_200_without_task(app):
    """No event-loop task set → still 200 (process is alive)."""
    async with await _make_client(app) as client:
        resp = await client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json()["status"] == "healthy"


# ---------------------------------------------------------------------------
# DeploymentHealthChecker — registration and composition
# ---------------------------------------------------------------------------


class TestDeploymentHealthCheckerRegistration:
    def test_constructor_defaults(self):
        checker = DeploymentHealthChecker()
        assert checker._failure_threshold == 3
        assert checker._recovery_interval == 60.0
        assert checker._max_incidents == 1000

    def test_constructor_custom_failure_threshold(self):
        checker = DeploymentHealthChecker(failure_threshold=5, recovery_interval=30.0)
        assert checker._failure_threshold == 5
        assert checker._recovery_interval == 30.0

    def test_constructor_custom_latency_gate(self):
        checker = DeploymentHealthChecker(max_avg_latency_s=2.0, max_latency_samples=20)
        assert checker._max_avg_latency_s == 2.0
        assert checker._max_latency_samples == 20

    def test_constructor_with_content_quality(self):
        from general_ludd.models.deployment_health import ContentQualityCheck

        cq = ContentQualityCheck(non_empty=True, min_length=10, parseable_json=True)
        checker = DeploymentHealthChecker(content_quality=cq)
        assert checker._content_quality is cq

    def test_constructor_with_incident_log(self):
        log = DeploymentIncidentLog(max_in_memory=500)
        checker = DeploymentHealthChecker(incident_log=log)
        assert checker._incident_log is log
        assert checker._incident_log._max_in_memory == 500

    def test_set_deployment_model(self):
        checker = DeploymentHealthChecker()
        checker.set_deployment_model("dep-1", "model-gpt4")
        assert checker._deployment_to_model["dep-1"] == "model-gpt4"

    def test_get_status_defaults_healthy(self):
        checker = DeploymentHealthChecker()
        status = checker.get_status("unknown-dep")
        assert status.healthy is True
        assert status.consecutive_failures == 0
        assert status.last_error is None


# ---------------------------------------------------------------------------
# DeploymentHealthChecker — timeout / auto-recovery
# ---------------------------------------------------------------------------


class TestDeploymentHealthCheckerTimeoutRecovery:
    def test_is_healthy_initially_true(self):
        checker = DeploymentHealthChecker()
        assert checker.is_healthy("dep-1") is True

    def test_record_failure_marks_unhealthy_after_threshold(self):
        checker = DeploymentHealthChecker(failure_threshold=2)
        checker.record_failure("dep-1", "timeout")
        assert checker.is_healthy("dep-1") is True
        checker.record_failure("dep-1", "timeout")
        assert checker.is_healthy("dep-1") is False

    def test_record_success_resets_consecutive_failures(self):
        checker = DeploymentHealthChecker(failure_threshold=2)
        checker.record_failure("dep-1", "error")
        checker.record_success("dep-1")
        status = checker.get_status("dep-1")
        assert status.consecutive_failures == 0

    def test_auto_recovery_after_interval(self):
        checker = DeploymentHealthChecker(failure_threshold=2, recovery_interval=0.01)
        checker.record_failure("dep-1", "timeout")
        checker.record_failure("dep-1", "timeout")
        assert checker.is_healthy("dep-1") is False

        time.sleep(0.02)
        assert checker.is_healthy("dep-1") is True

    def test_force_remediate_restores_health(self):
        checker = DeploymentHealthChecker(failure_threshold=1)
        checker.record_failure("dep-1", "crash")
        assert checker.is_healthy("dep-1") is False

        assert checker.force_remediate("dep-1") is True
        assert checker.is_healthy("dep-1") is True
        status = checker.get_status("dep-1")
        assert status.consecutive_failures == 0

    def test_force_remediate_nonexistent_returns_false(self):
        checker = DeploymentHealthChecker()
        assert checker.force_remediate("ghost-dep") is False

    def test_latency_above_threshold_marks_unhealthy(self):
        checker = DeploymentHealthChecker(max_avg_latency_s=1.0)
        checker.set_deployment_model("dep-lat", "dep-lat")
        for _ in range(5):
            checker.record_latency("dep-lat", 2.0)
        assert checker.is_healthy("dep-lat") is False

    def test_latency_below_threshold_stays_healthy(self):
        checker = DeploymentHealthChecker(max_avg_latency_s=5.0)
        checker.set_deployment_model("dep-lat", "dep-lat")
        checker.record_latency("dep-lat", 1.0)
        checker.record_latency("dep-lat", 2.0)
        assert checker.is_healthy("dep-lat") is True


# ---------------------------------------------------------------------------
# Health metric collection
# ---------------------------------------------------------------------------


class TestHealthMetricCollection:
    def test_incident_log_records_and_retrieves(self):
        log = DeploymentIncidentLog(max_in_memory=10)
        incident = DeploymentIncident(
            timestamp=time.time(),
            deployment_id="dep-42",
            model_id="m-42",
            error_type="timeout",
            error_message="request timed out after 30s",
        )
        log.record(incident)
        all_incidents = log.get_incidents()
        assert len(all_incidents) == 1
        assert all_incidents[0].deployment_id == "dep-42"

    def test_health_checker_incidents_accumulate(self):
        checker = DeploymentHealthChecker(failure_threshold=1)
        checker.record_failure("dep-a", "timeout")
        checker.record_failure("dep-b", "crash")
        incidents = checker.get_incidents()
        assert len(incidents) == 2
        ids = {i.deployment_id for i in incidents}
        assert ids == {"dep-a", "dep-b"}

    def test_all_statuses_tracks_known_deployments(self):
        checker = DeploymentHealthChecker(failure_threshold=2)
        checker.record_failure("dep-a", "err")
        checker.record_failure("dep-b", "err")
        statuses = checker.all_statuses()
        assert "dep-a" in statuses
        assert "dep-b" in statuses

    def test_record_success_on_new_deployment_initializes_status(self):
        checker = DeploymentHealthChecker()
        checker.record_success("fresh-dep")
        status = checker.get_status("fresh-dep")
        assert status.healthy is True
        assert status.deployment_id == "fresh-dep"


# ---------------------------------------------------------------------------
# _check_degraded helper
# ---------------------------------------------------------------------------


class TestCheckDegradedHelper:
    def test_returns_none_when_not_degraded(self, app):
        assert _check_degraded(app) is None

    def test_returns_503_json_response_when_degraded(self, app):
        app.state._degraded = "disk_full"
        resp = _check_degraded(app)
        assert resp is not None
        assert resp.status_code == 503
        body_bytes: bytes = bytes(resp.body)
        parsed = json.loads(body_bytes)
        assert parsed["error"] == "degraded"
        assert parsed["reason"] == "disk_full"

    def test_reason_truncated_at_200_chars(self, app):
        app.state._degraded = "x" * 250
        resp = _check_degraded(app)
        assert resp is not None
        body_bytes: bytes = bytes(resp.body)
        parsed = json.loads(body_bytes)
        assert len(parsed["reason"]) <= 200


# ---------------------------------------------------------------------------
# /metrics endpoint smoke (observability surface)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_metrics_endpoint_returns_prometheus_text(app):
    async with await _make_client(app) as client:
        resp = await client.get("/metrics")
    assert resp.status_code == 200
    assert "text/plain" in resp.headers.get("content-type", "")
    body = resp.text
    assert isinstance(body, str)
    assert len(body) > 0


@pytest.mark.asyncio
async def test_admin_metrics_export_returns_json(app):
    async with await _make_client(app) as client:
        resp = await client.get("/admin/metrics/export")
    assert resp.status_code == 200
    data = resp.json()
    assert "counters" in data
    assert "gauges" in data
    assert "uptime_seconds" in data
    assert isinstance(data["uptime_seconds"], (int, float))


@pytest.mark.asyncio
async def test_metrics_export_includes_counter_keys(app):
    async with await _make_client(app) as client:
        resp = await client.get("/admin/metrics/export")
    data = resp.json()
    assert isinstance(data["counters"], dict)
    assert isinstance(data["gauges"], dict)
