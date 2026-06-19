"""Tests for Alembic migration support."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest
from alembic.config import Config as AlembicConfig

from general_ludd.db.migrations import get_alembic_config, stamp_head


class TestGetAlembicConfig:
    def test_returns_alembic_config(self):
        cfg = get_alembic_config()
        assert cfg is not None

    def test_script_location_is_set(self):
        cfg = get_alembic_config()
        assert cfg.get_main_option("script_location") is not None
        assert "alembic" in cfg.get_main_option("script_location")

    def test_default_database_url(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("DATABASE_URL", None)
            cfg = get_alembic_config()
            url = cfg.get_main_option("sqlalchemy.url")
            assert url is not None
            assert "sqlite" in url

    def test_custom_database_url_from_env(self):
        with patch.dict(os.environ, {"DATABASE_URL": "postgresql://user:pass@localhost/mydb"}):
            cfg = get_alembic_config()
            url = cfg.get_main_option("sqlalchemy.url")
            assert url == "postgresql://user:pass@localhost/mydb"

    def test_config_file_name_set_when_exists(self):
        cfg = get_alembic_config()
        script = cfg.get_main_option("script_location")
        assert script.endswith("alembic") or "/alembic" in script

    def test_url_param_overrides_env_and_default(self):
        cfg = get_alembic_config("sqlite+aiosqlite:///custom.db")
        url = cfg.get_main_option("sqlalchemy.url")
        assert url == "sqlite+aiosqlite:///custom.db"


class TestStampHead:
    def test_stamp_head_calls_alembic_command_stamp(self):
        cfg = AlembicConfig()
        with patch("general_ludd.db.migrations.command") as mock_cmd:
            stamp_head(cfg)
        mock_cmd.stamp.assert_called_once_with(cfg, "head")

    def test_stamp_head_raises_on_failure(self):
        cfg = AlembicConfig()
        with patch("general_ludd.db.migrations.command") as mock_cmd:
            mock_cmd.stamp.side_effect = RuntimeError("can't connect")
            with pytest.raises(RuntimeError, match="can't connect"):
                stamp_head(cfg)


def _load_migration_001():
    """Load alembic/versions/001_initial_schema.py via importlib (filename starts with digit)."""
    import importlib.util
    import pathlib

    src = pathlib.Path(__file__).parent.parent.parent / "alembic" / "versions" / "001_initial_schema.py"
    spec = importlib.util.spec_from_file_location("migration_001_initial_schema", src)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


class TestAlembicEnvDatabaseURLOverride:
    """D-37: alembic/env.py must read DATABASE_URL and call config.set_main_option.

    env.py is project-local and runs at module level. We test the relevant
    snippet directly: given a mock alembic config, the walrus-operator block
    that reads DATABASE_URL and calls set_main_option behaves correctly.

    Strategy: execute only the D-37 snippet (the 2-line conditional) under
    controlled os.environ and a mock config, matching the exact code in env.py.
    This avoids the complexity of reloading a module that depends on alembic's
    internal context machinery.
    """

    def _run_d37_logic(self, mock_config: MagicMock) -> None:
        """Execute the D-37 env.py snippet against mock_config."""
        # Mirrors the production code in alembic/env.py:
        #   if db_url := os.environ.get("DATABASE_URL"):
        #       config.set_main_option("sqlalchemy.url", db_url)
        if db_url := os.environ.get("DATABASE_URL"):
            mock_config.set_main_option("sqlalchemy.url", db_url)

    def test_database_url_set_calls_set_main_option(self):
        """When DATABASE_URL is set, set_main_option is called with it."""
        test_url = "postgresql://user:pass@localhost/proddb"
        mock_config = MagicMock()
        with patch.dict(os.environ, {"DATABASE_URL": test_url}):
            self._run_d37_logic(mock_config)
        mock_config.set_main_option.assert_called_once_with("sqlalchemy.url", test_url)

    def test_database_url_unset_does_not_call_set_main_option(self):
        """When DATABASE_URL is absent, set_main_option is never called."""
        mock_config = MagicMock()
        env_without_db_url = {k: v for k, v in os.environ.items() if k != "DATABASE_URL"}
        with patch.dict(os.environ, env_without_db_url, clear=True):
            self._run_d37_logic(mock_config)
        mock_config.set_main_option.assert_not_called()

    def test_env_py_contains_database_url_override(self):
        """Structural: alembic/env.py source contains the DATABASE_URL override block (D-37)."""
        import pathlib

        src = pathlib.Path(__file__).parent.parent.parent / "alembic" / "env.py"
        text = src.read_text()
        assert 'os.environ.get("DATABASE_URL")' in text, (
            "D-37 fix missing: env.py must read DATABASE_URL from os.environ"
        )
        assert 'config.set_main_option("sqlalchemy.url"' in text, (
            "D-37 fix missing: env.py must call config.set_main_option with the URL"
        )
        assert "import os" in text, "D-37 fix missing: env.py must import os"


class TestMigration001DowngradeGuard:
    """D-38: downgrade() in 001_initial_schema must refuse without ALEMBIC_DOWNGRADE_CONFIRMED=yes."""

    def test_downgrade_raises_without_env_var(self):
        """downgrade() raises RuntimeError when ALEMBIC_DOWNGRADE_CONFIRMED is absent."""
        env = {k: v for k, v in os.environ.items() if k != "ALEMBIC_DOWNGRADE_CONFIRMED"}
        with patch.dict(os.environ, env, clear=True):
            mod = _load_migration_001()
            with pytest.raises(RuntimeError, match="ALEMBIC_DOWNGRADE_CONFIRMED"):
                mod.downgrade()

    def test_downgrade_raises_with_wrong_value(self):
        """downgrade() raises RuntimeError when ALEMBIC_DOWNGRADE_CONFIRMED is not 'yes'."""
        with patch.dict(os.environ, {"ALEMBIC_DOWNGRADE_CONFIRMED": "true"}):
            mod = _load_migration_001()
            with pytest.raises(RuntimeError, match="ALEMBIC_DOWNGRADE_CONFIRMED"):
                mod.downgrade()

    def test_downgrade_proceeds_and_drops_all_tables_when_confirmed(self):
        """downgrade() drops all 9 tables when ALEMBIC_DOWNGRADE_CONFIRMED=yes."""
        with patch.dict(os.environ, {"ALEMBIC_DOWNGRADE_CONFIRMED": "yes"}):
            mod = _load_migration_001()
            with patch.object(mod, "op") as mock_op:
                mod.downgrade()
            drop_calls = [c.args[0] for c in mock_op.drop_table.call_args_list]
        assert len(drop_calls) == 9, f"Expected 9 drop_table calls, got {len(drop_calls)}: {drop_calls}"
        expected_tables = {
            "todos",
            "todo_events",
            "task_returns",
            "task_decisions",
            "queues",
            "audit_events",
            "variable_namespaces",
            "variable_values",
            "bucket_leases",
        }
        assert set(drop_calls) == expected_tables

    def test_require_downgrade_confirmed_raises_without_env(self):
        """_require_downgrade_confirmed() raises RuntimeError when env var is absent."""
        env = {k: v for k, v in os.environ.items() if k != "ALEMBIC_DOWNGRADE_CONFIRMED"}
        with patch.dict(os.environ, env, clear=True):
            mod = _load_migration_001()
            with pytest.raises(RuntimeError, match="destructive"):
                mod._require_downgrade_confirmed()

    def test_require_downgrade_confirmed_passes_when_set(self):
        """_require_downgrade_confirmed() returns None when ALEMBIC_DOWNGRADE_CONFIRMED=yes."""
        with patch.dict(os.environ, {"ALEMBIC_DOWNGRADE_CONFIRMED": "yes"}):
            mod = _load_migration_001()
            result = mod._require_downgrade_confirmed()
        assert result is None
