"""Unit tests for secrets wiring with OpenBao/Vault backend awareness and container launch."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agentic_harness.config.binary_paths import BinaryPathResolver, BinaryPaths
from agentic_harness.secrets.config import OpenBaoConfig
from agentic_harness.secrets.manager import SecretsManager


class TestOpenBaoConfigBackend:
    def test_default_backend_is_openbao(self):
        cfg = OpenBaoConfig()
        assert cfg.backend == "openbao"

    def test_backend_can_be_vault(self):
        cfg = OpenBaoConfig(backend="vault")
        assert cfg.backend == "vault"

    def test_backend_invalid_raises(self):
        import pydantic
        with pytest.raises(pydantic.ValidationError):
            OpenBaoConfig(backend="invalid")

    def test_binary_path_default_none(self):
        cfg = OpenBaoConfig()
        assert cfg.binary_path is None

    def test_binary_path_override(self):
        cfg = OpenBaoConfig(binary_path="/usr/local/bin/vault")
        assert cfg.binary_path == "/usr/local/bin/vault"

    def test_preserves_existing_fields(self):
        cfg = OpenBaoConfig(
            backend="vault",
            mode="external",
            external_url="https://vault.example.com:8200",
            external_token="s.token",
            kv_mount="custom-kv",
        )
        assert cfg.mode == "external"
        assert cfg.external_url == "https://vault.example.com:8200"
        assert cfg.kv_mount == "custom-kv"


class TestSecretsManagerContainerLaunch:
    @pytest.mark.asyncio
    async def test_start_local_container_uses_podman(self):
        cfg = OpenBaoConfig()
        mgr = SecretsManager(config=cfg)

        resolver = BinaryPathResolver(config=BinaryPaths())
        mock_proc = MagicMock()
        mock_proc.communicate = AsyncMock(return_value=(b"container-id-123", b""))
        mock_proc.returncode = 0

        with patch.object(resolver, "get_container_runtime", return_value="podman"), \
             patch("asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
            result = await mgr.start_local_container(binary_resolver=resolver)
            assert result is not None
            call_args = mock_exec.call_args[0]
            assert call_args[0] == "podman"

    @pytest.mark.asyncio
    async def test_start_local_container_uses_docker_fallback(self):
        cfg = OpenBaoConfig()
        mgr = SecretsManager(config=cfg)

        resolver = BinaryPathResolver(config=BinaryPaths())
        mock_proc = MagicMock()
        mock_proc.communicate = AsyncMock(return_value=(b"container-id-456", b""))
        mock_proc.returncode = 0

        with patch.object(resolver, "get_container_runtime", return_value="docker"), \
             patch("asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
            result = await mgr.start_local_container(binary_resolver=resolver)
            assert result is not None
            call_args = mock_exec.call_args[0]
            assert call_args[0] == "docker"

    @pytest.mark.asyncio
    async def test_start_local_container_uses_configured_image(self):
        cfg = OpenBaoConfig(local_image="custom/bao:latest")
        mgr = SecretsManager(config=cfg)

        resolver = BinaryPathResolver(config=BinaryPaths())
        mock_proc = MagicMock()
        mock_proc.communicate = AsyncMock(return_value=(b"abc", b""))
        mock_proc.returncode = 0

        with patch.object(resolver, "get_container_runtime", return_value="podman"), \
             patch("asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
            await mgr.start_local_container(binary_resolver=resolver)
            call_args = mock_exec.call_args[0]
            assert "custom/bao:latest" in call_args

    @pytest.mark.asyncio
    async def test_start_local_container_without_resolver_uses_default(self):
        cfg = OpenBaoConfig()
        mgr = SecretsManager(config=cfg)

        mock_proc = MagicMock()
        mock_proc.communicate = AsyncMock(return_value=(b"container-id", b""))
        mock_proc.returncode = 0

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await mgr.start_local_container()
            assert result is not None


class TestSecretsManagerHealthCheck:
    @pytest.mark.asyncio
    async def test_health_check_healthy(self):
        mgr = SecretsManager(config=OpenBaoConfig())
        mock_client = MagicMock()
        mock_client.is_authenticated.return_value = True
        mgr._client = mock_client

        healthy = await mgr.health_check()
        assert healthy is True

    @pytest.mark.asyncio
    async def test_health_check_unhealthy_no_client(self):
        mgr = SecretsManager(config=OpenBaoConfig())

        healthy = await mgr.health_check()
        assert healthy is False

    @pytest.mark.asyncio
    async def test_health_check_unhealthy_not_authenticated(self):
        mgr = SecretsManager(config=OpenBaoConfig())
        mock_client = MagicMock()
        mock_client.is_authenticated.return_value = False
        mgr._client = mock_client

        healthy = await mgr.health_check()
        assert healthy is False
