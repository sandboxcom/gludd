"""W3.4 (N1/C6): /readyz endpoint reflects degraded state.

TDD: write the test first.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient


class TestReadyzEndpoint:
    def test_readyz_200_when_healthy(self):
        """Non-degraded, running event-loop → /readyz returns 200."""
        with patch(
            "general_ludd.ansible.runner.AnsibleRunnerAdapter",
            return_value=MagicMock(),
        ):
            from general_ludd.daemon import create_daemon_app

            app = create_daemon_app(tick_interval=0.01)
            with TestClient(app) as client:
                resp = client.get("/readyz")
                assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
                data = resp.json()
                assert data.get("status") == "ready"

    def test_readyz_503_when_degraded(self):
        """Degraded app → /readyz returns 503 while /healthz returns 200."""
        with patch(
            "general_ludd.ansible.runner.AnsibleRunnerAdapter",
            return_value=MagicMock(),
        ):
            from general_ludd.daemon import create_daemon_app

            app = create_daemon_app(tick_interval=0.01)
            with TestClient(app) as client:
                # Force degraded state
                app.state._degraded = "startup failed: test injection"
                resp = client.get("/readyz")
                assert resp.status_code == 503, (
                    f"Degraded app should return 503, got {resp.status_code}: {resp.text}"
                )
                data = resp.json()
                assert data.get("status") == "degraded"

                # /healthz must still return 200 (liveness, not readiness)
                hresp = client.get("/healthz")
                assert hresp.status_code == 200

    def test_readyz_503_when_event_loop_done(self):
        """Cancelled event-loop task → /readyz returns 503."""
        with patch(
            "general_ludd.ansible.runner.AnsibleRunnerAdapter",
            return_value=MagicMock(),
        ):
            from general_ludd.daemon import create_daemon_app

            app = create_daemon_app(tick_interval=0.01)
            with TestClient(app) as client:
                # Simulate cancelled event-loop task by replacing it with a done task
                done_task = MagicMock()
                done_task.done.return_value = True
                done_task.cancelled.return_value = True
                app.state._event_loop_task = done_task

                resp = client.get("/readyz")
                assert resp.status_code == 503

    def test_healthz_is_still_liveness(self):
        """/healthz must not change its liveness semantics — always 200 unless catastrophic."""
        with patch(
            "general_ludd.ansible.runner.AnsibleRunnerAdapter",
            return_value=MagicMock(),
        ):
            from general_ludd.daemon import create_daemon_app

            app = create_daemon_app(tick_interval=0.01)
            with TestClient(app) as client:
                resp = client.get("/healthz")
                assert resp.status_code == 200

    def test_readyz_is_in_public_paths(self):
        """readyz must be reachable without a PSK (same policy as healthz)."""
        with patch(
            "general_ludd.ansible.runner.AnsibleRunnerAdapter",
            return_value=MagicMock(),
        ):
            import os

            os.environ["GLUDD_PSK"] = "test-secret-psk"
            try:
                from general_ludd.daemon import create_daemon_app

                app = create_daemon_app(tick_interval=0.01)
                with TestClient(app) as client:
                    resp = client.get("/readyz")
                    # Must not return 401 — should be 200 or 503
                    assert resp.status_code in (200, 503), (
                        f"/readyz returned {resp.status_code} — it may be behind PSK auth"
                    )
            finally:
                del os.environ["GLUDD_PSK"]
