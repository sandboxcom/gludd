"""C20: worker /jobs/* endpoints require PSK auth (fail-closed by default).

The daemon enforces a pre-shared-key (GLUDD_AUTH_PSK) on all non-public paths
(daemon.py auth_and_stats_middleware). The worker historically accepted any
caller who could reach the port — anyone on the network could make it run
arbitrary registered playbooks. This test pins the fail-closed PSK contract:

  - GLUDD_AUTH_PSK set + no Authorization header  -> 401 (BEFORE any 501/200 logic)
  - GLUDD_AUTH_PSK set + wrong token               -> 401
  - GLUDD_AUTH_PSK set + correct Bearer token      -> endpoint's normal behavior
  - GLUDD_AUTH_PSK unset (fail-closed)             -> 403 (C20: fail-closed by default)
  - GLUDD_AUTH_PSK unset + GLUDD_PSK_DISABLE=1     -> pass-through (back-compat escape)
  - /healthz is always public
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
    with patch.dict("os.environ", {"GLUDD_AUTH_PSK": _PSK}):
        app = create_app(gateway=None)
    # create_app reads the env var at construction time; build inside the patch.
    return TestClient(app)


class TestWorkerAuth:
    def test_execute_without_psk_header_is_401(self):
        with patch.dict("os.environ", {"GLUDD_AUTH_PSK": _PSK}):
            app = create_app(gateway=None)
            client = TestClient(app)
            resp = client.post("/jobs/execute", json=_EXEC_PAYLOAD)
        assert resp.status_code == 401, (
            f"unauthenticated /jobs/execute returned {resp.status_code}, expected 401"
        )

    def test_execute_with_wrong_psk_is_401(self):
        with patch.dict("os.environ", {"GLUDD_AUTH_PSK": _PSK}):
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
        with patch.dict("os.environ", {"GLUDD_AUTH_PSK": _PSK}):
            app = create_app(gateway=None)
            client = TestClient(app)
            resp = client.post("/jobs/validate", json=_EXEC_PAYLOAD)
        assert resp.status_code == 401, (
            f"/jobs/validate without PSK returned {resp.status_code}; auth must "
            "fire before the 501 stub"
        )

    def test_policy_validate_without_psk_is_401(self):
        with patch.dict("os.environ", {"GLUDD_AUTH_PSK": _PSK}):
            app = create_app(gateway=None)
            client = TestClient(app)
            resp = client.post("/jobs/policy-validate", json=_EXEC_PAYLOAD)
        assert resp.status_code == 401

    def test_reload_request_without_psk_is_401(self):
        with patch.dict("os.environ", {"GLUDD_AUTH_PSK": _PSK}):
            app = create_app(gateway=None)
            client = TestClient(app)
            resp = client.post("/jobs/reload-request", json=_EXEC_PAYLOAD)
        assert resp.status_code == 401

    def test_return_review_without_psk_is_401(self):
        with patch.dict("os.environ", {"GLUDD_AUTH_PSK": _PSK}):
            app = create_app(gateway=None)
            client = TestClient(app)
            resp = client.post("/jobs/return-review", json=_EXEC_PAYLOAD)
        assert resp.status_code == 401

    def test_validate_with_correct_psk_reaches_endpoint(self):
        """With a valid PSK, /jobs/validate reaches its 501 handler (auth passed)."""
        with patch.dict("os.environ", {"GLUDD_AUTH_PSK": _PSK}):
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
        with patch.dict("os.environ", {"GLUDD_AUTH_PSK": _PSK}):
            app = create_app(gateway=None)
            client = TestClient(app)
            resp = client.get("/healthz")
        assert resp.status_code == 200

    def test_no_psk_set_fail_closed_403(self):
        """C20: when GLUDD_AUTH_PSK is unset, fail-closed with 403 (not fail-open)."""
        import os

        env = dict(os.environ)
        env.pop("GLUDD_AUTH_PSK", None)
        env.pop("GLUDD_PSK_DISABLE", None)
        env.pop("GLUDD_ALLOW_NO_AUTH", None)
        with patch.dict("os.environ", env, clear=True):
            app = create_app(gateway=None)
            client = TestClient(app)
            resp = client.post("/jobs/validate", json=_EXEC_PAYLOAD)
        assert resp.status_code == 403, (
            f"unauthenticated /jobs/validate with no PSK returned {resp.status_code}, "
            "expected 403 (C20: fail-closed by default)"
        )

    def test_psk_disable_allows_pass_through(self):
        """C20: GLUDD_PSK_DISABLE=1 is the back-compat escape, allows pass-through."""
        import os

        env = dict(os.environ)
        env.pop("GLUDD_AUTH_PSK", None)
        env.pop("GLUDD_ALLOW_NO_AUTH", None)
        env["GLUDD_PSK_DISABLE"] = "1"
        with patch.dict("os.environ", env, clear=True):
            app = create_app(gateway=None)
            client = TestClient(app)
            resp = client.post("/jobs/validate", json=_EXEC_PAYLOAD)
        assert resp.status_code == 501, (
            f"disabled auth /jobs/validate returned {resp.status_code}, "
            "expected 501 (pass-through when auth disabled)"
        )

    def test_docs_prefix_collision_is_not_public(self):
        """SECURITY: ``/docs_evil`` must NOT inherit public status from ``/docs``.

        The public-path check historically used ``path.startswith("/docs")``,
        which let any path sharing the ``/docs`` prefix (e.g. ``/docs_evil``,
        ``/docsadmin``) bypass the PSK auth gate. Such paths must be rejected
        with 401 when a PSK is configured, not fall through to a 404 (which
        would mean auth was skipped).
        """
        with patch.dict("os.environ", {"GLUDD_AUTH_PSK": _PSK}):
            app = create_app(gateway=None)
            client = TestClient(app)
            resp = client.get("/docs_evil")
        assert resp.status_code == 401, (
            f"/docs_evil without PSK returned {resp.status_code}; a prefix-colliding "
            "path must NOT be treated as public (startswith('/docs') bypass)"
        )

    def test_docs_exact_and_subpath_remain_public(self):
        """The fix must not break legitimate public docs paths."""
        with patch.dict("os.environ", {"GLUDD_AUTH_PSK": _PSK}):
            app = create_app(gateway=None)
            client = TestClient(app)
            # /docs exact -> 200 (served by FastAPI's docs route)
            docs_resp = client.get("/docs")
            assert docs_resp.status_code != 401, (
                f"/docs returned {docs_resp.status_code}; exact /docs must stay public"
            )
            # /docs/ subpath -> not 401 (still public)
            docs_sub = client.get("/docs/")
            assert docs_sub.status_code != 401, (
                f"/docs/ returned {docs_sub.status_code}; /docs/ subpath must stay public"
            )
