"""Unit tests for general_ludd/routers/signing.py — target >85% coverage.

Covers:
  - register() wires up all 6 routes
  - /admin/signing/cosign/generate  (happy path, no resolver 503, resolver without write_secret 503)
  - /admin/signing/cosign/list/{project_id}  (happy path with list_secrets, resolver missing 503,
      resolver without list_secrets attribute)
  - /admin/signing/cosign/{project_id}/{key_name} GET  (happy path, 404, 503)
  - /admin/signing/cosign/{project_id}/{key_name} DELETE  (happy path, 503)
  - /admin/signing/gitsign/config  POST  (happy path, 503)
  - /admin/signing/gitsign/{project_id}  GET  (happy path, 404, 503)
"""
from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from general_ludd.routers.signing import register
from general_ludd.secrets.cosign import CosignKey
from general_ludd.secrets.gitsign import GitsignConfig

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_app(resolver: Any = None) -> FastAPI:
    """Build a minimal FastAPI app with the signing router registered."""
    app = FastAPI()
    if resolver is not None:
        app.state._secrets_resolver = resolver
    register(app, {})
    return app


def _cosign_key(name: str = "k1", pub: str = "PUB", created: str = "2024-01-01") -> CosignKey:
    return CosignKey(key_name=name, private_key="PRIV", public_key=pub, created_at=created)  # pragma: allowlist secret


# ---------------------------------------------------------------------------
# /admin/signing/cosign/generate  POST
# ---------------------------------------------------------------------------

class TestCosignGenerate:
    _path = "/admin/signing/cosign/generate"

    def test_happy_path_defaults(self) -> None:
        resolver = MagicMock(spec=["write_secret"])
        key = _cosign_key()
        with patch(
            "general_ludd.routers.signing.generate_and_store_cosign_key",
            return_value=key,
        ) as mock_gen:
            client = TestClient(_make_app(resolver))
            resp = client.post(self._path, json={})
        assert resp.status_code == 200
        body = resp.json()
        assert body["key_name"] == "k1"
        assert body["public_key"] == "PUB"
        assert body["created_at"] == "2024-01-01"
        mock_gen.assert_called_once_with(
            mgr=resolver,
            project_id="default",
            key_name="cosign-key",
            output_dir=None,
            password=None,
        )

    def test_happy_path_explicit_fields(self) -> None:
        resolver = MagicMock(spec=["write_secret"])
        key = _cosign_key("mykey", "MYPUB", "2025-06-01")
        with patch(
            "general_ludd.routers.signing.generate_and_store_cosign_key",
            return_value=key,
        ):
            client = TestClient(_make_app(resolver))
            resp = client.post(
                self._path,
                json={
                    "project_id": "proj-a",
                    "key_name": "mykey",
                    "output_dir": "/tmp/keys",
                    "password": "s3cr3t",  # pragma: allowlist secret
                },
            )
        assert resp.status_code == 200
        assert resp.json()["key_name"] == "mykey"

    def test_503_no_resolver(self) -> None:
        client = TestClient(_make_app(resolver=None))
        resp = client.post(self._path, json={})
        assert resp.status_code == 503
        assert "secrets resolver" in resp.json()["error"]

    def test_503_resolver_missing_write_secret(self) -> None:
        # resolver exists but has no write_secret attribute
        resolver = MagicMock(spec=["read_secret"])
        client = TestClient(_make_app(resolver))
        resp = client.post(self._path, json={})
        assert resp.status_code == 503
        assert "secrets resolver" in resp.json()["error"]


# ---------------------------------------------------------------------------
# /admin/signing/cosign/list/{project_id}  GET
# ---------------------------------------------------------------------------

