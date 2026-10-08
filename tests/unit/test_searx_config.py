from __future__ import annotations

import os
import stat
from pathlib import Path
from unittest import mock

import pytest
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
        assert data["server"]["bind_address"] == "127.0.0.1"
        assert data["server"]["port"] == 8888

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
        assert data["server"]["bind_address"] == "127.0.0.1"
        assert data["server"]["port"] == 9999

    def test_creates_parent_directories(self, tmp_path) -> None:
        nested = tmp_path / "a" / "b" / "c"
        assert not nested.exists()
        config = SearXConfig(base_dir=str(nested))
        config.generate()
        assert nested.is_dir()
        assert (nested / "settings.yml").is_file()

    def test_does_not_modify_defaults(self, tmp_path) -> None:
        before = dict(DEFAULT_SEARX_SETTINGS["server"])
        config = SearXConfig(base_dir=str(tmp_path))
        config.generate()
        after = dict(DEFAULT_SEARX_SETTINGS["server"])
        assert after == before
        assert DEFAULT_SEARX_SETTINGS["server"]["bind_address"] == "127.0.0.1"

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
        first_content = (tmp_path / "settings.yml").read_text(encoding="utf-8")
        second = config.generate()
        assert first == second
        assert (tmp_path / "settings.yml").read_text(encoding="utf-8") == first_content
        assert (tmp_path / "settings.yml").is_file()

    def test_output_and_directory_are_owner_only(self, tmp_path) -> None:
        config_dir = tmp_path / "secure"
        config = SearXConfig(base_dir=str(config_dir))
        config.generate()
        assert stat.S_IMODE(config_dir.stat().st_mode) == 0o700
        assert stat.S_IMODE((config_dir / "settings.yml").stat().st_mode) == 0o600

    def test_secret_is_generated_and_stable(self, tmp_path) -> None:
        config = SearXConfig(base_dir=str(tmp_path))
        config.generate()
        first = yaml.safe_load((tmp_path / "settings.yml").read_text(encoding="utf-8"))
        config.generate()
        second = yaml.safe_load((tmp_path / "settings.yml").read_text(encoding="utf-8"))
        assert len(first["server"]["secret_key"]) >= 32
        assert first["server"]["secret_key"] == second["server"]["secret_key"]


class TestSearXConfigBaseDir:
    def test_expands_tilde(self) -> None:
        config = SearXConfig(base_dir="~/test-gludd-searx")
        assert str(config.base_dir).endswith("test-gludd-searx")
        assert "~" not in str(config.base_dir)

    def test_default_directory_is_resource_namespaced(self, monkeypatch) -> None:
        monkeypatch.setenv("GLUDD_RESOURCE_NAMESPACE", "project-alpha")
        config = SearXConfig()
        assert config.base_dir.name == "project-alpha"

    def test_rejects_unsafe_default_namespace(self, monkeypatch) -> None:
        monkeypatch.setenv("GLUDD_RESOURCE_NAMESPACE", "../escape")

        with pytest.raises(ValueError, match="unsafe path"):
            SearXConfig()


class TestExistingSettingsSecurity:
    def test_rejects_symlinked_settings(self, tmp_path: Path) -> None:
        target = tmp_path / "target.yml"
        target.write_text("server: {}\n", encoding="utf-8")
        (tmp_path / "settings.yml").symlink_to(target)

        with pytest.raises(OSError, match="symlinked"):
            SearXConfig(base_dir=str(tmp_path)).generate()

    def test_rejects_non_regular_settings_path(self, tmp_path: Path) -> None:
        (tmp_path / "settings.yml").mkdir()

        with pytest.raises(OSError, match="not a regular file"):
            SearXConfig(base_dir=str(tmp_path)).generate()

    def test_rejects_group_writable_settings(self, tmp_path: Path) -> None:
        settings = tmp_path / "settings.yml"
        settings.write_text("server: {}\n", encoding="utf-8")
        settings.chmod(0o620)

        with pytest.raises(PermissionError, match="owner-only writable"):
            SearXConfig(base_dir=str(tmp_path)).generate()

    @pytest.mark.parametrize(
        "content",
        [
            "server: [unterminated\n",
            "- not\n- a\n- mapping\n",
            "server: not-a-mapping\n",
            "server:\n  secret_key: short\n",
        ],
    )
    def test_invalid_existing_content_is_replaced_securely(
        self,
        tmp_path: Path,
        content: str,
    ) -> None:
        settings = tmp_path / "settings.yml"
        settings.write_text(content, encoding="utf-8")
        settings.chmod(0o600)

        SearXConfig(base_dir=str(tmp_path)).generate()

        loaded = yaml.safe_load(settings.read_text(encoding="utf-8"))
        assert len(loaded["server"]["secret_key"]) >= 32
        assert stat.S_IMODE(settings.stat().st_mode) == 0o600
