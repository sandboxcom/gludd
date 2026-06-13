"""W5.6 (AUTH blocker): worker /jobs/* endpoints require PSK auth.

The daemon enforces a pre-shared-key (GLUDD_PSK) on all non-public paths
(daemon.py auth_and_stats_middleware). The worker historically accepted any
caller who could reach the port — anyone on the network could make it run
arbitrary registered playbooks. This test pins the same PSK contract on the
worker:

  - GLUDD_PSK set + no Authorization header  -> 401 (BEFORE any 501/200 logic)
  - GLUDD_PSK set + wrong token               -> 401
  - GLUDD_PSK set + correct Bearer token      -> endpoint's normal behavior
  - GLUDD_PSK unset                           -> no auth enforced (back-compat)
  - /healthz is always public

TDD: written before the middleware exists.
"""
from __future__ import annotations

from unittest.mock import patch

from fastapi.testclient import TestClient

from general_ludd.worker.app import create_app

_PSK = "test-worker-psk-secret"

_EXEC_PAYLOAD = {
    "job_id": "AUTH-001",
    "todo_id": "T-001",
    "playbook": "noop.yml",
    "queue": "core",
    "work_type": "code",
}


def _client_with_psk() -> TestClient:
    with patch.dict("os.environ", {"GLUDD_PSK": _PSK}):
        app = create_app(gateway=None)
    # create_app reads the env var at construction time; build inside the patch.
    return TestClient(app)


class TestWorkerAuth:
    def test_execute_without_psk_header_is_401(self):
        with patch.dict("os.environ", {"GLUDD_PSK": _PSK}):
            app = create_app(gateway=None)
            client = TestClient(app)
            resp = client.post("/jobs/execute", json=_EXEC_PAYLOAD)
        assert resp.status_code == 401, (
            f"unauthenticated /jobs/execute returned {resp.status_code}, expected 401"
        )

    def test_execute_with_wrong_psk_is_401(self):
        with patch.dict("os.environ", {"GLUDD_PSK": _PSK}):
            app = create_app(gateway=None)
            client = TestClient(app)
            resp = client.post(
                "/jobs/execute",
                json=_EXEC_PAYLOAD,
                headers={"Authorization": "Bearer wrong-token"},
            )
        assert resp.status_code == 401

    def test_validate_without_psk_is_401_before_501(self):
        """Auth must run BEFORE the 501 stub (W3.8) — no header -> 401, not 501."""
        with patch.dict("os.environ", {"GLUDD_PSK": _PSK}):
            app = create_app(gateway=None)
            client = TestClient(app)
            resp = client.post("/jobs/validate", json=_EXEC_PAYLOAD)
        assert resp.status_code == 401, (
            f"/jobs/validate without PSK returned {resp.status_code}; auth must "
            "fire before the 501 stub"
        )

    def test_policy_validate_without_psk_is_401(self):
        with patch.dict("os.environ", {"GLUDD_PSK": _PSK}):
            app = create_app(gateway=None)
            client = TestClient(app)
            resp = client.post("/jobs/policy-validate", json=_EXEC_PAYLOAD)
        assert resp.status_code == 401

    def test_reload_request_without_psk_is_401(self):
        with patch.dict("os.environ", {"GLUDD_PSK": _PSK}):
            app = create_app(gateway=None)
            client = TestClient(app)
            resp = client.post("/jobs/reload-request", json=_EXEC_PAYLOAD)
        assert resp.status_code == 401

    def test_return_review_without_psk_is_401(self):
        with patch.dict("os.environ", {"GLUDD_PSK": _PSK}):
            app = create_app(gateway=None)
            client = TestClient(app)
            resp = client.post("/jobs/return-review", json=_EXEC_PAYLOAD)
        assert resp.status_code == 401

    def test_validate_with_correct_psk_reaches_endpoint(self):
        """With a valid PSK, /jobs/validate reaches its 501 handler (auth passed)."""
        with patch.dict("os.environ", {"GLUDD_PSK": _PSK}):
            app = create_app(gateway=None)
            client = TestClient(app)
            resp = client.post(
                "/jobs/validate",
                json=_EXEC_PAYLOAD,
                headers={"Authorization": f"Bearer {_PSK}"},
            )
        assert resp.status_code == 501, (
            f"authenticated /jobs/validate returned {resp.status_code}, expected "
            "the 501 stub (auth passed through to handler)"
        )

    def test_healthz_is_public_even_with_psk(self):
        with patch.dict("os.environ", {"GLUDD_PSK": _PSK}):
            app = create_app(gateway=None)
            client = TestClient(app)
            resp = client.get("/healthz")
        assert resp.status_code == 200

    def test_no_psk_set_means_no_auth(self):
        """Back-compat: when GLUDD_PSK is unset, existing callers still work."""
        import os

        env = dict(os.environ)
        env.pop("GLUDD_PSK", None)
        with patch.dict("os.environ", env, clear=True):
            app = create_app(gateway=None)
            client = TestClient(app)
            # validate still returns its 501 (no auth gate), not 401
            resp = client.post("/jobs/validate", json=_EXEC_PAYLOAD)
        assert resp.status_code == 501
