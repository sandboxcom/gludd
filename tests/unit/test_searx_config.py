from __future__ import annotations

import os
from unittest import mock

import yaml

from general_ludd.searx.config import DEFAULT_SEARX_SETTINGS, SearXConfig


class TestSearXConfigGenerate:
    def test_creates_settings_file(self, tmp_path) -> None:
        config = SearXConfig(base_dir=str(tmp_path))
        output = config.generate()
        assert output == str(tmp_path / "settings.yml")
        assert (tmp_path / "settings.yml").is_file()

    def test_default_port(self, tmp_path) -> None:
        config = SearXConfig(base_dir=str(tmp_path))
        config.generate()
        with open(tmp_path / "settings.yml") as f:
            data = yaml.safe_load(f)
        assert data["server"]["bind_address"] == "127.0.0.1:8888"

    def test_default_safe_search(self, tmp_path) -> None:
        config = SearXConfig(base_dir=str(tmp_path))
        config.generate()
        with open(tmp_path / "settings.yml") as f:
            data = yaml.safe_load(f)
        assert data["search"]["safe_search"] == 0

    def test_default_listen_address(self, tmp_path) -> None:
        config = SearXConfig(base_dir=str(tmp_path))
        config.generate()
        with open(tmp_path / "settings.yml") as f:
            data = yaml.safe_load(f)
        assert data["server"]["bind_address"].startswith("127.0.0.1")

    def test_custom_port_via_env(self, tmp_path) -> None:
        with mock.patch.dict(os.environ, {"GLUDD_SEARX_PORT": "9999"}):
            config = SearXConfig(base_dir=str(tmp_path))
            config.generate()
        with open(tmp_path / "settings.yml") as f:
            data = yaml.safe_load(f)
        assert data["server"]["bind_address"] == "127.0.0.1:9999"

    def test_creates_parent_directories(self, tmp_path) -> None:
        nested = tmp_path / "a" / "b" / "c"
        assert not nested.exists()
        config = SearXConfig(base_dir=str(nested))
        config.generate()
        assert nested.is_dir()
        assert (nested / "settings.yml").is_file()

    def test_does_not_modify_defaults(self) -> None:
        before = dict(DEFAULT_SEARX_SETTINGS["server"])
        config = SearXConfig(base_dir="/nonexistent/path")
        with mock.patch.object(config, "base_dir") as mock_dir:
            mock_path = mock.MagicMock()
            mock_dir.mkdir = mock.MagicMock()
            mock_dir.__truediv__ = mock.MagicMock(return_value=mock_path)
            with mock.patch("builtins.open", mock.mock_open()):
                config.generate()
        after = dict(DEFAULT_SEARX_SETTINGS["server"])
        assert after == before
        assert DEFAULT_SEARX_SETTINGS["server"]["bind_address"] == "127.0.0.1:8888"

    def test_default_lang_and_formats(self, tmp_path) -> None:
        config = SearXConfig(base_dir=str(tmp_path))
        config.generate()
        with open(tmp_path / "settings.yml") as f:
            data = yaml.safe_load(f)
        assert data["search"]["default_lang"] == "en"
        assert data["search"]["formats"] == ["html", "json"]

    def test_public_instance_false(self, tmp_path) -> None:
        config = SearXConfig(base_dir=str(tmp_path))
        config.generate()
        with open(tmp_path / "settings.yml") as f:
            data = yaml.safe_load(f)
        assert data["server"]["public_instance"] is False

    def test_output_file_idempotent(self, tmp_path) -> None:
        config = SearXConfig(base_dir=str(tmp_path))
        first = config.generate()
        second = config.generate()
        assert first == second
        assert (tmp_path / "settings.yml").is_file()


class TestSearXConfigBaseDir:
    def test_expands_tilde(self) -> None:
        config = SearXConfig(base_dir="~/test-gludd-searx")
        assert str(config.base_dir).endswith("test-gludd-searx")
        assert "~" not in str(config.base_dir)
