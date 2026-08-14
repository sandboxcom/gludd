"""Tests for D-19: Alembic dry-run and migration plan."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from general_ludd.db.migrations import (
    MigrationPlan,
    check_pending,
    get_alembic_config,
    plan_migration,
)


class TestGetAlembicConfigExports:
    def test_plan_migration_importable(self):
        assert callable(plan_migration)

    def test_check_pending_importable(self):
        assert callable(check_pending)


class TestPlanMigration:
    def test_returns_migration_plan(self):
        cfg = get_alembic_config()
        result = plan_migration(cfg)
        assert isinstance(result, MigrationPlan)
        assert isinstance(result.sql, str)
        assert isinstance(result.pending_count, int)
        assert result.head_rev

    def test_head_rev_is_nonempty_string(self):
        cfg = get_alembic_config()
        result = plan_migration(cfg)
        assert result.head_rev
        assert isinstance(result.head_rev, str)
        assert len(result.head_rev) >= 1

    def test_pending_count_nonnegative(self):
        cfg = get_alembic_config()
        result = plan_migration(cfg)
        assert result.pending_count >= 0

    def test_plan_is_repeatable(self):
        cfg = get_alembic_config()
        result1 = plan_migration(cfg)
        result2 = plan_migration(cfg)
        assert result1.head_rev == result2.head_rev
        assert result1.sql == result2.sql

    def test_pending_count_zero_when_at_head(self):
        cfg = get_alembic_config()
        with patch("general_ludd.db.migrations.ScriptDirectory") as mock_sd:
            script = MagicMock()
            script.get_current_head.return_value = "abc123"
            mock_sd.from_config.return_value = script

            mc_ctx = MagicMock()
            mc_ctx.get_current_revision.return_value = "abc123"

            with (
                patch("general_ludd.db.migrations.MigrationContext") as mock_mc,
                patch("general_ludd.db.migrations.create_engine") as mock_engine,
                patch("general_ludd.db.migrations.EnvironmentContext"),
            ):
                mock_mc.configure.return_value = mc_ctx
                result = plan_migration(cfg)
                assert result.pending_count == 0
                mock_engine.return_value.dispose.assert_called_once_with()

    def test_pending_count_nonzero_when_behind(self):
        cfg = get_alembic_config()
        with patch("general_ludd.db.migrations.ScriptDirectory") as mock_sd:
            script = MagicMock()
            script.get_current_head.return_value = "abc123"
            script.iterate_revisions.return_value = ["a", "b", "c"]
            mock_sd.from_config.return_value = script

            mc_ctx = MagicMock()
            mc_ctx.get_current_revision.return_value = "xyz"

            with (
                patch("general_ludd.db.migrations.MigrationContext") as mock_mc,
                patch("general_ludd.db.migrations.create_engine"),
                patch("general_ludd.db.migrations.EnvironmentContext"),
            ):
                mock_mc.configure.return_value = mc_ctx
                result = plan_migration(cfg)
                assert result.pending_count == 3


class TestCheckPending:
    def test_returns_zero_when_at_head(self):
        cfg = get_alembic_config()
        with patch("general_ludd.db.migrations.ScriptDirectory") as mock_sd:
            script = MagicMock()
            script.get_current_head.return_value = "abc123"
            mock_sd.from_config.return_value = script

            mc_ctx = MagicMock()
            mc_ctx.get_current_revision.return_value = "abc123"

            with patch("general_ludd.db.migrations.MigrationContext") as mock_mc:
                mock_mc.configure.return_value = mc_ctx
                with patch("general_ludd.db.migrations.create_engine") as mock_engine:
                    result = check_pending(cfg)
                    assert result == 0
                    mock_engine.return_value.dispose.assert_called_once_with()

    def test_returns_pending_count(self):
        cfg = get_alembic_config()
        with patch("general_ludd.db.migrations.ScriptDirectory") as mock_sd:
            script = MagicMock()
            script.get_current_head.return_value = "abc123"
            script.iterate_revisions.return_value = ["a", "b", "c", "d", "e"]
            mock_sd.from_config.return_value = script

            mc_ctx = MagicMock()
            mc_ctx.get_current_revision.return_value = "xyz"

            with patch("general_ludd.db.migrations.MigrationContext") as mock_mc:
                mock_mc.configure.return_value = mc_ctx
                with patch("general_ludd.db.migrations.create_engine"):
                    result = check_pending(cfg)
                    assert result == 5

    def test_returns_one_when_no_current_revision(self):
        cfg = get_alembic_config()
        with patch("general_ludd.db.migrations.ScriptDirectory") as mock_sd:
            script = MagicMock()
            script.get_current_head.return_value = "abc123"
            mock_sd.from_config.return_value = script

            mc_ctx = MagicMock()
            mc_ctx.get_current_revision.return_value = None

            with patch("general_ludd.db.migrations.MigrationContext") as mock_mc:
                mock_mc.configure.return_value = mc_ctx
                with patch("general_ludd.db.migrations.create_engine"):
                    result = check_pending(cfg)
                    assert result == 1

    def test_returns_minus_one_on_db_error(self):
        cfg = get_alembic_config()
        with patch("general_ludd.db.migrations.ScriptDirectory") as mock_sd:
            script = MagicMock()
            script.get_current_head.return_value = "abc123"
            mock_sd.from_config.return_value = script

            with patch("general_ludd.db.migrations.create_engine") as mock_engine:
                mock_engine.side_effect = RuntimeError("connection refused")
                result = check_pending(cfg)
                assert result == -1


class TestMigrationPlanNamedTuple:
    def test_fields_accessible_by_name(self):
        plan = MigrationPlan(sql="SELECT 1", pending_count=0, current_rev="abc", head_rev="abc")
        assert plan.sql == "SELECT 1"
        assert plan.pending_count == 0
        assert plan.current_rev == "abc"
        assert plan.head_rev == "abc"

    def test_fields_accessible_by_index(self):
        plan = MigrationPlan(sql="x", pending_count=2, current_rev="a", head_rev="b")
        assert plan[0] == "x"
        assert plan[1] == 2
        assert plan[2] == "a"
        assert plan[3] == "b"
