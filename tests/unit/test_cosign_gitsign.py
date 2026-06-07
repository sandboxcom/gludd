"""Test cosign key management and delete_secret via project-namespaced OpenBao."""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock


class TestDeleteSecret:
    def test_delete_secret_on_secrets_manager(self):
        from general_ludd.secrets.manager import SecretsManager

        mgr = SecretsManager(client=MagicMock(), aliases={})
        mgr.delete_secret("test/path")

        mgr._client.secrets.kv.v2.delete_metadata_and_all_versions.assert_called_once_with(
            path="test/path", mount_point=mgr._config.kv_mount,
        )

    def test_delete_secret_on_project_secrets_manager(self):
        from general_ludd.secrets.project_secrets import ProjectSecretsManager

        base = MagicMock()
        pmgr = ProjectSecretsManager(base_manager=base, project_id="proj-abc")
        pmgr.delete_secret("cosign/default")

        base.delete_secret.assert_called_once_with("projects/proj-abc/cosign/default")


class TestCosignKeyStorage:
    def test_write_cosign_key_stores_in_openbao(self):
        from general_ludd.secrets.cosign import write_cosign_key

        mgr = MagicMock()
        write_cosign_key(
            mgr,
            project_id="proj-xyz",
            key_name="default",
            private_key="-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----",
            public_key="-----BEGIN PUBLIC KEY-----\n...\n-----END PUBLIC KEY-----",
            password=None,
        )
        mgr.write_secret.assert_called_once()
        call_args = mgr.write_secret.call_args[0]
        assert call_args[0] == "projects/proj-xyz/cosign/default"
        stored = call_args[1]
        assert "private_key" in stored
        assert "public_key" in stored
        assert stored["key_name"] == "default"

    def test_read_cosign_key_retrieves_from_openbao(self):
        from general_ludd.secrets.cosign import read_cosign_key

        mgr = MagicMock()
        mgr.read_secret.return_value = {
            "private_key": "-----BEGIN EC PRIVATE KEY-----\n...",
            "public_key": "-----BEGIN PUBLIC KEY-----\n...",
            "password": None,
            "key_name": "default",
            "created_at": "2026-06-07T00:00:00Z",
        }
        key = read_cosign_key(mgr, project_id="proj-xyz", key_name="default")
        assert key is not None
        assert key.private_key.startswith("-----BEGIN EC")
        assert key.key_name == "default"
        mgr.read_secret.assert_called_once_with("projects/proj-xyz/cosign/default")

    def test_read_cosign_key_returns_none_when_missing(self):
        from general_ludd.secrets.cosign import read_cosign_key

        mgr = MagicMock()
        mgr.read_secret.return_value = None
        key = read_cosign_key(mgr, project_id="proj-xyz", key_name="missing")
        assert key is None

    def test_delete_cosign_key_removes_from_openbao(self):
        from general_ludd.secrets.cosign import delete_cosign_key

        mgr = MagicMock()
        delete_cosign_key(mgr, project_id="proj-xyz", key_name="default")
        mgr.delete_secret.assert_called_once_with("projects/proj-xyz/cosign/default")


class TestCosignKeyGeneration:
    def test_generate_cosign_key_returns_key_pair(self):
        from general_ludd.secrets.cosign import generate_cosign_key

        with tempfile.TemporaryDirectory() as tmpdir:
            key = generate_cosign_key(key_name="test-key", output_dir=tmpdir)
            assert key.key_name == "test-key"
            assert "BEGIN" in key.private_key
            assert "BEGIN" in key.public_key
            assert key.password is None

    def test_generate_cosign_key_writes_files(self):
        from general_ludd.secrets.cosign import generate_cosign_key

        with tempfile.TemporaryDirectory() as tmpdir:
            generate_cosign_key(key_name="test-key", output_dir=tmpdir)
            assert (Path(tmpdir) / "cosign.key").exists()
            assert (Path(tmpdir) / "cosign.pub").exists()

    def test_generate_and_store_cosign_key(self):
        from general_ludd.secrets.cosign import generate_and_store_cosign_key

        mgr = MagicMock()
        with tempfile.TemporaryDirectory() as tmpdir:
            key = generate_and_store_cosign_key(
                mgr, project_id="proj-test", key_name="test-key", output_dir=tmpdir,
            )
            assert key.key_name == "test-key"
            mgr.write_secret.assert_called_once()
            stored_path = mgr.write_secret.call_args[0][0]
            assert "cosign/test-key" in stored_path

    def test_generate_cosign_key_password_protected(self):
        from general_ludd.secrets.cosign import generate_cosign_key

        with tempfile.TemporaryDirectory() as tmpdir:
            key = generate_cosign_key(
                key_name="pw-key", output_dir=tmpdir, password="test-password",
            )
            assert key.password == "test-password"
            assert "BEGIN" in key.private_key


class TestGitsignConfig:
    def test_write_gitsign_config_stores_settings(self):
        from general_ludd.secrets.gitsign import write_gitsign_config

        mgr = MagicMock()
        write_gitsign_config(
            mgr,
            project_id="proj-abc",
            fulcio_url="https://fulcio.sigstore.dev",
            rekor_url="https://rekor.sigstore.dev",
            oidc_issuer="https://oauth2.sigstore.dev/auth",
            key_ref="cosign/default",
        )
        mgr.write_secret.assert_called_once()
        call_path = mgr.write_secret.call_args[0][0]
        assert "gitsign/config" in call_path
        stored = mgr.write_secret.call_args[0][1]
        assert stored["fulcio_url"] == "https://fulcio.sigstore.dev"
        assert stored["key_ref"] == "cosign/default"

    def test_read_gitsign_config_returns_settings(self):
        from general_ludd.secrets.gitsign import read_gitsign_config

        mgr = MagicMock()
        mgr.read_secret.return_value = {
            "fulcio_url": "https://fulcio.sigstore.dev",
            "rekor_url": "https://rekor.sigstore.dev",
            "key_ref": "cosign/default",
        }
        config = read_gitsign_config(mgr, project_id="proj-abc")
        assert config is not None
        assert config.fulcio_url == "https://fulcio.sigstore.dev"
        assert config.key_ref == "cosign/default"

    def test_read_gitsign_config_returns_none_when_missing(self):
        from general_ludd.secrets.gitsign import read_gitsign_config

        mgr = MagicMock()
        mgr.read_secret.return_value = None
        config = read_gitsign_config(mgr, project_id="proj-abc")
        assert config is None
