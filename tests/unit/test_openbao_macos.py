"""Tests for OpenBao keystore support on macOS."""
from __future__ import annotations

from unittest.mock import patch


class TestContainerRuntimeMacOS:
    def test_macos_prefers_docker_over_podman(self):
        with patch("platform.system", return_value="Darwin"):
            from general_ludd.config.binary_paths import BinaryPathResolver, BinaryPaths
            config = BinaryPaths(podman="/usr/bin/podman", docker="/usr/bin/docker")
            resolver = BinaryPathResolver(config=config)
            with patch.object(resolver, "is_available", side_effect=lambda name: name in ("podman", "docker")):
                runtime = resolver.get_container_runtime()
                assert runtime == "/usr/bin/docker"

    def test_linux_prefers_podman_over_docker(self):
        with patch("platform.system", return_value="Linux"):
            from general_ludd.config.binary_paths import BinaryPathResolver, BinaryPaths
            config = BinaryPaths(podman="/usr/bin/podman", docker="/usr/bin/docker")
            resolver = BinaryPathResolver(config=config)
            with patch.object(resolver, "is_available", side_effect=lambda name: name in ("podman", "docker")):
                runtime = resolver.get_container_runtime()
                assert runtime == "/usr/bin/podman"

    def test_falls_back_to_docker_when_podman_unavailable(self):
        from general_ludd.config.binary_paths import BinaryPathResolver, BinaryPaths
        config = BinaryPaths(podman="podman", docker="docker")
        resolver = BinaryPathResolver(config=config)
        with patch.object(resolver, "is_available", side_effect=lambda name: name == "docker"):
            runtime = resolver.get_container_runtime()
            assert runtime == "docker"


class TestOpenBaoConfig:
    def test_default_mode_is_auto(self):
        from general_ludd.secrets.config import OpenBaoConfig
        cfg = OpenBaoConfig()
        assert cfg.mode == "auto"

    def test_local_image_default(self):
        from general_ludd.secrets.config import OpenBaoConfig
        cfg = OpenBaoConfig()
        assert "openbao" in cfg.local_image

    def test_config_accepts_custom_values(self):
        from general_ludd.secrets.config import OpenBaoConfig
        cfg = OpenBaoConfig(mode="external", external_url="http://localhost:8200")
        assert cfg.mode == "external"
        assert cfg.external_url == "http://localhost:8200"
