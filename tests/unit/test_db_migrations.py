"""Tests for Alembic migration support."""

from __future__ import annotations

import os
from typing import Any, cast
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
        # Alembic runs synchronously, so the application driver's async URL is
        # intentionally normalized while preserving the explicit database.
        assert url == "sqlite:///custom.db"


class TestStampHead:
    def test_stamp_head_uses_sync_driver_for_aiosqlite(self):
        cfg = AlembicConfig()
        cfg.set_main_option("sqlalchemy.url", "sqlite+aiosqlite:///custom.db")
        migration_urls: list[str | None] = []

        def _capture_url(captured_cfg: AlembicConfig, revision: str) -> None:
            assert revision == "head"
            migration_urls.append(captured_cfg.get_main_option("sqlalchemy.url"))

        with patch("general_ludd.db.migrations.command.stamp", side_effect=_capture_url):
            stamp_head(cfg)

        assert migration_urls == ["sqlite:///custom.db"]
        assert cfg.get_main_option("sqlalchemy.url") == "sqlite+aiosqlite:///custom.db"

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

    def test_stamp_head_restores_async_url_on_failure(self):
        cfg = AlembicConfig()
        async_url = "sqlite+aiosqlite:///custom.db"
        cfg.set_main_option("sqlalchemy.url", async_url)
        with patch("general_ludd.db.migrations.command") as mock_cmd:
            mock_cmd.stamp.side_effect = RuntimeError("can't connect")
            with pytest.raises(RuntimeError, match="can't connect"):
                stamp_head(cfg)
        assert cfg.get_main_option("sqlalchemy.url") == async_url


def _load_migration_by_filename(filename: str):
    """Load an alembic/versions/<filename> module via importlib.

    Version filenames start with a digit so they are not importable as normal
    modules.
    """
    import importlib.util
    import pathlib

    src = pathlib.Path(__file__).parent.parent.parent / "alembic" / "versions" / filename
    spec = importlib.util.spec_from_file_location(f"migration_{src.stem}", src)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    cast(Any, spec.loader).exec_module(mod)
    return mod


class TestMigrationChainSingleHead:
    """Regression: the migration graph must have exactly one head and a linear
    walkable chain.

    Two files previously both declared ``revision = "014"`` (ornith audit fields
    and ornith training pairs), creating a duplicate revision id / ambiguous
    multi-head graph that broke ``alembic upgrade head``. The training-pairs
    migration was linearized to ``014a`` (013 -> 014 -> 014a -> 015 -> 016).
    ``ScriptDirectory.from_config`` itself raises on a duplicate revision id, so
    merely loading the graph is part of the guard.
    """

    def _script_dir(self):
        from alembic.script import ScriptDirectory

        return ScriptDirectory.from_config(get_alembic_config())

    def test_single_head(self):
        script = self._script_dir()
        heads = script.get_heads()
        assert len(heads) == 1, f"expected single head, got {heads}"

    def test_ornith_014_migrations_are_distinct(self):
        script = self._script_dir()
        rev_ids = {r.revision for r in script.walk_revisions()}
        assert "014" in rev_ids
        assert "014a" in rev_ids
        assert "015" in rev_ids
        assert "016" in rev_ids
        assert "017" in rev_ids
        assert "018" in rev_ids

    def test_chain_is_linear(self):
        script = self._script_dir()
        # Every revision must have at most one child: no branch points.
        for rev in script.walk_revisions():
            assert len(rev.nextrev) <= 1, (
                f"revision {rev.revision} has multiple children {rev.nextrev}; "
                "the migration chain must stay linear"
            )

    def test_015_links_to_014a(self):
        mod = _load_migration_by_filename("015_add_model_performance_tables.py")
        assert mod.revision == "015"
        assert mod.down_revision == "014a"


class TestMigration016EscalationRemediation:
    """016 adds the ``permission_escalation_request`` and ``remediation_actions``
    tables that were declared in the ORM but had no migration.
    """

    def test_revision_links_to_015(self):
        mod = _load_migration_by_filename("016_add_escalation_and_remediation.py")
        assert mod.revision == "016"
        assert mod.down_revision == "015"

    def test_upgrade_creates_both_tables(self):
        mod = _load_migration_by_filename("016_add_escalation_and_remediation.py")
        with patch.object(mod, "op") as mock_op:
            mod.upgrade()
        created = [c.args[0] for c in mock_op.create_table.call_args_list]
        assert "permission_escalation_request" in created
        assert "remediation_actions" in created

    def test_downgrade_drops_both_tables(self):
        mod = _load_migration_by_filename("016_add_escalation_and_remediation.py")
        with patch.object(mod, "op") as mock_op:
            mod.downgrade()
        dropped = [c.args[0] for c in mock_op.drop_table.call_args_list]
        assert "permission_escalation_request" in dropped
        assert "remediation_actions" in dropped