class TestCosignList:
    def _path(self, pid: str = "proj1") -> str:
        return f"/admin/signing/cosign/list/{pid}"

    def test_happy_path_returns_keys(self) -> None:
        key = _cosign_key("k1", "PUB1", "2024-01-01")
        resolver = MagicMock(spec=["read_secret", "list_secrets"])
        resolver.list_secrets.return_value = ["projects/proj1/cosign/k1"]
        with patch("general_ludd.routers.signing.read_cosign_key", return_value=key):
            client = TestClient(_make_app(resolver))
            resp = client.get(self._path("proj1"))
        assert resp.status_code == 200
        body = resp.json()
        assert len(body) == 1
        assert body[0]["key_name"] == "k1"
        assert body[0]["public_key"] == "PUB1"

    def test_happy_path_read_cosign_key_returns_none(self) -> None:
        """Keys that return None from read_cosign_key are skipped."""
        resolver = MagicMock(spec=["read_secret", "list_secrets"])
        resolver.list_secrets.return_value = ["projects/proj1/cosign/missing"]
        with patch("general_ludd.routers.signing.read_cosign_key", return_value=None):
            client = TestClient(_make_app(resolver))
            resp = client.get(self._path("proj1"))
        assert resp.status_code == 200
        assert resp.json() == []

    def test_no_list_secrets_attribute(self) -> None:
        """Resolver without list_secrets returns empty list (no crash)."""
        resolver = MagicMock(spec=["read_secret"])
        client = TestClient(_make_app(resolver))
        resp = client.get(self._path("proj1"))
        assert resp.status_code == 200
        assert resp.json() == []

    def test_503_no_resolver(self) -> None:
        client = TestClient(_make_app(resolver=None))
        resp = client.get(self._path())
        assert resp.status_code == 503

    def test_503_resolver_missing_read_secret(self) -> None:
        resolver = MagicMock(spec=["write_secret"])
        client = TestClient(_make_app(resolver))
        resp = client.get(self._path())
        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# /admin/signing/cosign/{project_id}/{key_name}  GET
# ---------------------------------------------------------------------------

class TestCosignRead:
    def _path(self, pid: str = "proj1", kn: str = "mykey") -> str:
        return f"/admin/signing/cosign/{pid}/{kn}"

    def test_happy_path(self) -> None:
        key = _cosign_key("mykey", "PUBVAL", "2024-03-01")
        resolver = MagicMock(spec=["read_secret"])
        with patch("general_ludd.routers.signing.read_cosign_key", return_value=key):
            client = TestClient(_make_app(resolver))
            resp = client.get(self._path())
        assert resp.status_code == 200
        body = resp.json()
        assert body["key_name"] == "mykey"
        assert body["public_key"] == "PUBVAL"
        assert body["created_at"] == "2024-03-01"

    def test_404_key_not_found(self) -> None:
        resolver = MagicMock(spec=["read_secret"])
        with patch("general_ludd.routers.signing.read_cosign_key", return_value=None):
            client = TestClient(_make_app(resolver))
            resp = client.get(self._path())
        assert resp.status_code == 404
        assert "key not found" in resp.json()["error"]

    def test_503_no_resolver(self) -> None:
        client = TestClient(_make_app(resolver=None))
        resp = client.get(self._path())
        assert resp.status_code == 503

    def test_503_resolver_missing_read_secret(self) -> None:
        resolver = MagicMock(spec=["write_secret"])
        client = TestClient(_make_app(resolver))
        resp = client.get(self._path())
        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# /admin/signing/cosign/{project_id}/{key_name}  DELETE
# ---------------------------------------------------------------------------

class TestCosignDelete:
    def _path(self, pid: str = "proj1", kn: str = "mykey") -> str:
        return f"/admin/signing/cosign/{pid}/{kn}"

    def test_happy_path(self) -> None:
        resolver = MagicMock(spec=["delete_secret"])
        with patch("general_ludd.routers.signing.delete_cosign_key") as mock_del:
            client = TestClient(_make_app(resolver))
            resp = client.delete(self._path("proj1", "mykey"))
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "deleted"
        assert body["project_id"] == "proj1"
        assert body["key_name"] == "mykey"
        mock_del.assert_called_once_with(resolver, "proj1", "mykey")

    def test_503_no_resolver(self) -> None:
        client = TestClient(_make_app(resolver=None))
        resp = client.delete(self._path())
        assert resp.status_code == 503
        assert "secrets resolver" in resp.json()["error"]

    def test_503_resolver_missing_delete_secret(self) -> None:
        resolver = MagicMock(spec=["read_secret"])
        client = TestClient(_make_app(resolver))
        resp = client.delete(self._path())
        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# /admin/signing/gitsign/config  POST
