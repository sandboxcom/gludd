"""Tests for G5 eval harness daemon wiring — EvalHarness on app.state + /admin/eval/status."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from general_ludd.daemon import create_daemon_app
from general_ludd.eval.harness import EvalHarness


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setenv("GLUDD_DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("GLUDD_ALLOW_NO_AUTH", "1")


class TestEvalDaemonWiring:
    @pytest.fixture
    def app(self):
        return create_daemon_app(tick_interval=0.01)

    def test_eval_harness_on_app_state(self, app):
        with TestClient(app):
            harness = app.state.eval_harness
            assert harness is not None
            assert isinstance(harness, EvalHarness)

    def test_eval_harness_has_model_attribute(self, app):
        with TestClient(app):
            harness = app.state.eval_harness
            assert harness.model == "sonnet"

    def test_eval_harness_ready_is_boolean(self, app):
        with TestClient(app):
            harness = app.state.eval_harness
            assert isinstance(harness.ready, bool)

    def test_admin_eval_status_endpoint_returns_200(self, app):
        with TestClient(app) as client:
            resp = client.get("/admin/eval/status")
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "configured"
            assert data["ready"] is app.state.eval_harness.ready
            assert data["model"] == "sonnet"

    def test_admin_eval_status_ready_field(self, app):
        with TestClient(app) as client:
            resp = client.get("/admin/eval/status")
            assert resp.status_code == 200
            data = resp.json()
            assert data["ready"] is app.state.eval_harness.ready
            assert isinstance(data["model"], str)
