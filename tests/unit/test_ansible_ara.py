"""Deep tests for ansible/ara.py — ARAConfig model and validator."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from general_ludd.ansible.ara import ARAConfig

pytestmark = pytest.mark.xdist_group("ansible_ara")


class TestARAConfigDefaults:
    def test_default_enabled_is_false(self):
        cfg = ARAConfig()
        assert cfg.enabled is False

    def test_default_backend_is_sqlite(self):
        cfg = ARAConfig()
        assert cfg.backend == "sqlite"

    def test_default_connection_string_is_sqlite_file(self):
        cfg = ARAConfig()
        assert cfg.connection_string == "sqlite:///tmp/ara-default.db"

    def test_default_callback_plugin_path(self):
        cfg = ARAConfig()
        assert cfg.callback_plugin_path == "/usr/lib/python3/dist-packages/ara/plugins/callback"

    def test_all_defaults_match_expected(self):
        cfg = ARAConfig()
        assert cfg.enabled is False
        assert cfg.backend == "sqlite"
        assert cfg.connection_string.startswith("sqlite:///")
        assert "ara" in cfg.callback_plugin_path


class TestARAConfigExplicit:
    def test_explicit_enabled_true(self):
        cfg = ARAConfig(enabled=True)
        assert cfg.enabled is True

    def test_explicit_postgresql_backend(self):
        cfg = ARAConfig(backend="postgresql")
        assert cfg.backend == "postgresql"

    def test_explicit_connection_string(self):
        cfg = ARAConfig(connection_string="sqlite:///custom/path/ara.db")
        assert cfg.connection_string == "sqlite:///custom/path/ara.db"

    def test_explicit_callback_plugin_path(self):
        cfg = ARAConfig(callback_plugin_path="/opt/ara/plugins/callback")
        assert cfg.callback_plugin_path == "/opt/ara/plugins/callback"

    def test_all_fields_explicit(self):
        cfg = ARAConfig(
            enabled=True,
            backend="sqlite",
            connection_string="sqlite:///data/ara.db",
            callback_plugin_path="/usr/local/lib/ara/plugins",
        )
        assert cfg.enabled is True
        assert cfg.backend == "sqlite"
        assert cfg.connection_string == "sqlite:///data/ara.db"
        assert cfg.callback_plugin_path == "/usr/local/lib/ara/plugins"


class TestARAConfigBackendValidation:
    def test_backend_must_be_sqlite_or_postgresql(self):
        with pytest.raises(ValidationError):
            ARAConfig(backend="mysql")

    def test_sqlite_is_valid_backend(self):
        cfg = ARAConfig(backend="sqlite")
        assert cfg.backend == "sqlite"

    def test_postgresql_is_valid_backend(self):
        cfg = ARAConfig(backend="postgresql")
        assert cfg.backend == "postgresql"


class TestARAConfigConnectionStringValidator:
    def test_sqlite_backend_preserves_connection_string(self):
        cfg = ARAConfig(
            backend="sqlite",
            connection_string="sqlite:///tmp/my-ara.db",
        )
        assert cfg.connection_string == "sqlite:///tmp/my-ara.db"

    def test_postgresql_backend_prepends_prefix_when_missing(self):
        cfg = ARAConfig(
            backend="postgresql",
            connection_string="user:pass@host:5432/db",
        )
        assert cfg.connection_string == "postgresql://user:pass@host:5432/db"

    def test_postgresql_backend_preserves_prefix_when_present(self):
        cfg = ARAConfig(
            backend="postgresql",
            connection_string="postgresql://user@host/db",
        )
        assert cfg.connection_string == "postgresql://user@host/db"

    def test_postgresql_prepend_does_not_double_prefix(self):
        cfg = ARAConfig(
            backend="postgresql",
            connection_string="postgresql://already/prefixed",
        )
        assert cfg.connection_string == "postgresql://already/prefixed"

    def test_sqlite_backend_does_not_mutate_non_prefix_string(self):
        cfg = ARAConfig(
            backend="sqlite",
            connection_string="my-custom-connection-string",
        )
        assert cfg.connection_string == "my-custom-connection-string"

    def test_connection_string_empty_default(self):
        cfg = ARAConfig(connection_string="")
        assert cfg.connection_string == ""

    def test_empty_connection_string_postgresql_gets_prefixed(self):
        cfg = ARAConfig(backend="postgresql", connection_string="")
        assert cfg.connection_string == "postgresql://"


class TestARAConfigSerialization:
    def test_model_dump(self):
        cfg = ARAConfig(
            enabled=True,
            backend="postgresql",
            connection_string="postgresql://user:pass@localhost/ara",
        )
        data = cfg.model_dump()
        assert data["enabled"] is True
        assert data["backend"] == "postgresql"
        assert data["connection_string"] == "postgresql://user:pass@localhost/ara"
        assert data["callback_plugin_path"] == "/usr/lib/python3/dist-packages/ara/plugins/callback"

    def test_model_dump_json(self):
        cfg = ARAConfig(enabled=False)
        json_str = cfg.model_dump_json()
        assert '"enabled":false' in json_str
        assert '"backend":"sqlite"' in json_str

    def test_roundtrip_serialization(self):
        original = ARAConfig(
            enabled=True,
            backend="postgresql",
            connection_string="postgresql://host/db",
            callback_plugin_path="/custom/ara/plugins",
        )
        data = original.model_dump()
        reconstructed = ARAConfig(**data)
        assert reconstructed == original

    def test_roundtrip_postgresql_no_prefix(self):
        original = ARAConfig(
            backend="postgresql",
            connection_string="host:5432/db",
        )
        data = original.model_dump()
        reconstructed = ARAConfig(**data)
        assert reconstructed.connection_string == "postgresql://host:5432/db"
        assert reconstructed.backend == "postgresql"


class TestARAConfigEdgeCases:
    def test_validator_on_sqlite_leaves_string_unchanged(self):
        cfg = ARAConfig(backend="sqlite", connection_string="short")
        assert cfg.connection_string == "short"
        assert cfg.backend == "sqlite"

    def test_mixed_case_backend_is_rejected(self):
        with pytest.raises(ValidationError):
            ARAConfig(backend="PostgreSQL")

    def test_numeric_enabled_values(self):
        cfg = ARAConfig(enabled=True)
        assert cfg.enabled is True
        cfg2 = ARAConfig(enabled=False)
        assert cfg2.enabled is False

    def test_connection_string_with_special_chars(self):
        cfg = ARAConfig(
            backend="postgresql",
            connection_string="user%40domain:pass%23word@host/db?sslmode=require",
        )
        assert cfg.connection_string == "postgresql://user%40domain:pass%23word@host/db?sslmode=require"
