"""Unit tests for binary path configuration and resolver."""

from __future__ import annotations

from unittest.mock import patch

from general_ludd.config.binary_paths import BinaryPathResolver, BinaryPaths


class TestBinaryPaths:
    def test_defaults(self):
        bp = BinaryPaths()
        assert bp.terraform == "terraform"
        assert bp.opentofu == "tofu"
        assert bp.vault == "vault"
        assert bp.openbao == "bao"
        assert bp.podman == "podman"
        assert bp.docker == "docker"
        assert bp.ansible_playbook == "ansible-playbook"
        assert bp.git == "git"
        assert bp.uv == "uv"

    def test_custom_paths(self):
        bp = BinaryPaths(
            terraform="/usr/local/bin/terraform",
            opentofu="/opt/tofu/bin/tofu",
            docker="/usr/bin/docker",
        )
        assert bp.terraform == "/usr/local/bin/terraform"
        assert bp.opentofu == "/opt/tofu/bin/tofu"
        assert bp.docker == "/usr/bin/docker"

    def test_serialization_roundtrip(self):
        bp = BinaryPaths(terraform="/custom/tf", vault="/custom/vault")
        data = bp.model_dump()
        restored = BinaryPaths.model_validate(data)
        assert restored == bp


class TestBinaryPathResolver:
    def test_resolve_returns_configured_path(self):
        bp = BinaryPaths(terraform="/custom/terraform")
        resolver = BinaryPathResolver(config=bp)
        assert resolver.resolve("terraform") == "/custom/terraform"

    def test_resolve_falls_back_to_which(self):
        bp = BinaryPaths()
        resolver = BinaryPathResolver(config=bp)
        with patch("general_ludd.config.binary_paths.shutil.which", return_value="/usr/bin/git"):
            assert resolver.resolve("git") == "/usr/bin/git"

    def test_resolve_returns_name_when_not_found(self):
        bp = BinaryPaths()
        resolver = BinaryPathResolver(config=bp)
        with patch("general_ludd.config.binary_paths.shutil.which", return_value=None):
            assert resolver.resolve("terraform") == "terraform"

    def test_resolve_unknown_binary_not_in_config(self):
        bp = BinaryPaths()
        resolver = BinaryPathResolver(config=bp)
        with patch("general_ludd.config.binary_paths.shutil.which", return_value=None):
            assert resolver.resolve("nonexistent-tool") == "nonexistent-tool"

    def test_is_available_true(self):
        bp = BinaryPaths()
        resolver = BinaryPathResolver(config=bp)
        with patch("general_ludd.config.binary_paths.shutil.which", return_value="/usr/bin/git"):
            assert resolver.is_available("git") is True

    def test_is_available_false(self):
        bp = BinaryPaths()
        resolver = BinaryPathResolver(config=bp)
        with patch("general_ludd.config.binary_paths.shutil.which", return_value=None):
            assert resolver.is_available("terraform") is False

    def test_is_available_with_custom_path(self):
        bp = BinaryPaths(terraform="/custom/terraform")
        resolver = BinaryPathResolver(config=bp)
        with patch("general_ludd.config.binary_paths.shutil.which", return_value=None):
            assert resolver.is_available("terraform") is True

    def test_get_infra_binary_prefers_tofu(self):
        bp = BinaryPaths()
        resolver = BinaryPathResolver(config=bp)
        with patch("general_ludd.config.binary_paths.shutil.which", return_value="/usr/bin/tofu"):
            assert resolver.get_infra_binary() == "tofu"

    def test_get_infra_binary_falls_back_to_terraform(self):
        bp = BinaryPaths()
        resolver = BinaryPathResolver(config=bp)
        which_fn = lambda x: None if x == "tofu" else "/usr/bin/terraform"  # noqa: E731
        with patch("general_ludd.config.binary_paths.shutil.which", side_effect=which_fn):
            assert resolver.get_infra_binary() == "terraform"

    def test_get_secrets_binary_prefers_bao(self):
        bp = BinaryPaths()
        resolver = BinaryPathResolver(config=bp)
        with patch("general_ludd.config.binary_paths.shutil.which", return_value="/usr/bin/bao"):
            assert resolver.get_secrets_binary() == "bao"

    def test_get_secrets_binary_falls_back_to_vault(self):
        bp = BinaryPaths()
        resolver = BinaryPathResolver(config=bp)
        which_fn = lambda x: None if x == "bao" else "/usr/bin/vault"  # noqa: E731
        with patch("general_ludd.config.binary_paths.shutil.which", side_effect=which_fn):
            assert resolver.get_secrets_binary() == "vault"

    def test_get_container_runtime_prefers_podman(self):
        bp = BinaryPaths()
        resolver = BinaryPathResolver(config=bp)
        with patch("general_ludd.config.binary_paths.shutil.which", return_value="/usr/bin/podman"):
            assert resolver.get_container_runtime() == "podman"

    def test_get_container_runtime_falls_back_to_docker(self):
        bp = BinaryPaths()
        resolver = BinaryPathResolver(config=bp)
        which_fn = lambda x: None if x == "podman" else "/usr/bin/docker"  # noqa: E731
        with patch("general_ludd.config.binary_paths.shutil.which", side_effect=which_fn):
            assert resolver.get_container_runtime() == "docker"

    def test_default_config_when_none(self):
        resolver = BinaryPathResolver()
        assert resolver._config.terraform == "terraform"

    def test_custom_paths_override_discovery(self):
        bp = BinaryPaths(opentofu="/opt/tofu/bin/tofu")
        resolver = BinaryPathResolver(config=bp)
        with patch("general_ludd.config.binary_paths.shutil.which", return_value=None):
            assert resolver.resolve("opentofu") == "/opt/tofu/bin/tofu"
