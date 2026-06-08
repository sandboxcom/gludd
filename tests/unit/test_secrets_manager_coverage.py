from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from general_ludd.secrets.config import OpenBaoConfig
from general_ludd.secrets.manager import (
    SecretAlias,
    SecretsManager,
)


class TestResolveWithClient:
    def test_resolve_with_client_returns_value(self):
        mock_client = MagicMock()
        mock_client.secrets.kv.v2.read_secret_version.return_value = {
            "data": {"data": {"value": "secret-val"}}
        }
        mgr = SecretsManager(client=mock_client)
        mgr.register_alias(SecretAlias(alias="db", path="db/pass"))
        assert mgr.resolve("db") == "secret-val"

    def test_resolve_with_client_exception_returns_none(self):
        mock_client = MagicMock()
        mock_client.secrets.kv.v2.read_secret_version.side_effect = Exception("boom")
        mgr = SecretsManager(client=mock_client)
        mgr.register_alias(SecretAlias(alias="db", path="db/pass"))
        assert mgr.resolve("db") is None


class TestConnect:
    def test_connect_with_local_bootstrap_result(self):
        mgr = SecretsManager()
        mgr.bootstrap_local()
        with patch("general_ludd.secrets.manager.hvac") as mock_hvac:
            mock_hvac.Client.return_value = MagicMock()
            mgr.connect()
            mock_hvac.Client.assert_called_once()
            assert mgr._client is mock_hvac.Client.return_value

    def test_connect_without_external_or_bootstrap_raises(self):
        mgr = SecretsManager()
        with pytest.raises(RuntimeError, match="No OpenBao backend available"):
            mgr.connect()


class TestSetupApproleNoClient:
    def test_setup_approle_without_client_raises(self):
        mgr = SecretsManager()
        with pytest.raises(RuntimeError, match="Not connected"):
            mgr.setup_approle("role")


class TestWriteSecret:
    def test_write_secret_without_client_raises(self):
        mgr = SecretsManager()
        with pytest.raises(RuntimeError, match="Not connected"):
            mgr.write_secret("path", {"k": "v"})


class TestReadSecret:
    def test_read_secret_with_exception_returns_none(self):
        mock_client = MagicMock()
        mock_client.secrets.kv.v2.read_secret_version.side_effect = Exception("fail")
        mgr = SecretsManager(client=mock_client)
        assert mgr.read_secret("some/path") is None

    def test_read_secret_no_data_data_returns_none(self):
        mock_client = MagicMock()
        mock_client.secrets.kv.v2.read_secret_version.return_value = {"data": {}}
        mgr = SecretsManager(client=mock_client)
        assert mgr.read_secret("some/path") is None

    def test_read_secret_empty_result_returns_none(self):
        mock_client = MagicMock()
        mock_client.secrets.kv.v2.read_secret_version.return_value = {}
        mgr = SecretsManager(client=mock_client)
        assert mgr.read_secret("some/path") is None


class TestDeleteSecret:
    def test_delete_secret(self):
        mock_client = MagicMock()
        mgr = SecretsManager(client=mock_client, config=OpenBaoConfig(kv_mount="secret"))
        mgr.delete_secret("my/path")
        mock_client.secrets.kv.v2.delete_metadata_and_all_versions.assert_called_once_with(
            path="my/path", mount_point="secret"
        )

    def test_delete_secret_without_client_raises(self):
        mgr = SecretsManager()
        with pytest.raises(RuntimeError, match="Not connected"):
            mgr.delete_secret("any/path")


class TestScanForImageUpdates:
    def test_scan_with_exception_returns_none(self):
        mock_client = MagicMock()
        mock_client.secrets.kv.v2.read_secret_version.side_effect = Exception("err")
        mgr = SecretsManager(client=mock_client)
        assert mgr.scan_for_image_updates() is None

    def test_scan_no_stored_data_returns_none(self):
        mock_client = MagicMock()
        mock_client.secrets.kv.v2.read_secret_version.return_value = None
        mgr = SecretsManager(client=mock_client)
        assert mgr.scan_for_image_updates() is None

    def test_scan_image_ref_without_slash_defaults_docker_io(self):
        cfg = OpenBaoConfig(local_image="myimage")
        mock_client = MagicMock()
        mock_client.secrets.kv.v2.read_secret_version.return_value = {
            "data": {"data": {"pinned_digest": "sha256:aaa"}}
        }
        mgr = SecretsManager(client=mock_client, config=cfg)
        with patch.object(mgr, "_fetch_remote_digest", return_value="sha256:bbb"):
            result = mgr.scan_for_image_updates()
        assert result is not None
        assert result.registry == "docker.io"


class TestStartLocalContainer:
    @pytest.mark.asyncio
    async def test_start_local_container_success(self):
        mgr = SecretsManager(config=OpenBaoConfig(backend="openbao"))
        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (b"abc123container\n", b"")
        mock_proc.returncode = 0
        with patch("general_ludd.secrets.manager.asyncio.create_subprocess_exec", return_value=mock_proc), \
             patch("general_ludd.config.binary_paths.BinaryPathResolver") as mock_resolver_cls:
            mock_resolver = MagicMock()
            mock_resolver.get_container_runtime.return_value = "podman"
            mock_resolver_cls.return_value = mock_resolver
            result = await mgr.start_local_container()
        assert result == "abc123container"

    @pytest.mark.asyncio
    async def test_start_local_container_failure(self):
        mgr = SecretsManager(config=OpenBaoConfig(backend="openbao"))
        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (b"", b"error")
        mock_proc.returncode = 1
        with patch("general_ludd.secrets.manager.asyncio.create_subprocess_exec", return_value=mock_proc), \
             patch("general_ludd.config.binary_paths.BinaryPathResolver") as mock_resolver_cls:
            mock_resolver = MagicMock()
            mock_resolver.get_container_runtime.return_value = "podman"
            mock_resolver_cls.return_value = mock_resolver
            result = await mgr.start_local_container()
        assert result is None


class TestHealthCheck:
    @pytest.mark.asyncio
    async def test_health_check_authenticated(self):
        mock_client = MagicMock()
        mock_client.is_authenticated.return_value = True
        mgr = SecretsManager(client=mock_client)
        assert await mgr.health_check() is True

    @pytest.mark.asyncio
    async def test_health_check_exception(self):
        mock_client = MagicMock()
        mock_client.is_authenticated.side_effect = Exception("fail")
        mgr = SecretsManager(client=mock_client)
        assert await mgr.health_check() is False

    @pytest.mark.asyncio
    async def test_health_check_no_client(self):
        mgr = SecretsManager()
        assert await mgr.health_check() is False


class TestGenerateSecretId:
    def test_generate_secret_id_without_client_raises(self):
        mgr = SecretsManager()
        with pytest.raises(RuntimeError, match="Not connected"):
            mgr._generate_secret_id("role")


class TestSecretAlias:
    def test_secret_alias_default_mount(self):
        alias = SecretAlias(alias="x", path="p")
        assert alias.mount == "secret"
