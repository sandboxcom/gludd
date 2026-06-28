"""Regression tests: output_dir path confinement in /admin/signing/cosign/generate.

The handler must reject any output_dir that resolves outside the FileStore root
(~/.local/share/general-ludd/filestore).  A caller-supplied absolute path like
/etc or a traversal like ../../ must return HTTP 400, not write files to
arbitrary locations.
"""

from __future__ import annotations

import os
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from general_ludd.routers.signing import _COSIGN_OUTPUT_ALLOWED_ROOT, _validate_output_dir, register

# ---------------------------------------------------------------------------
# Unit tests for _validate_output_dir (pure logic, no HTTP)
# ---------------------------------------------------------------------------


class TestValidateOutputDir:
    """_validate_output_dir must confine paths to _COSIGN_OUTPUT_ALLOWED_ROOT."""

    _ALLOWED = _COSIGN_OUTPUT_ALLOWED_ROOT

    def test_none_is_accepted(self) -> None:
        assert _validate_output_dir(None) is None

    def test_valid_child_is_accepted(self) -> None:
        child = os.path.join(self._ALLOWED, "cosign", "project-1")
        result = _validate_output_dir(child)
        assert result == os.path.realpath(child)

    def test_allowed_root_itself_is_accepted(self) -> None:
        result = _validate_output_dir(self._ALLOWED)
        assert result == os.path.realpath(self._ALLOWED)

    def test_etc_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="outside the allowed root"):
            _validate_output_dir("/etc")

    def test_etc_passwd_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="outside the allowed root"):
            _validate_output_dir("/etc/passwd")

    def test_root_slash_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="outside the allowed root"):
            _validate_output_dir("/")

    def test_traversal_relative_path_is_rejected(self) -> None:
        # A relative traversal that resolves outside the allowed root.
        with pytest.raises(ValueError, match="outside the allowed root"):
            _validate_output_dir("../../etc")

    def test_allowed_root_prefix_not_child_is_rejected(self, tmp_path: Any) -> None:
        """A directory whose name starts with the allowed root string but is
        NOT a child of it must still be rejected (e.g. /allowed-rootXYZ)."""
        # Construct a path that shares the prefix but isn't a descendant.
        sibling = self._ALLOWED + "-sibling"
        with pytest.raises(ValueError, match="outside the allowed root"):
            _validate_output_dir(sibling)

    def test_symlink_to_etc_is_rejected(self, tmp_path: Any) -> None:
        """A symlink that resolves outside the allowed root must be rejected."""
        link = tmp_path / "evil_link"
        link.symlink_to("/etc")
        with pytest.raises(ValueError, match="outside the allowed root"):
            _validate_output_dir(str(link))


# ---------------------------------------------------------------------------
# Integration tests: HTTP 400 returned by the route handler
# ---------------------------------------------------------------------------


def _make_app() -> tuple[FastAPI, MagicMock]:
    """Create a minimal FastAPI app with the signing router registered and a
    mock resolver attached to app.state."""
    app = FastAPI()
    mock_resolver = MagicMock()
    mock_resolver.write_secret = MagicMock()
    app.state._secrets_resolver = mock_resolver
    register(app, {})
    return app, mock_resolver


class TestCosignGenerateOutputDirConfinement:
    """/admin/signing/cosign/generate must reject output_dir outside the allowed root."""

    def _client(self) -> tuple[TestClient, MagicMock]:
        app, resolver = _make_app()
        return TestClient(app, raise_server_exceptions=True), resolver

    def test_etc_output_dir_returns_400(self) -> None:
        client, _ = self._client()
        resp = client.post(
            "/admin/signing/cosign/generate",
            json={"project_id": "test", "key_name": "k", "output_dir": "/etc"},
        )
        assert resp.status_code == 400
        assert "outside the allowed root" in resp.json()["error"]

    def test_root_output_dir_returns_400(self) -> None:
        client, _ = self._client()
        resp = client.post(
            "/admin/signing/cosign/generate",
            json={"project_id": "test", "key_name": "k", "output_dir": "/"},
        )
        assert resp.status_code == 400

    def test_relative_traversal_output_dir_returns_400(self) -> None:
        client, _ = self._client()
        resp = client.post(
            "/admin/signing/cosign/generate",
            json={"project_id": "test", "key_name": "k", "output_dir": "../../etc"},
        )
        assert resp.status_code == 400

    def test_null_output_dir_does_not_write_to_disk(self) -> None:
        """output_dir=None is valid — key is stored in secrets only, no disk I/O."""
        app, _ = _make_app()
        # Patch generate_and_store_cosign_key so no real crypto or disk write occurs.
        from general_ludd.secrets.cosign import CosignKey
        mock_key = CosignKey(
            key_name="k",
            private_key="PRIV",
            public_key="PUB",
            created_at="2026-01-01T00:00:00+00:00",
        )
        with patch(
            "general_ludd.routers.signing.generate_and_store_cosign_key",
            return_value=mock_key,
        ) as mock_gen:
            client = TestClient(app, raise_server_exceptions=True)
            resp = client.post(
                "/admin/signing/cosign/generate",
                json={"project_id": "test", "key_name": "k"},
            )
        assert resp.status_code == 200
        # output_dir must be passed as None (not the string "None")
        call_kwargs = mock_gen.call_args.kwargs
        assert call_kwargs["output_dir"] is None

    def test_valid_child_output_dir_is_accepted(self) -> None:
        """An output_dir nested inside the allowed root must pass validation."""
        app, _ = _make_app()
        from general_ludd.secrets.cosign import CosignKey
        mock_key = CosignKey(
            key_name="k",
            private_key="PRIV",
            public_key="PUB",
            created_at="2026-01-01T00:00:00+00:00",
        )
        safe_dir = os.path.join(_COSIGN_OUTPUT_ALLOWED_ROOT, "cosign", "test-proj")
        with patch(
            "general_ludd.routers.signing.generate_and_store_cosign_key",
            return_value=mock_key,
        ) as mock_gen:
            client = TestClient(app, raise_server_exceptions=True)
            resp = client.post(
                "/admin/signing/cosign/generate",
                json={"project_id": "test", "key_name": "k", "output_dir": safe_dir},
            )
        assert resp.status_code == 200
        call_kwargs = mock_gen.call_args.kwargs
        # The resolved path must be the realpath of the safe_dir.
        assert call_kwargs["output_dir"] == os.path.realpath(safe_dir)


# ---------------------------------------------------------------------------
# Regression: private key file must be written with mode 0o600 (owner-only)
# ---------------------------------------------------------------------------


class TestCosignKeyFilePermissions:
    """generate_cosign_key must write cosign.key with mode 0o600 (not world-readable)."""

    def test_private_key_file_mode_is_0o600(self, tmp_path: Any) -> None:
        from general_ludd.secrets.cosign import generate_cosign_key

        generate_cosign_key(key_name="test-key", output_dir=str(tmp_path))
        key_file = tmp_path / "cosign.key"
        assert key_file.exists(), "cosign.key was not written"
        mode = key_file.stat().st_mode & 0o777
        assert mode == 0o600, (
            f"cosign.key has mode {oct(mode)}, expected 0o600 (owner-read/write only)"
        )

    def test_output_dir_mode_is_0o700(self, tmp_path: Any) -> None:
        from general_ludd.secrets.cosign import generate_cosign_key

        key_dir = tmp_path / "cosign-keys"
        generate_cosign_key(key_name="test-key", output_dir=str(key_dir))
        mode = key_dir.stat().st_mode & 0o777
        assert mode == 0o700, (
            f"output_dir has mode {oct(mode)}, expected 0o700 (owner-only)"
        )
