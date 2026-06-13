"""W3.8 (H3): Worker stub endpoints must not silently ack — return HTTP 501.

TDD: write the test first.
"""
from __future__ import annotations

from fastapi.testclient import TestClient


class TestWorkerStub501:
    def test_validate_returns_501(self):
        """/jobs/validate must return 501 Not Implemented (not 200 ack)."""
        from general_ludd.worker.app import create_app

        app = create_app(gateway=None)
        client = TestClient(app)
        payload = {
            "job_id": "V-001",
            "todo_id": "T-001",
            "playbook": "noop.yml",
            "queue": "core",
            "work_type": "code",
        }
        resp = client.post("/jobs/validate", json=payload)
        assert resp.status_code == 501, (
            f"/jobs/validate returned {resp.status_code}, expected 501. "
            "Silent ack makes callers believe validation ran."
        )
        data = resp.json()
        assert "reason" in data or "detail" in data, (
            "501 response must include a reason so callers understand why"
        )

    def test_policy_validate_returns_501(self):
        """/jobs/policy-validate must return 501 Not Implemented."""
        from general_ludd.worker.app import create_app

        app = create_app(gateway=None)
        client = TestClient(app)
        payload = {
            "job_id": "V-002",
            "todo_id": "T-002",
            "playbook": "noop.yml",
            "queue": "core",
            "work_type": "code",
        }
        resp = client.post("/jobs/policy-validate", json=payload)
        assert resp.status_code == 501, (
            f"/jobs/policy-validate returned {resp.status_code}, expected 501."
        )
        data = resp.json()
        assert "reason" in data or "detail" in data

    def test_reload_request_returns_501(self):
        """/jobs/reload-request must return 501 Not Implemented."""
        from general_ludd.worker.app import create_app

        app = create_app(gateway=None)
        client = TestClient(app)
        payload = {
            "job_id": "V-003",
            "todo_id": "T-003",
            "playbook": "noop.yml",
            "queue": "core",
            "work_type": "code",
        }
        resp = client.post("/jobs/reload-request", json=payload)
        assert resp.status_code == 501, (
            f"/jobs/reload-request returned {resp.status_code}, expected 501."
        )
        data = resp.json()
        assert "reason" in data or "detail" in data

    def test_execute_still_works(self):
        """/jobs/execute must still function — this test proves we didn't break it."""
        from unittest.mock import MagicMock

        from general_ludd.worker.app import create_app

        runner = MagicMock()
        runner.list_playbooks.return_value = ["noop.yml"]
        runner.prepare_job_dirs.return_value = {"root": "/tmp/exec-test"}
        runner.write_vars.return_value = None
        runner.run_playbook.return_value = {"rc": 0, "output": "ok"}

        import general_ludd.worker.app as wapp
        original = wapp._runner
        wapp._runner = runner
        try:
            app = create_app(gateway=None)
            client = TestClient(app)
            payload = {
                "job_id": "EXEC-001",
                "todo_id": "T-exec",
                "playbook": "noop.yml",
                "queue": "core",
                "work_type": "code",
            }
            resp = client.post("/jobs/execute", json=payload)
            assert resp.status_code == 200, f"execute broke: {resp.text}"
        finally:
            wapp._runner = original
