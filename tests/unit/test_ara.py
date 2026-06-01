"""Unit tests for ARA config and playbook stubs."""

from __future__ import annotations

from pathlib import Path

from general_ludd.ansible.ara import ARAConfig

_PLAYBOOKS_ROOT = Path(__file__).resolve().parent.parent.parent / "playbooks"


class TestARAConfig:
    def test_ara_config_defaults(self):
        config = ARAConfig()
        assert config.enabled is False
        assert config.backend == "sqlite"
        assert config.connection_string != ""
        assert config.callback_plugin_path != ""

    def test_ara_config_postgresql_backend(self):
        config = ARAConfig(
            enabled=True,
            backend="postgresql",
            connection_string="postgresql://ara:pass@localhost:5432/ara",
        )
        assert config.enabled is True
        assert config.backend == "postgresql"
        assert "postgresql://" in config.connection_string

    def test_ara_config_rejects_invalid_backend(self):
        import pytest

        with pytest.raises(ValueError):
            ARAConfig(backend="mysql")


class TestPlaybookStubs:
    def test_playbooks_exist(self):
        expected = [
            _PLAYBOOKS_ROOT / "action_policy_validate.yml",
            _PLAYBOOKS_ROOT / "ara_setup.yml",
        ]
        for path in expected:
            assert path.is_file(), f"Playbook stub missing: {path}"
