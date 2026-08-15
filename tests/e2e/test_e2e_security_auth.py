"""E2E tests for the daemon PSK auth middleware and security auth primitives.

Tests the auth.py functions through the daemon's actual middleware (TestClient)
and direct integration for SSRF + path-containment guards.

Unit tests already cover: load_auth_posture env resolution, verify_psk constant-
time comparison, the is_path_within alias. These e2e tests exercise the daemon's
``auth_and_stats_middleware`` for 401/403/503 behaviour and the SSRF guard with
realistic URL payloads.
"""

from __future__ import annotations

import os
import tempfile

import pytest
from fastapi.testclient import TestClient

from general_ludd.security.auth import (
    check_bearer_token,
    is_join_within,
    is_path_within,
    is_safe_fetch_url,
    verify_psk,
)

_PSK = "e2e-test-psk-secret"


class _LiveLoopTask:
    def done(self) -> bool:
        return False

    def cancelled(self) -> bool:
        return False


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def daemon_with_psk(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("GLUDD_AUTH_PSK", _PSK)
    monkeypatch.setenv("GLUDD_ALLOW_NO_AUTH", "0")
    monkeypatch.delenv("GLUDD_PSK_DISABLE", raising=False)
    monkeypatch.delenv("GLUDD_REQUIRE_AUTH", raising=False)

    from general_ludd.daemon import create_daemon_app

    app = create_daemon_app(tick_interval=0.0)
    return TestClient(app)


@pytest.fixture
def daemon_no_psk_fail_closed(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.delenv("GLUDD_AUTH_PSK", raising=False)
    monkeypatch.delenv("GLUDD_PSK_DISABLE", raising=False)
    monkeypatch.setenv("GLUDD_ALLOW_NO_AUTH", "0")
    monkeypatch.delenv("GLUDD_PSK_DISABLE", raising=False)
    monkeypatch.delenv("GLUDD_REQUIRE_AUTH", raising=False)

    from general_ludd.daemon import create_daemon_app

    app = create_daemon_app(tick_interval=0.0)
    return TestClient(app)


@pytest.fixture
def daemon_no_psk_dev_allow(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.delenv("GLUDD_AUTH_PSK", raising=False)
    monkeypatch.setenv("GLUDD_ALLOW_NO_AUTH", "1")
    monkeypatch.delenv("GLUDD_PSK_DISABLE", raising=False)
    monkeypatch.delenv("GLUDD_REQUIRE_AUTH", raising=False)

    from general_ludd.daemon import create_daemon_app

    app = create_daemon_app(tick_interval=0.0)
    return TestClient(app)


@pytest.fixture
def daemon_with_require_auth(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("GLUDD_AUTH_PSK", _PSK)
    monkeypatch.setenv("GLUDD_REQUIRE_AUTH", "1")
    monkeypatch.setenv("GLUDD_ALLOW_NO_AUTH", "1")
    monkeypatch.delenv("GLUDD_PSK_DISABLE", raising=False)

    from general_ludd.daemon import create_daemon_app

    app = create_daemon_app(tick_interval=0.0)
    return TestClient(app)


def _auth_header() -> dict[str, str]:
    return {"Authorization": f"Bearer {_PSK}"}


def _wrong_auth_header() -> dict[str, str]:
    return {"Authorization": "Bearer wrong-key"}


# ---------------------------------------------------------------------------
# Daemon PSK middleware — happy path
# ---------------------------------------------------------------------------


class TestPSKAuthHappyPath:
    def test_public_healthz_no_auth_needed(self, daemon_with_psk: TestClient):
        resp = daemon_with_psk.get("/healthz")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] in {"healthy", "degraded"}

    def test_public_readyz_no_auth_needed(self, daemon_with_psk: TestClient):
        daemon_with_psk.app.state._event_loop_task = _LiveLoopTask()
        resp = daemon_with_psk.get("/readyz")
        assert resp.status_code == 200

    def test_public_get_api_status_no_auth(self, daemon_with_psk: TestClient):
        resp = daemon_with_psk.get("/api/status")
        assert resp.status_code == 200
        assert "version" in resp.json()

    def test_admin_projects_with_correct_psk(self, daemon_with_psk: TestClient):
        resp = daemon_with_psk.get("/admin/projects", headers=_auth_header())
        assert resp.status_code == 200

    def test_psk_admin_can_decide_deployment_fixes(
        self,
        daemon_with_psk: TestClient,
    ) -> None:
        request = {
            "deployment": {
                "engine": "vllm",
                "gpu_memory_utilization": 0.99,
                "quantization": "awq",
            },
            "gpu_type": "a100_80",
        }

        for decision, expected_status in (
            ("approve", "approved"),
            ("reject", "rejected"),
        ):
            suggestion = daemon_with_psk.post(
                "/admin/deployments/suggest-fix",
                json=request,
                headers=_auth_header(),
            )
            assert suggestion.status_code == 200

            response = daemon_with_psk.post(
                f"/admin/deployments/fixes/{suggestion.json()['fix_id']}/{decision}",
                headers=_auth_header(),
            )
            assert response.status_code == 200
            assert response.json()["status"] == expected_status

    def test_public_healthz_exposes_security_posture(self, daemon_with_psk: TestClient):
        resp = daemon_with_psk.get("/healthz")
        assert resp.status_code == 200
        data = resp.json()
        assert "no_auth" in data
        assert "require_auth" in data
        assert "allow_no_auth" in data


# ---------------------------------------------------------------------------
# Daemon PSK middleware — auth failures
# ---------------------------------------------------------------------------


class TestPSKAuthFailures:
    def test_admin_projects_no_auth_header_401(self, daemon_with_psk: TestClient):
        resp = daemon_with_psk.get("/admin/projects")
        assert resp.status_code == 401
        assert "unauthorized" in resp.json()["error"]

    def test_admin_projects_wrong_psk_401(self, daemon_with_psk: TestClient):
        resp = daemon_with_psk.get(
            "/admin/projects", headers=_wrong_auth_header()
        )
        assert resp.status_code == 401

    def test_admin_projects_malformed_auth_401(self, daemon_with_psk: TestClient):
        resp = daemon_with_psk.get(
            "/admin/projects", headers={"Authorization": "no-bearer-prefix"}
        )
        assert resp.status_code == 401

    def test_admin_projects_empty_auth_401(self, daemon_with_psk: TestClient):
        resp = daemon_with_psk.get(
            "/admin/projects", headers={"Authorization": "Bearer "}
        )
        assert resp.status_code == 401

    def test_mutating_method_on_public_path_401(self, daemon_with_psk: TestClient):
        resp = daemon_with_psk.post("/api/todos", json={"title": "x"})
        assert resp.status_code == 401

    def test_mutating_public_path_with_correct_psk(self, daemon_with_psk: TestClient):
        resp = daemon_with_psk.post(
            "/api/todos",
            json={
                "title": "auth test",
                "queue": "core",
                "priority": "medium",
                "work_type": "code",
            },
            headers=_auth_header(),
        )
        assert resp.status_code in {201, 200}


# ---------------------------------------------------------------------------
# Daemon PSK middleware — fail-closed (no PSK)
# ---------------------------------------------------------------------------


class TestNoPSKFailClosed:
    def test_non_public_path_503_no_psk(self, daemon_no_psk_fail_closed: TestClient):
        resp = daemon_no_psk_fail_closed.get("/admin/projects")
        assert resp.status_code == 503
        data = resp.json()
        assert data["error"] == "auth_required"

    def test_healthz_still_200_no_psk(self, daemon_no_psk_fail_closed: TestClient):
        resp = daemon_no_psk_fail_closed.get("/healthz")
        assert resp.status_code == 200

    def test_readyz_still_200_no_psk(self, daemon_no_psk_fail_closed: TestClient):
        daemon_no_psk_fail_closed.app.state._event_loop_task = _LiveLoopTask()
        resp = daemon_no_psk_fail_closed.get("/readyz")
        assert resp.status_code == 200

    def test_mutating_public_path_503_no_psk(self, daemon_no_psk_fail_closed: TestClient):
        resp = daemon_no_psk_fail_closed.post(
            "/api/todos", json={"title": "should block"}
        )
        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# Daemon PSK middleware — dev allow (no PSK + GLUDD_ALLOW_NO_AUTH=1)
# ---------------------------------------------------------------------------


class TestNoPSKDevAllow:
    def test_admin_projects_allowed_dev_mode(self, daemon_no_psk_dev_allow: TestClient):
        resp = daemon_no_psk_dev_allow.get("/admin/projects")
        assert resp.status_code == 200

    def test_healthz_allowed_dev_mode(self, daemon_no_psk_dev_allow: TestClient):
        resp = daemon_no_psk_dev_allow.get("/healthz")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Daemon PSK middleware — cross-tenant project scoping
# ---------------------------------------------------------------------------


class TestCrossTenantProjectToken:
    def test_admin_endpoint_with_project_scoped_token(
        self, daemon_with_psk: TestClient
    ):
        resp = daemon_with_psk.get(
            "/admin/projects",
            headers={"Authorization": f"Bearer test-project:{_PSK}"},
        )
        assert resp.status_code == 200

    def test_admin_endpoint_with_colon_wrong_psk(
        self, daemon_with_psk: TestClient
    ):
        resp = daemon_with_psk.get(
            "/admin/projects",
            headers={"Authorization": "Bearer bad-project:wrong-psk"},
        )
        assert resp.status_code == 401

    def test_admin_endpoint_with_colon_empty_project_auth(
        self, daemon_with_psk: TestClient
    ):
        resp = daemon_with_psk.get(
            "/admin/projects",
            headers={"Authorization": f"Bearer :{_PSK}"},
        )
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# check_bearer_token / verify_psk — direct integration
# ---------------------------------------------------------------------------


class TestCheckBearerTokenIntegration:
    def test_matching_bearer_token(self):
        assert check_bearer_token(f"Bearer {_PSK}", _PSK) is True

    def test_mismatched_bearer_token(self):
        assert check_bearer_token("Bearer wrong", _PSK) is False

    def test_no_bearer_prefix(self):
        assert check_bearer_token(_PSK, _PSK) is False

    def test_empty_header(self):
        assert check_bearer_token("", _PSK) is False

    def test_empty_expected_key(self):
        assert check_bearer_token(f"Bearer {_PSK}", "") is False

    def test_none_header(self):
        assert check_bearer_token(None, _PSK) is False  # type: ignore[arg-type]


class TestVerifyPSKIntegration:
    def test_matching_keys(self):
        assert verify_psk(_PSK, _PSK) is True

    def test_mismatched_keys(self):
        assert verify_psk("wrong", _PSK) is False

    def test_empty_presented(self):
        assert verify_psk("", _PSK) is False

    def test_empty_expected(self):
        assert verify_psk(_PSK, "") is False

    def test_both_empty(self):
        assert verify_psk("", "") is False


# ---------------------------------------------------------------------------
# SSRF guard — is_safe_fetch_url
# ---------------------------------------------------------------------------


class TestSSRFGuardE2E:
    def test_allows_valid_https_public_url(self):
        assert is_safe_fetch_url("https://example.com/api") is True
        assert is_safe_fetch_url("https://github.com/org/repo/skills") is True
        assert is_safe_fetch_url("https://api.openai.com/v1/models") is True

    def test_rejects_http_scheme(self):
        assert is_safe_fetch_url("http://example.com") is False
        assert is_safe_fetch_url("http://localhost:8080") is False

    def test_rejects_loopback_hosts(self):
        assert is_safe_fetch_url("https://localhost") is False
        assert is_safe_fetch_url("https://127.0.0.1") is False
        assert is_safe_fetch_url("https://[::1]") is False

    def test_rejects_metadata_ips(self):
        assert is_safe_fetch_url("https://169.254.169.254") is False
        assert is_safe_fetch_url("https://100.100.100.200") is False

    def test_rejects_private_ranges(self):
        assert is_safe_fetch_url("https://192.168.1.1") is False
        assert is_safe_fetch_url("https://10.0.0.1") is False
        assert is_safe_fetch_url("https://172.16.0.1") is False

    def test_rejects_empty_input(self):
        assert is_safe_fetch_url("") is False
        assert is_safe_fetch_url(None) is False  # type: ignore[arg-type]

    def test_rejects_non_url_garbage(self):
        assert is_safe_fetch_url("not-a-url") is False
        assert is_safe_fetch_url("ftp://example.com") is False

    def test_rejects_localhost_subdomains(self):
        assert is_safe_fetch_url("https://api.svc.localhost") is False
        assert is_safe_fetch_url("https://foo.bar.localhost:8443") is False

    def test_rejects_single_label_hostnames(self):
        assert is_safe_fetch_url("https://prometheus:9090") is False
        assert is_safe_fetch_url("https://vault/api") is False

    def test_rejects_metadata_hostnames(self):
        assert is_safe_fetch_url("https://metadata.google.internal") is False
        assert is_safe_fetch_url("https://metadata.azure.com") is False

    def test_rejects_numeric_ip_encodings(self):
        assert is_safe_fetch_url("https://2130706433") is False


# ---------------------------------------------------------------------------
# Path containment — is_join_within / is_path_within
# ---------------------------------------------------------------------------


class TestPathContainmentE2E:
    def test_sub_path_allowed(self):
        with tempfile.TemporaryDirectory() as base:
            assert is_join_within(base, "subdir") is True
            assert is_join_within(base, "subdir/file.txt") is True

    def test_escape_rejected(self):
        with tempfile.TemporaryDirectory() as base:
            assert is_join_within(base, "../escape") is False
            assert is_join_within(base, "../../etc/passwd") is False

    def test_absolute_path_rejected(self):
        with tempfile.TemporaryDirectory() as base:
            assert is_join_within(base, "/etc/passwd") is False

    def test_empty_candidate_is_base_itself(self):
        with tempfile.TemporaryDirectory() as base:
            assert is_join_within(base, "") is True

    def test_alias_is_same_function(self):
        assert is_path_within is is_join_within

    def test_alias_works(self):
        with tempfile.TemporaryDirectory() as base:
            assert is_path_within(base, "sub.txt") is True
            assert is_path_within(base, "../nope") is False

    def test_symlink_escape_rejected(self):
        with tempfile.TemporaryDirectory() as outer:
            inner = os.path.join(outer, "inner")
            os.makedirs(inner, exist_ok=True)
            escape = os.path.join(outer, "escape")
            os.makedirs(escape, exist_ok=True)
            symlink = os.path.join(inner, "link")
            os.symlink(escape, symlink)
            assert is_join_within(inner, "link") is False

    def test_mixed_drive_component_rejected(self):
        with tempfile.TemporaryDirectory() as base:
            assert is_join_within(base, "\x00bad") is False