# ---------------------------------------------------------------------------

class TestGitsignWrite:
    _path = "/admin/signing/gitsign/config"

    def test_happy_path_defaults(self) -> None:
        resolver = MagicMock(spec=["write_secret"])
        with patch("general_ludd.routers.signing.write_gitsign_config") as mock_write:
            client = TestClient(_make_app(resolver))
            resp = client.post(self._path, json={})
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"
        mock_write.assert_called_once_with(
            mgr=resolver,
            project_id="default",
            fulcio_url="https://fulcio.sigstore.dev",
            rekor_url="https://rekor.sigstore.dev",
            oidc_issuer="https://oauth2.sigstore.dev/auth",
            key_ref="",
            enabled=True,
        )

    def test_happy_path_explicit_fields(self) -> None:
        resolver = MagicMock(spec=["write_secret"])
        with patch("general_ludd.routers.signing.write_gitsign_config") as mock_write:
            client = TestClient(_make_app(resolver))
            resp = client.post(
                self._path,
                json={
                    "project_id": "myproj",
                    "fulcio_url": "https://custom-fulcio.example.com",
                    "rekor_url": "https://custom-rekor.example.com",
                    "oidc_issuer": "https://custom-oidc.example.com",
                    "key_ref": "sha256:abc",
                    "enabled": False,
                },
            )
        assert resp.status_code == 200
        call_kwargs = mock_write.call_args.kwargs
        assert call_kwargs["project_id"] == "myproj"
        assert call_kwargs["fulcio_url"] == "https://custom-fulcio.example.com"
        assert call_kwargs["enabled"] is False

    def test_503_no_resolver(self) -> None:
        client = TestClient(_make_app(resolver=None))
        resp = client.post(self._path, json={})
        assert resp.status_code == 503

    def test_503_resolver_missing_write_secret(self) -> None:
        resolver = MagicMock(spec=["read_secret"])
        client = TestClient(_make_app(resolver))
        resp = client.post(self._path, json={})
        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# /admin/signing/gitsign/{project_id}  GET
# ---------------------------------------------------------------------------

class TestGitsignRead:
    def _path(self, pid: str = "proj1") -> str:
        return f"/admin/signing/gitsign/{pid}"

    def test_happy_path(self) -> None:
        cfg = GitsignConfig(
            fulcio_url="https://fulcio.sigstore.dev",
            rekor_url="https://rekor.sigstore.dev",
            oidc_issuer="https://oauth2.sigstore.dev/auth",
            key_ref="",
            enabled=True,
        )
        resolver = MagicMock(spec=["read_secret"])
        with patch("general_ludd.routers.signing.read_gitsign_config", return_value=cfg):
            client = TestClient(_make_app(resolver))
            resp = client.get(self._path())
        assert resp.status_code == 200
        body = resp.json()
        assert body["fulcio_url"] == "https://fulcio.sigstore.dev"
        assert body["enabled"] is True

    def test_404_config_not_found(self) -> None:
        resolver = MagicMock(spec=["read_secret"])
        with patch("general_ludd.routers.signing.read_gitsign_config", return_value=None):
            client = TestClient(_make_app(resolver))
            resp = client.get(self._path())
        assert resp.status_code == 404
        assert "gitsign config not found" in resp.json()["error"]

    def test_503_no_resolver(self) -> None:
        client = TestClient(_make_app(resolver=None))
        resp = client.get(self._path())
        assert resp.status_code == 503

    def test_503_resolver_missing_read_secret(self) -> None:
        resolver = MagicMock(spec=["write_secret"])
        client = TestClient(_make_app(resolver))
        resp = client.get(self._path())
        assert resp.status_code == 503
