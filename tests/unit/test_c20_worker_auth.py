"""C20: Worker fail-closed auth — P1 security fix.

The worker was historically fail-open: no PSK configured → any caller could
reach /jobs/* endpoints. C20 fixes this: when no PSK is configured, the worker
defaults to DENY (fail-closed) on all /jobs/* paths, returning 403.

Tests:
  - test_worker_fails_closed_without_psk — no PSK → all /jobs/* return 403
  - test_worker_allows_with_valid_psk — valid PSK → endpoint reached
  - test_worker_healthz_always_public — /healthz always accessible
  - test_mirrors_daemon_fail_closed — auth posture contract matches daemon-side

TDD: written to confirm and pin the fail-closed contract.
"""
from __future__ import annotations

from unittest.mock import patch

from fastapi.testclient import TestClient

from general_ludd.security.auth import AuthPosture, load_auth_posture
from general_ludd.worker.app import create_app

_PSK = "c20-test-worker-psk"

_EXEC_PAYLOAD = {
    "job_id": "C20-001",
    "playbook": "noop.yml",
    "queue": "core",
    "work_type": "code",
}


class TestC20WorkerAuth:
    """C20: Worker fail-closed auth — default deny without PSK."""

    def test_worker_fails_closed_without_psk(self):
        """No PSK configured, both opt-out vars cleared → all /jobs/* return 403."""
        with patch.dict("os.environ", {"GLUDD_PSK_DISABLE": "", "GLUDD_ALLOW_NO_AUTH": ""}):
            app = create_app(gateway=None)
            client = TestClient(app)

            resp = client.post("/jobs/execute", json=_EXEC_PAYLOAD)
            assert resp.status_code == 403, (
                f"expected 403 (fail-closed) for /jobs/execute without PSK, "
                f"got {resp.status_code}"
            )

            for path in (
                "/jobs/validate",
                "/jobs/policy-validate",
                "/jobs/reload-request",
                "/jobs/return-review",
            ):
                resp = client.post(path, json=_EXEC_PAYLOAD)
                assert resp.status_code == 403, (
                    f"{path} without PSK returned {resp.status_code}, expected 403"
                )

    def test_worker_allows_with_valid_psk(self):
        """Valid PSK → /jobs/execute passes auth, reaches endpoint logic."""
        with patch.dict("os.environ", {"GLUDD_AUTH_PSK": _PSK}):
            app = create_app(gateway=None)
            client = TestClient(app)
            resp = client.post(
                "/jobs/execute",
                json=_EXEC_PAYLOAD,
                headers={"Authorization": f"Bearer {_PSK}"},
            )
        assert resp.status_code not in (401, 403), (
            f"valid PSK should pass auth, got {resp.status_code}"
        )

    def test_worker_healthz_always_public(self):
        """/healthz is always accessible, even when PSK is not configured."""
        with patch.dict("os.environ", {"GLUDD_PSK_DISABLE": "", "GLUDD_ALLOW_NO_AUTH": ""}):
            app = create_app(gateway=None)
            client = TestClient(app)
            resp = client.get("/healthz")
            assert resp.status_code == 200, (
                f"/healthz should remain public, got {resp.status_code}"
            )

    def test_mirrors_daemon_fail_closed(self):
        """Verify the auth posture contract matches daemon-side behavior.

        Both daemon and worker use the shared load_auth_posture from
        security/auth.py.  When no PSK is configured and no explicit
        GLUDD_PSK_DISABLE opt-out is present, both surfaces must return
        require_auth=True (fail-closed).
        """
        import os

        clean_env: dict[str, str] = {}
        for k, v in os.environ.items():
            if not k.startswith("GLUDD_"):
                clean_env[k] = v

        worker_posture: AuthPosture = load_auth_posture("worker", clean_env)
        daemon_posture: AuthPosture = load_auth_posture("daemon", clean_env)

        assert worker_posture.require_auth is True, (
            "worker fail-closed: require_auth must be True when no PSK and "
            "no GLUDD_PSK_DISABLE"
        )
        assert daemon_posture.require_auth is True, (
            "daemon fail-closed: require_auth must be True when no PSK and "
            "no GLUDD_PSK_DISABLE"
        )
        assert worker_posture.no_auth is True
        assert daemon_posture.no_auth is True
        assert worker_posture.psk == ""
        assert daemon_posture.psk == ""

        assert worker_posture.require_auth == daemon_posture.require_auth, (
            "worker and daemon require_auth contract must match"
        )
        assert worker_posture.no_auth == daemon_posture.no_auth, (
            "worker and daemon no_auth contract must match"
        )