class TestMigration020Arity:
    """020 downgrade() alter_column calls must use exactly 1 positional arg
    (column name only) inside a batch_alter_table context.

    Regression: the second alter_column erroneously passed ("todos",
    "definition_of_done") as positional args. Inside a batch context the table
    is already bound, so the extra "todos" arg shifts the column name into the
    wrong position and breaks the call signature at runtime.
    """

    _FILENAME = "020_make_todo_acceptance_criteria_and_dod_nullable.py"

    def test_revision_links_to_020(self):
        mod = _load_migration_by_filename(self._FILENAME)
        assert mod.revision == "021"
        assert mod.down_revision == "020"

    def test_downgrade_alter_column_calls_have_single_positional_arg(self):
        mod = _load_migration_by_filename(self._FILENAME)
        with patch.object(mod, "op") as mock_op:
            mod.downgrade()
        batch_ctx = mock_op.batch_alter_table.return_value.__enter__.return_value
        alter_calls = batch_ctx.alter_column.call_args_list
        assert len(alter_calls) == 2, (
            f"expected 2 alter_column calls in downgrade(), got {len(alter_calls)}"
        )
        for call in alter_calls:
            assert len(call.args) == 1, (
                "alter_column inside batch_alter_table must be called with exactly "
                f"1 positional arg (column name only); got args={call.args}"
            )
        columns = [call.args[0] for call in alter_calls]
        assert columns == ["acceptance_criteria", "definition_of_done"], (
            f"expected ['acceptance_criteria', 'definition_of_done'], got {columns}"
        )

    def test_upgrade_alter_column_calls_have_single_positional_arg(self):
        """upgrade() already follows the correct arity; pin it as the reference shape."""
        mod = _load_migration_by_filename(self._FILENAME)
        with patch.object(mod, "op") as mock_op:
            mod.upgrade()
        batch_ctx = mock_op.batch_alter_table.return_value.__enter__.return_value
        alter_calls = batch_ctx.alter_column.call_args_list
        assert len(alter_calls) == 2
        for call in alter_calls:
            assert len(call.args) == 1, (
                f"upgrade() alter_column arity drifted; got args={call.args}"
            )


def _load_migration_001():
    """Load alembic/versions/001_initial_schema.py via importlib (filename starts with digit)."""
    import importlib.util
    import pathlib

    src = pathlib.Path(__file__).parent.parent.parent / "alembic" / "versions" / "001_initial_schema.py"
    spec = importlib.util.spec_from_file_location("migration_001_initial_schema", src)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    cast(Any, spec.loader).exec_module(mod)
    return mod


def _load_migration_011():
    """Load alembic/versions/011_add_bucket_leases_expires_at_index.py via importlib."""
    import importlib.util
    import pathlib

    src = (
        pathlib.Path(__file__).parent.parent.parent
        / "alembic"
        / "versions"
        / "011_add_bucket_leases_expires_at_index.py"
    )
    spec = importlib.util.spec_from_file_location("migration_011_add_bucket_leases_expires_at_index", src)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    cast(Any, spec.loader).exec_module(mod)
    return mod


class TestMigration011ExpiresAtIndex:
    """F4: ``reclaim_expired_leases`` filters ``expires_at < now`` every tick.
    Without an index this is a full-table scan on ``bucket_leases`` per tick.
    Migration 011 adds ``ix_bucket_leases_expires_at`` to make the reclaim query
    indexed.
    """

    _EXPECTED_INDEX = "ix_bucket_leases_expires_at"

    def test_revision_links_to_010(self):
        mod = _load_migration_011()
        assert mod.revision == "011"
        assert mod.down_revision == "010"

    def test_upgrade_creates_index(self):
        mod = _load_migration_011()
        with patch.object(mod, "op") as mock_op:
            mod.upgrade()
        create_calls = [c.args[0] for c in mock_op.create_index.call_args_list]
        assert self._EXPECTED_INDEX in create_calls, (
            f"upgrade() did not create {self._EXPECTED_INDEX}; "
            f"create_index calls={create_calls}"
        )
        # Sanity: the index is on (expires_at) of bucket_leases.
        idx_call = next(
            c for c in mock_op.create_index.call_args_list if c.args[0] == self._EXPECTED_INDEX
        )
        assert idx_call.kwargs.get("table_name") == "bucket_leases" or (
            len(idx_call.args) > 2 and idx_call.args[1] == "bucket_leases"
        )
        assert "expires_at" in idx_call.args[2], (
            f"index columns={idx_call.args[2]} does not include expires_at"
        )

    def test_downgrade_drops_index(self):
        mod = _load_migration_011()
        with patch.object(mod, "op") as mock_op:
            mod.downgrade()
        drop_calls = [c.args[0] for c in mock_op.drop_index.call_args_list]
        assert self._EXPECTED_INDEX in drop_calls, (
            f"downgrade() did not drop {self._EXPECTED_INDEX}; "
            f"drop_index calls={drop_calls}"
        )


class TestBucketLeaseModelExpiresAtIndexed:
    """F4 (model layer): BucketLeaseModel.expires_at must declare index=True so
    ``Base.metadata.create_all`` (used by tests + fresh SQLite DBs) also gets
    the index, not only Alembic-managed Postgres deployments.
    """

    def test_expires_at_column_has_index(self):
        from general_ludd.db.models import BucketLeaseModel

        col = BucketLeaseModel.__table__.c.expires_at
        assert col.index is True, (
            "BucketLeaseModel.expires_at must be indexed (index=True) so "
            "reclaim_expired_leases does not full-table-scan every tick."
        )


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
