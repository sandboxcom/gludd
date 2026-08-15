"""H.18: /admin/signing/* privilege separation — admin-token guardrail.

Verifies that every /admin/signing/* endpoint requires a GLUDD_ADMIN_TOKEN
(separate from the shared PSK) via the X-Admin-Token header.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _make_secrets_resolver() -> MagicMock:
    mgr = MagicMock()
    mgr.write_secret = MagicMock()
    mgr.read_secret = MagicMock(return_value={
        "key_name": "test-key", "public_key": "fake-pub", "created_at": "2025-01-01T00:00:00Z",
    })
    mgr.delete_secret = MagicMock()
    mgr.list_secrets = MagicMock(return_value=[])
    return mgr


def _make_app() -> FastAPI:
    import general_ludd.routers.signing as signing_router

    app = FastAPI()
    app.state._secrets_resolver = _make_secrets_resolver()
    signing_router.register(app, {})
    return app


# ── endpoints under test ────────────────────────────────────────────────

_SIGNING_ENDPOINTS = [
    ("POST", "/admin/signing/cosign/generate", {"project_id": "p1", "key_name": "k1"}),
    ("GET", "/admin/signing/cosign/list/p1", None),
    ("GET", "/admin/signing/cosign/p1/k1", None),
    ("DELETE", "/admin/signing/cosign/p1/k1", None),
    ("POST", "/admin/signing/gitsign/config", {"project_id": "p1"}),
    ("GET", "/admin/signing/gitsign/p1", None),
]


# ── tests ───────────────────────────────────────────────────────────────

class TestAdminTokenRequired:
    """When GLUDD_ADMIN_TOKEN is NOT set, all signing endpoints fail 503."""

    @pytest.mark.parametrize("method,path,body", _SIGNING_ENDPOINTS)
    def test_missing_admin_token_returns_503(
        self, method: str, path: str, body: dict | None, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("GLUDD_ADMIN_TOKEN", raising=False)
        app = _make_app()
        client = TestClient(app)
        kwargs = {"json": body} if body else {}
        resp = getattr(client, method.lower())(path, **kwargs)
        assert resp.status_code == 503
        assert "admin_token" in resp.text.lower()


class TestAdminTokenWrong:
    """A wrong X-Admin-Token value is rejected (403)."""

    @pytest.mark.parametrize("method,path,body", _SIGNING_ENDPOINTS)
    def test_wrong_token_returns_403(
        self, method: str, path: str, body: dict | None, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("GLUDD_ADMIN_TOKEN", "correct-secret")
        app = _make_app()
        client = TestClient(app)
        kwargs = {"json": body} if body else {}
        kwargs["headers"] = {"X-Admin-Token": "wrong-secret"}
        resp = getattr(client, method.lower())(path, **kwargs)
        assert resp.status_code == 403


class TestAdminTokenCorrect:
    """When GLUDD_ADMIN_TOKEN matches X-Admin-Token, the endpoint proceeds."""

    @pytest.mark.parametrize("method,path,body", _SIGNING_ENDPOINTS)
    def test_correct_token_allows_request(
        self, method: str, path: str, body: dict | None, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("GLUDD_ADMIN_TOKEN", "correct-secret")
        app = _make_app()
        client = TestClient(app)
        kwargs = {"json": body} if body else {}
        kwargs["headers"] = {"X-Admin-Token": "correct-secret"}
        resp = getattr(client, method.lower())(path, **kwargs)
        assert resp.status_code != 403
        assert resp.status_code != 503


class TestAdminTokenIndependentOfPsk:
    """The admin token is a separate privilege tier — PSK alone is NOT enough."""

    @pytest.mark.parametrize("method,path,body", _SIGNING_ENDPOINTS)
    def test_psk_alone_is_insufficient(
        self, method: str, path: str, body: dict | None, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("GLUDD_ADMIN_TOKEN", "admin-secret")
        monkeypatch.setenv("GLUDD_AUTH_PSK", "shared-psk")
        app = _make_app()
        client = TestClient(app)
        kwargs = {"json": body} if body else {}
        # Send the PSK as bearer but no X-Admin-Token — should still fail
        kwargs["headers"] = {"Authorization": "Bearer shared-psk"}
        resp = getattr(client, method.lower())(path, **kwargs)
        # When GLUDD_ADMIN_TOKEN IS configured, missing header = 403 (wrong token).
        # When NOT configured, it would be 503 — these tests prove PSK alone
        # does not grant access to signing endpoints regardless.
        assert resp.status_code in (403, 503)


class TestAdminTokenConstantTime:
    """check_admin_token uses hmac.compare_digest for timing-safe comparison."""

    def test_uses_constant_time_comparison(self) -> None:
        import hmac

        from general_ludd.security.auth import check_admin_token
        with patch.object(hmac, "compare_digest", wraps=hmac.compare_digest) as spy:
            check_admin_token("hello", "hello")
            spy.assert_called_once()

    def test_empty_token_returns_false(self) -> None:
        from general_ludd.security.auth import check_admin_token
        assert check_admin_token("", "secret") is False
        assert check_admin_token("token", "") is False
        assert check_admin_token("", "") is False

    def test_mismatched_token_returns_false(self) -> None:
        from general_ludd.security.auth import check_admin_token
        assert check_admin_token("abc", "xyz") is False

    def test_matched_token_returns_true(self) -> None:
        from general_ludd.security.auth import check_admin_token
        assert check_admin_token("secret", "secret") is True

    def test_reads_from_env_by_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from general_ludd.security.auth import check_admin_token
        monkeypatch.setenv("GLUDD_ADMIN_TOKEN", "env-secret")
        assert check_admin_token("env-secret") is True
        assert check_admin_token("wrong") is False
