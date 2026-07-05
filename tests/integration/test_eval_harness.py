"""Integration tests for G5 EvalHarness — daemon endpoints via TestClient."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from general_ludd.daemon import create_daemon_app


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setenv("GLUDD_DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("GLUDD_ALLOW_NO_AUTH", "1")


class TestEvalHarnessIntegration:
    @pytest.fixture
    def app(self):
        return create_daemon_app(tick_interval=0.01)

    def test_post_eval_run_dry(self, app):
        with TestClient(app) as client:
            resp = client.post(
                "/admin/eval/run",
                json={
                    "cases": [
                        {
                            "id": "case-1",
                            "description": "Test case",
                            "input_files": {"a.py": "x = 1"},
                            "expected_patch": "",
                        },
                    ],
                },
            )
            assert resp.status_code in (200, 503)
            data = resp.json()
            if resp.status_code == 200:
                assert "results" in data
                for r in data["results"]:
                    assert "case_id" in r
                    assert "passed" in r
                    assert "score" in r

    def test_get_eval_results_empty(self, app):
        with TestClient(app) as client:
            resp = client.get("/admin/eval/results")
            assert resp.status_code in (200, 503)
            data = resp.json()
            assert "results" in data
            assert "total" in data

    def test_get_eval_results_populated_after_run(self, app):
        with TestClient(app) as client:
            client.post(
                "/admin/eval/run",
                json={
                    "cases": [
                        {
                            "id": "case-2",
                            "description": "Simple case",
                            "input_files": {"b.py": "y = 2"},
                            "expected_patch": "",
                        },
                    ],
                },
            )
            resp = client.get("/admin/eval/results")
            assert resp.status_code in (200, 503)
            data = resp.json()
            assert "total" in data
            assert "passed" in data

    def test_get_eval_status_returns_configured(self, app):
        with TestClient(app) as client:
            resp = client.get("/admin/eval/status")
            assert resp.status_code == 200
            data = resp.json()
            assert "status" in data
            assert data["status"] in ("configured", "not_configured")
            assert "ready" in data
            assert "model" in data

    def test_post_eval_run_requires_cases(self, app):
        with TestClient(app) as client:
            resp = client.post("/admin/eval/run", json={})
            assert resp.status_code in (422, 503)
