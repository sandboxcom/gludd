"""Deep behavioral tests for the signing router (cosign + gitsign endpoints).

Covers: auth, resolver-unavailable, boundary conditions, response shapes,
error paths, and the full cosign/gitsign CRUD surface.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from general_ludd.routers.signing import _COSIGN_OUTPUT_ALLOWED_ROOT, register

_ADMIN_TOKEN = "deep-unit-test-admin"
_ADMIN_HEADERS = {"X-Admin-Token": _ADMIN_TOKEN}


# ── helpers ────────────────────────────────────────────────────────────────


def _make_app() -> tuple[FastAPI, MagicMock]:
    app = FastAPI()
    mock_resolver = MagicMock()
    app.state._secrets_resolver = mock_resolver
    register(app, {})
    return app, mock_resolver


def _make_client(app: FastAPI, headers: dict | None = None) -> TestClient:
    return TestClient(app, headers=headers or _ADMIN_HEADERS, raise_server_exceptions=True)


def _mock_cosign_key():
    from general_ludd.secrets.cosign import CosignKey

    return CosignKey(
        key_name="test-key",
        private_key="PRIVATE-BASE64",
        public_key="PUBLIC-BASE64",
        created_at="2026-01-01T00:00:00+00:00",
    )


def _mock_gitsign_config():
    from general_ludd.secrets.gitsign import GitsignConfig

    return GitsignConfig(
        fulcio_url="https://fulcio.sigstore.dev",
        rekor_url="https://rekor.sigstore.dev",
        oidc_issuer="https://oauth2.sigstore.dev/auth",
        key_ref="refs/heads/main",
        enabled=True,
    )


# ── fixtures ───────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GLUDD_ADMIN_TOKEN", _ADMIN_TOKEN)


# ── auth ───────────────────────────────────────────────────────────────────


class TestAuth:
    def test_admin_token_not_configured_returns_503(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("GLUDD_ADMIN_TOKEN", raising=False)
        app, _ = _make_app()
        client = _make_client(app)
        resp = client.get("/admin/signing/cosign/list/test-project")
        assert resp.status_code == 503
        assert "admin_token_required" in resp.json()["detail"]

    def test_missing_x_admin_token_header_returns_403(self) -> None:
        app, _ = _make_app()
        client = TestClient(app, raise_server_exceptions=True)
        resp = client.get("/admin/signing/cosign/list/test-project")
        assert resp.status_code == 403
        assert "invalid admin token" in resp.json()["detail"]

    def test_wrong_admin_token_returns_403(self) -> None:
        app, _ = _make_app()
        client = _make_client(app, {"X-Admin-Token": "wrong-token"})
        resp = client.get("/admin/signing/cosign/list/test-project")
        assert resp.status_code == 403
        assert "invalid admin token" in resp.json()["detail"]

    def test_empty_admin_token_header_returns_403(self) -> None:
        app, _ = _make_app()
        client = _make_client(app, {"X-Admin-Token": ""})
        resp = client.get("/admin/signing/cosign/list/test-project")
        assert resp.status_code == 403


# ── resolver unavailable ───────────────────────────────────────────────────


class TestResolverUnavailable:
    def test_no_resolver_on_app_state_cosign_generate(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GLUDD_ADMIN_TOKEN", _ADMIN_TOKEN)
        app = FastAPI()
        register(app, {})
        client = TestClient(app, headers=_ADMIN_HEADERS, raise_server_exceptions=True)
        resp = client.post("/admin/signing/cosign/generate", json={"project_id": "p", "key_name": "k"})
        assert resp.status_code == 503
        assert "secrets resolver not available" in resp.json()["error"]

    def test_no_resolver_on_app_state_cosign_list(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GLUDD_ADMIN_TOKEN", _ADMIN_TOKEN)
        app = FastAPI()
        register(app, {})
        client = TestClient(app, headers=_ADMIN_HEADERS, raise_server_exceptions=True)
        resp = client.get("/admin/signing/cosign/list/p")
        assert resp.status_code == 503
        assert "secrets resolver not available" in resp.json()["error"]

    def test_no_resolver_on_app_state_cosign_read(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GLUDD_ADMIN_TOKEN", _ADMIN_TOKEN)
        app = FastAPI()
        register(app, {})
        client = TestClient(app, headers=_ADMIN_HEADERS, raise_server_exceptions=True)
        resp = client.get("/admin/signing/cosign/p/k")
        assert resp.status_code == 503

    def test_no_resolver_on_app_state_cosign_delete(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GLUDD_ADMIN_TOKEN", _ADMIN_TOKEN)
        app = FastAPI()
        register(app, {})
        client = TestClient(app, headers=_ADMIN_HEADERS, raise_server_exceptions=True)
        resp = client.delete("/admin/signing/cosign/p/k")
        assert resp.status_code == 503

    def test_no_resolver_on_app_state_gitsign_write(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GLUDD_ADMIN_TOKEN", _ADMIN_TOKEN)
        app = FastAPI()
        register(app, {})
        client = TestClient(app, headers=_ADMIN_HEADERS, raise_server_exceptions=True)
        resp = client.post("/admin/signing/gitsign/config", json={"project_id": "p"})
        assert resp.status_code == 503

    def test_no_resolver_on_app_state_gitsign_read(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GLUDD_ADMIN_TOKEN", _ADMIN_TOKEN)
        app = FastAPI()
        register(app, {})
        client = TestClient(app, headers=_ADMIN_HEADERS, raise_server_exceptions=True)
        resp = client.get("/admin/signing/gitsign/p")
        assert resp.status_code == 503

    def test_resolver_missing_write_secret_generate(self) -> None:
        app, resolver = _make_app()
        del resolver.write_secret  # remove the attribute
        client = _make_client(app)
        resp = client.post("/admin/signing/cosign/generate", json={"project_id": "p", "key_name": "k"})
        assert resp.status_code == 503

    def test_resolver_missing_read_secret_list(self) -> None:
        app, resolver = _make_app()
        del resolver.read_secret
        client = _make_client(app)
        resp = client.get("/admin/signing/cosign/list/p")
        assert resp.status_code == 503

    def test_resolver_missing_read_secret_read(self) -> None:
        app, resolver = _make_app()
        del resolver.read_secret
        client = _make_client(app)
        resp = client.get("/admin/signing/cosign/p/k")
        assert resp.status_code == 503

    def test_resolver_missing_delete_secret_delete(self) -> None:
        app, resolver = _make_app()
        del resolver.delete_secret
        client = _make_client(app)
        resp = client.delete("/admin/signing/cosign/p/k")
        assert resp.status_code == 503

    def test_resolver_missing_write_secret_gitsign(self) -> None:
        app, resolver = _make_app()
        del resolver.write_secret
        client = _make_client(app)
        resp = client.post("/admin/signing/gitsign/config", json={"project_id": "p"})
        assert resp.status_code == 503

    def test_resolver_missing_read_secret_gitsign(self) -> None:
        app, resolver = _make_app()
        del resolver.read_secret
        client = _make_client(app)
        resp = client.get("/admin/signing/gitsign/p")
        assert resp.status_code == 503


# ── cosign generate ────────────────────────────────────────────────────────


class TestCosignGenerate:
    @staticmethod
    def _mock_key():
        return _mock_cosign_key()

    def test_generates_and_returns_key(self) -> None:
        app, _ = _make_app()
        mock_key = self._mock_key()
        with patch(
            "general_ludd.routers.signing.generate_and_store_cosign_key",
            return_value=mock_key,
        ):
            client = _make_client(app)
            resp = client.post(
                "/admin/signing/cosign/generate",
                json={"project_id": "proj", "key_name": "my-key"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["key_name"] == "test-key"
        assert data["public_key"] == "PUBLIC-BASE64"
        assert "created_at" in data

    def test_calls_generate_with_default_project_id(self) -> None:
        app, _ = _make_app()
        mock_key = self._mock_key()
        with patch(
            "general_ludd.routers.signing.generate_and_store_cosign_key",
            return_value=mock_key,
        ) as mock_gen:
            client = _make_client(app)
            resp = client.post(
                "/admin/signing/cosign/generate",
                json={"key_name": "k"},
            )
        assert resp.status_code == 200
        assert mock_gen.call_args.kwargs["project_id"] == "default"

    def test_calls_generate_with_password(self) -> None:
        app, _ = _make_app()
        mock_key = self._mock_key()
        with patch(
            "general_ludd.routers.signing.generate_and_store_cosign_key",
            return_value=mock_key,
        ) as mock_gen:
            client = _make_client(app)
            resp = client.post(
                "/admin/signing/cosign/generate",
                json={"project_id": "p", "key_name": "k", "password": "s3cret"},
            )
        assert resp.status_code == 200
        assert mock_gen.call_args.kwargs["password"] == "s3cret"

    def test_calls_generate_with_output_dir(self) -> None:
        app, _ = _make_app()
        mock_key = self._mock_key()
        safe_dir = os.path.join(_COSIGN_OUTPUT_ALLOWED_ROOT, "cosign", "p")
        with patch(
            "general_ludd.routers.signing.generate_and_store_cosign_key",
            return_value=mock_key,
        ) as mock_gen:
            client = _make_client(app)
            resp = client.post(
                "/admin/signing/cosign/generate",
                json={"project_id": "p", "key_name": "k", "output_dir": safe_dir},
            )
        assert resp.status_code == 200
        assert mock_gen.call_args.kwargs["output_dir"] == os.path.realpath(safe_dir)

    def test_invalid_project_id_regex_returns_400(self) -> None:
        app, _ = _make_app()
        client = _make_client(app)
        resp = client.post(
            "/admin/signing/cosign/generate",
            json={"project_id": "bad.id", "key_name": "k"},
        )
        assert resp.status_code == 400

    def test_invalid_key_name_regex_returns_400(self) -> None:
        app, _ = _make_app()
        client = _make_client(app)
        resp = client.post(
            "/admin/signing/cosign/generate",
            json={"project_id": "p", "key_name": "bad/name"},
        )
        assert resp.status_code == 400

    def test_output_dir_outside_allowed_root_returns_400(self) -> None:
        app, _ = _make_app()
        client = _make_client(app)
        resp = client.post(
            "/admin/signing/cosign/generate",
            json={"project_id": "p", "key_name": "k", "output_dir": "/etc"},
        )
        assert resp.status_code == 400
        assert "outside the allowed root" in resp.json()["error"]

    def test_completely_empty_body_uses_defaults(self) -> None:
        app, _ = _make_app()
        mock_key = self._mock_key()
        with patch(
            "general_ludd.routers.signing.generate_and_store_cosign_key",
            return_value=mock_key,
        ) as mock_gen:
            client = _make_client(app)
            resp = client.post("/admin/signing/cosign/generate", json={})
        assert resp.status_code == 200
        assert mock_gen.call_args.kwargs["project_id"] == "default"
        assert mock_gen.call_args.kwargs["key_name"] == "cosign-key"


# ── cosign list ────────────────────────────────────────────────────────────


class TestCosignList:
    def test_lists_keys_for_project(self) -> None:
        app, resolver = _make_app()
        resolver.list_secrets = MagicMock(
            return_value=[
                "projects/test-proj/cosign/key-a",
                "projects/test-proj/cosign/key-b",
            ]
        )

        with patch(
            "general_ludd.routers.signing.read_cosign_key",
            side_effect=[
                _mock_cosign_key(),
                _mock_cosign_key(),
            ],
        ):
            client = _make_client(app)
            resp = client.get("/admin/signing/cosign/list/test-proj")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        assert data[0]["key_name"] == "test-key"

    def test_no_list_secrets_falls_back(self) -> None:
        app, resolver = _make_app()
        del resolver.list_secrets
        assert not hasattr(resolver, "list_secrets")
        client = _make_client(app)
        resp = client.get("/admin/signing/cosign/list/p")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_invalid_project_id_regex_400(self) -> None:
        app, resolver = _make_app()
        resolver.list_secrets = MagicMock(return_value=["projects/bad.id/cosign/k"])
        resolver.read_secret = MagicMock(
            return_value={"key_name": "k", "private_key": "p", "public_key": "pb", "created_at": "t"}
        )
        client = _make_client(app)
        resp = client.get("/admin/signing/cosign/list/bad.id")
        assert resp.status_code == 400

    def test_read_key_returns_none_skipped(self) -> None:
        app, resolver = _make_app()
        resolver.list_secrets = MagicMock(
            return_value=[
                "projects/p/cosign/key-a",
                "projects/p/cosign/key-b",
            ]
        )
        with patch(
            "general_ludd.routers.signing.read_cosign_key",
            side_effect=[_mock_cosign_key(), None],  # second key missing
        ):
            client = _make_client(app)
            resp = client.get("/admin/signing/cosign/list/p")
        data = resp.json()
        assert len(data) == 1


# ── cosign read ────────────────────────────────────────────────────────────


class TestCosignRead:
    def test_reads_existing_key(self) -> None:
        app, _ = _make_app()
        mock_key = _mock_cosign_key()
        with patch(
            "general_ludd.routers.signing.read_cosign_key",
            return_value=mock_key,
        ):
            client = _make_client(app)
            resp = client.get("/admin/signing/cosign/my-proj/my-key")
        assert resp.status_code == 200
        data = resp.json()
        assert data["key_name"] == "test-key"
        assert data["public_key"] == "PUBLIC-BASE64"

    def test_key_not_found_returns_404(self) -> None:
        app, _ = _make_app()
        with patch(
            "general_ludd.routers.signing.read_cosign_key",
            return_value=None,
        ):
            client = _make_client(app)
            resp = client.get("/admin/signing/cosign/p/k")
        assert resp.status_code == 404
        assert "key not found" in resp.json()["error"]

    def test_invalid_project_id_400(self) -> None:
        app, _ = _make_app()
        client = _make_client(app)
        resp = client.get("/admin/signing/cosign/bad.id/k")
        assert resp.status_code == 400


# ── cosign delete ──────────────────────────────────────────────────────────


class TestCosignDelete:
    def test_deletes_key_and_returns_status(self) -> None:
        app, _ = _make_app()
        with patch(
            "general_ludd.routers.signing.delete_cosign_key",
            return_value=None,
        ):
            client = _make_client(app)
            resp = client.delete("/admin/signing/cosign/proj/my-key")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "deleted"
        assert data["project_id"] == "proj"
        assert data["key_name"] == "my-key"

    def test_invalid_project_id_400(self) -> None:
        app, _ = _make_app()
        client = _make_client(app)
        resp = client.delete("/admin/signing/cosign/bad.id/k")
        assert resp.status_code == 400

    def test_invalid_key_name_400(self) -> None:
        app, _ = _make_app()
        client = _make_client(app)
        resp = client.delete("/admin/signing/cosign/p/bad.name")
        assert resp.status_code == 400


# ── gitsign write ──────────────────────────────────────────────────────────


class TestGitsignWrite:
    def test_writes_config_with_all_fields(self) -> None:
        app, _ = _make_app()
        with patch(
            "general_ludd.routers.signing.write_gitsign_config",
            return_value=None,
        ) as mock_write:
            client = _make_client(app)
            resp = client.post(
                "/admin/signing/gitsign/config",
                json={
                    "project_id": "p",
                    "fulcio_url": "https://f.example.com",
                    "rekor_url": "https://r.example.com",
                    "oidc_issuer": "https://i.example.com",
                    "key_ref": "refs/heads/main",
                    "enabled": False,
                },
            )
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}
        kwargs = mock_write.call_args.kwargs
        assert kwargs["project_id"] == "p"
        assert kwargs["fulcio_url"] == "https://f.example.com"
        assert kwargs["rekor_url"] == "https://r.example.com"
        assert kwargs["oidc_issuer"] == "https://i.example.com"
        assert kwargs["key_ref"] == "refs/heads/main"
        assert kwargs["enabled"] is False

    def test_default_values(self) -> None:
        app, _ = _make_app()
        with patch(
            "general_ludd.routers.signing.write_gitsign_config",
            return_value=None,
        ) as mock_write:
            client = _make_client(app)
            resp = client.post("/admin/signing/gitsign/config", json={})
        assert resp.status_code == 200
        kwargs = mock_write.call_args.kwargs
        assert kwargs["project_id"] == "default"
        assert kwargs["fulcio_url"] == "https://fulcio.sigstore.dev"
        assert kwargs["rekor_url"] == "https://rekor.sigstore.dev"
        assert kwargs["enabled"] is True

    def test_invalid_project_id_400(self) -> None:
        app, _ = _make_app()
        client = _make_client(app)
        resp = client.post(
            "/admin/signing/gitsign/config",
            json={"project_id": "invalid/id"},
        )
        assert resp.status_code == 400

    def test_empty_body_uses_defaults(self) -> None:
        app, _ = _make_app()
        with patch(
            "general_ludd.routers.signing.write_gitsign_config",
            return_value=None,
        ) as mock_write:
            client = _make_client(app)
            resp = client.post("/admin/signing/gitsign/config", json={})
        assert resp.status_code == 200
        assert mock_write.call_args.kwargs["key_ref"] == ""


# ── gitsign read ───────────────────────────────────────────────────────────


class TestGitsignRead:
    def test_reads_config(self) -> None:
        app, _ = _make_app()
        config = _mock_gitsign_config()
        with patch(
            "general_ludd.routers.signing.read_gitsign_config",
            return_value=config,
        ):
            client = _make_client(app)
            resp = client.get("/admin/signing/gitsign/p")
        assert resp.status_code == 200
        data = resp.json()
        assert data["fulcio_url"] == "https://fulcio.sigstore.dev"
        assert data["enabled"] is True

    def test_config_not_found_404(self) -> None:
        app, _ = _make_app()
        with patch(
            "general_ludd.routers.signing.read_gitsign_config",
            return_value=None,
        ):
            client = _make_client(app)
            resp = client.get("/admin/signing/gitsign/p")
        assert resp.status_code == 404
        assert "gitsign config not found" in resp.json()["error"]

    def test_invalid_project_id_400(self) -> None:
        app, _ = _make_app()
        client = _make_client(app)
        resp = client.get("/admin/signing/gitsign/bad.id")
        assert resp.status_code == 400


# ── response shape assertions ──────────────────────────────────────────────


class TestResponseShapes:
    def test_generate_response_has_required_keys(self) -> None:
        app, _ = _make_app()
        mock_key = _mock_cosign_key()
        with patch(
            "general_ludd.routers.signing.generate_and_store_cosign_key",
            return_value=mock_key,
        ):
            client = _make_client(app)
            resp = client.post(
                "/admin/signing/cosign/generate",
                json={"project_id": "p", "key_name": "k"},
            )
        data = resp.json()
        assert set(data.keys()) == {"key_name", "public_key", "created_at"}

    def test_list_response_is_array(self) -> None:
        app, resolver = _make_app()
        resolver.list_secrets = MagicMock(
            return_value=[
                "projects/p/cosign/k",
            ]
        )
        with patch(
            "general_ludd.routers.signing.read_cosign_key",
            return_value=_mock_cosign_key(),
        ):
            client = _make_client(app)
            resp = client.get("/admin/signing/cosign/list/p")
        assert isinstance(resp.json(), list)

    def test_read_response_has_required_keys(self) -> None:
        app, _ = _make_app()
        with patch(
            "general_ludd.routers.signing.read_cosign_key",
            return_value=_mock_cosign_key(),
        ):
            client = _make_client(app)
            resp = client.get("/admin/signing/cosign/p/k")
        data = resp.json()
        assert set(data.keys()) == {"key_name", "public_key", "created_at"}

    def test_gitsign_read_response_shape(self) -> None:
        app, _ = _make_app()
        with patch(
            "general_ludd.routers.signing.read_gitsign_config",
            return_value=_mock_gitsign_config(),
        ):
            client = _make_client(app)
            resp = client.get("/admin/signing/gitsign/p")
        data = resp.json()
        assert set(data.keys()) == {"fulcio_url", "rekor_url", "oidc_issuer", "key_ref", "enabled"}


# ── _COSIGN_OUTPUT_ALLOWED_ROOT invariant ──────────────────────────────────


def test_allowed_root_is_non_empty_realpath() -> None:
    assert _COSIGN_OUTPUT_ALLOWED_ROOT
    assert os.path.isabs(_COSIGN_OUTPUT_ALLOWED_ROOT)
    assert os.path.realpath(_COSIGN_OUTPUT_ALLOWED_ROOT) == _COSIGN_OUTPUT_ALLOWED_ROOT
    assert ".local/share/general-ludd/filestore" in _COSIGN_OUTPUT_ALLOWED_ROOT
