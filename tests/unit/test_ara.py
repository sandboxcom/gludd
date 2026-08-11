"""Deep tests for ARA (Ansible Record Automation) configuration."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from general_ludd.ansible.ara import ARAConfig

_PLAYBOOKS_ROOT = Path(__file__).resolve().parent.parent.parent / "playbooks"


class TestARAConfigDefaults:
    """All four fields have correct defaults."""

    def test_default_enabled_false(self) -> None:
        assert ARAConfig().enabled is False

    def test_default_backend_sqlite(self) -> None:
        assert ARAConfig().backend == "sqlite"

    def test_default_connection_string_points_to_tmp(self) -> None:
        assert ARAConfig().connection_string == "sqlite:///tmp/ara-default.db"

    def test_default_callback_plugin_path(self) -> None:
        assert ARAConfig().callback_plugin_path == ("/usr/lib/python3/dist-packages/ara/plugins/callback")

    def test_all_defaults_present(self) -> None:
        cfg = ARAConfig()
        dumped = cfg.model_dump()
        assert dumped["enabled"] is False
        assert dumped["backend"] == "sqlite"
        assert dumped["connection_string"] == "sqlite:///tmp/ara-default.db"
        assert "callback_plugin_path" in dumped

    def test_default_instance_is_valid(self) -> None:
        cfg = ARAConfig()
        assert cfg.backend == "sqlite"
        assert cfg.connection_string == "sqlite:///tmp/ara-default.db"


class TestARAConfigEnabled:
    """The enabled flag toggles ARA recording."""

    def test_enabled_true(self) -> None:
        cfg = ARAConfig(enabled=True)
        assert cfg.enabled is True
        assert cfg.backend == "sqlite"

    def test_enabled_true_keeps_other_defaults(self) -> None:
        cfg = ARAConfig(enabled=True)
        assert cfg.backend == "sqlite"
        assert cfg.connection_string == "sqlite:///tmp/ara-default.db"

    def test_enabled_false_explicit(self) -> None:
        cfg = ARAConfig(enabled=False)
        assert cfg.enabled is False

    def test_enabled_preserved_in_dump(self) -> None:
        cfg = ARAConfig(enabled=True)
        assert cfg.model_dump()["enabled"] is True


class TestARAConfigSQLiteBackend:
    """The default sqlite backend preserves connection_string unchanged."""

    def test_sqlite_default_connection_string(self) -> None:
        cfg = ARAConfig(backend="sqlite")
        assert cfg.connection_string == "sqlite:///tmp/ara-default.db"

    def test_sqlite_custom_connection_string(self) -> None:
        custom = "sqlite:///var/lib/ara/ansible.sqlite"
        cfg = ARAConfig(backend="sqlite", connection_string=custom)
        assert cfg.backend == "sqlite"
        assert cfg.connection_string == custom

    def test_sqlite_does_not_prefix_non_postgres_string(self) -> None:
        cfg = ARAConfig(backend="sqlite", connection_string="sqlite:///custom.db")
        assert cfg.connection_string == "sqlite:///custom.db"
        assert not cfg.connection_string.startswith("postgresql://")

    def test_sqlite_preserves_nonstandard_path(self) -> None:
        cfg = ARAConfig(
            backend="sqlite",
            connection_string="sqlite:///home/user/.ara/ansible.sqlite",
        )
        assert cfg.connection_string == "sqlite:///home/user/.ara/ansible.sqlite"

    def test_sqlite_allows_memory_database(self) -> None:
        cfg = ARAConfig(backend="sqlite", connection_string="sqlite://")
        assert cfg.connection_string == "sqlite://"


class TestARAConfigPostgreSQLBackend:
    """PostgreSQL backend auto-prefixes connection_string when missing."""

    def test_postgresql_auto_prefix(self) -> None:
        cfg = ARAConfig(
            backend="postgresql",
            connection_string="ara:password@localhost:5432/aradb",
        )
        assert cfg.backend == "postgresql"
        assert cfg.connection_string == "postgresql://ara:password@localhost:5432/aradb"

    def test_postgresql_already_prefixed(self) -> None:
        full = "postgresql://ara:pass@db.example.com:5432/aradb"
        cfg = ARAConfig(backend="postgresql", connection_string=full)
        assert cfg.connection_string == full

    def test_postgresql_minimal(self) -> None:
        cfg = ARAConfig(backend="postgresql", connection_string="localhost/aradb")
        assert cfg.connection_string == "postgresql://localhost/aradb"

    def test_postgresql_empty_connection_string(self) -> None:
        cfg = ARAConfig(backend="postgresql", connection_string="")
        assert cfg.connection_string == "postgresql://"

    def test_postgresql_full_credentials(self) -> None:
        cfg = ARAConfig(
            enabled=True,
            backend="postgresql",
            connection_string="ara_user:supersecret@pg-primary.internal:5432/ara_production",
        )
        assert cfg.enabled is True
        assert cfg.connection_string == ("postgresql://ara_user:supersecret@pg-primary.internal:5432/ara_production")

    def test_postgresql_prefix_is_idempotent(self) -> None:
        cfg = ARAConfig(
            backend="postgresql",
            connection_string="postgresql://x",
        )
        assert cfg.connection_string == "postgresql://x"


class TestARAConfigInvalidBackend:
    """Literal type rejects values not in the union."""

    def test_invalid_backend_raises(self) -> None:
        with pytest.raises(ValidationError) as exc:
            ARAConfig(backend="mysql")
        errors = exc.value.errors()
        assert any(e["loc"] == ("backend",) for e in errors)

    def test_invalid_backend_none_raises(self) -> None:
        with pytest.raises(ValidationError):
            ARAConfig(backend=None)

    def test_invalid_backend_empty_string_raises(self) -> None:
        with pytest.raises(ValidationError):
            ARAConfig(backend="")

    def test_invalid_backend_mixed_case_raises(self) -> None:
        with pytest.raises(ValidationError):
            ARAConfig(backend="PostgreSQL")


class TestARAConfigCallbackPluginPath:
    """The callback_plugin_path field is a plain string with a default."""

    def test_callback_plugin_path_default(self) -> None:
        assert ARAConfig().callback_plugin_path == ("/usr/lib/python3/dist-packages/ara/plugins/callback")

    def test_callback_plugin_path_custom(self) -> None:
        custom = "/opt/ara/lib/python3/site-packages/ara/plugins/callback"
        cfg = ARAConfig(callback_plugin_path=custom)
        assert cfg.callback_plugin_path == custom

    def test_callback_plugin_path_empty(self) -> None:
        cfg = ARAConfig(callback_plugin_path="")
        assert cfg.callback_plugin_path == ""

    def test_callback_plugin_path_relative(self) -> None:
        cfg = ARAConfig(callback_plugin_path="./plugins/callback")
        assert cfg.callback_plugin_path == "./plugins/callback"


class TestARAConfigModelDump:
    """model_dump() round-trips all fields correctly."""

    def test_round_trip_defaults(self) -> None:
        cfg = ARAConfig()
        dumped = cfg.model_dump()
        reloaded = ARAConfig(**dumped)
        assert reloaded.enabled == cfg.enabled
        assert reloaded.backend == cfg.backend
        assert reloaded.connection_string == cfg.connection_string
        assert reloaded.callback_plugin_path == cfg.callback_plugin_path

    def test_round_trip_postgresql(self) -> None:
        cfg = ARAConfig(
            enabled=True,
            backend="postgresql",
            connection_string="postgresql://ara:pass@localhost/aradb",
            callback_plugin_path="/custom/path",
        )
        dumped = cfg.model_dump()
        reloaded = ARAConfig(**dumped)
        assert reloaded.model_dump() == dumped

    def test_round_trip_keys(self) -> None:
        dumped = ARAConfig().model_dump()
        assert set(dumped.keys()) == {
            "enabled",
            "backend",
            "connection_string",
            "callback_plugin_path",
        }

    def test_model_dump_json(self) -> None:
        cfg = ARAConfig(enabled=True, backend="sqlite")
        json_str = cfg.model_dump_json()
        assert "true" in json_str or '"enabled":true' in json_str
        assert '"backend":"sqlite"' in json_str


class TestARAConfigModelValidator:
    """The model_validator only fires for postgresql backend."""

    def test_sqlite_connection_string_untouched(self) -> None:
        cfg = ARAConfig(
            backend="sqlite",
            connection_string="not-postgresql://whatever",
        )
        assert cfg.connection_string == "not-postgresql://whatever"

    def test_validator_does_not_overwrite_already_correct(self) -> None:
        cfg = ARAConfig(
            backend="postgresql",
            connection_string="postgresql://correct",
        )
        assert cfg.connection_string == "postgresql://correct"

    def test_validator_runs_in_after_mode(self) -> None:
        cfg = ARAConfig(
            backend="postgresql",
            connection_string="needs-prefix",
        )
        assert cfg.connection_string == "postgresql://needs-prefix"


class TestARAConfigCombinedScenarios:
    """End-to-end realistic scenarios."""

    def test_full_sqlite_setup(self) -> None:
        cfg = ARAConfig(
            enabled=True,
            backend="sqlite",
            connection_string="sqlite:///var/lib/ara/ara.db",
            callback_plugin_path="/usr/share/ara/plugins/callback",
        )
        assert cfg.enabled is True
        assert cfg.backend == "sqlite"
        assert cfg.connection_string == "sqlite:///var/lib/ara/ara.db"
        assert cfg.callback_plugin_path == "/usr/share/ara/plugins/callback"

    def test_full_postgresql_setup_auto_prefix(self) -> None:
        cfg = ARAConfig(
            enabled=True,
            backend="postgresql",
            connection_string="ara_user:password@db.private:5432/ara",
            callback_plugin_path="/opt/ara/plugins/callback",
        )
        assert cfg.enabled is True
        assert cfg.backend == "postgresql"
        assert cfg.connection_string == ("postgresql://ara_user:password@db.private:5432/ara")
        assert cfg.callback_plugin_path == "/opt/ara/plugins/callback"

    def test_disabled_ara_does_not_need_valid_connection_string(self) -> None:
        cfg = ARAConfig(
            enabled=False,
            backend="sqlite",
            connection_string="",
        )
        assert cfg.enabled is False
        assert cfg.connection_string == ""

    def test_field_count_is_four(self) -> None:
        assert len(ARAConfig.model_fields) == 4


class TestPlaybookStubs:
    """Playbook stubs referenced by ARA exist on disk."""

    def test_ara_playbooks_exist(self) -> None:
        expected = [
            _PLAYBOOKS_ROOT / "action_policy_validate.yml",
            _PLAYBOOKS_ROOT / "ara_setup.yml",
        ]
        for path in expected:
            assert path.is_file(), f"Playbook stub missing: {path}"

    def test_ara_setup_playbook_is_not_empty(self) -> None:
        path = _PLAYBOOKS_ROOT / "ara_setup.yml"
        assert path.is_file()
        content = path.read_text()
        assert len(content) > 10
        assert "ara" in content.lower()
