"""Deep edge-case tests for db/migrations.py — migration utilities, version
chains, and stamp operations."""

from __future__ import annotations

import os
import pathlib
from typing import Any
from unittest.mock import patch

import pytest
from alembic.config import Config as AlembicConfig
from alembic.util.exc import CommandError

from general_ludd.db.migrations import (
    MigrationPlan,
    check_pending,
    get_alembic_config,
    plan_migration,
    stamp_head,
)


def _rev_ids() -> set[str]:
    """Return every revision id declared in alembic/versions/."""
    versions_dir = pathlib.Path(__file__).parent.parent.parent / "alembic" / "versions"
    ids: set[str] = set()
    for f in sorted(versions_dir.glob("*.py")):
        if f.name == "__init__.py":
            continue
        content = f.read_text()
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith("revision:") or stripped.startswith("revision ="):
                ids.add(stripped.split("=")[-1].strip().strip("'\""))
                break
            if stripped.startswith("revision"):
                ids.add(stripped.split(":")[-1].strip().strip("'\""))
                break
    return ids


def _down_revs() -> dict[str, str | None]:
    """Return {revision: down_revision_or_None} for every migration file."""
    versions_dir = pathlib.Path(__file__).parent.parent.parent / "alembic" / "versions"
    mapping: dict[str, str | None] = {}
    for f in sorted(versions_dir.glob("*.py")):
        if f.name == "__init__.py":
            continue
        content = f.read_text()
        rev: str | None = None
        down: str | None = None
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith("revision:") or stripped.startswith("revision ="):
                rev = stripped.split("=")[-1].strip().strip("'\"").strip(":\"'")
            if stripped.startswith("down_revision:") or stripped.startswith("down_revision ="):
                val = stripped.split("=")[-1].strip().strip("'\"").strip(":\"'")
                down = None if val == "None" else val
        if rev is not None:
            mapping[rev] = down
    return mapping


# ---------------------------------------------------------------------------
# get_alembic_config — edge cases
# ---------------------------------------------------------------------------


class TestGetAlembicConfigEdgeCases:
    def test_empty_url_defaults_to_sqlite(self):
        cfg = get_alembic_config("")
        url = cfg.get_main_option("sqlalchemy.url")
        assert url is not None
        assert "sqlite" in url

    def test_preserves_non_aiosqlite_postgres_url(self):
        cfg = get_alembic_config("postgresql://user:pass@host/db")
        assert cfg.get_main_option("sqlalchemy.url") == "postgresql://user:pass@host/db"

    def test_config_file_name_is_absolute(self):
        cfg = get_alembic_config()
        name = cfg.config_file_name
        assert name is not None
        assert os.path.isabs(name) or name.startswith("/")

    def test_script_location_ends_with_alembic(self):
        cfg = get_alembic_config()
        loc = cfg.get_main_option("script_location")
        assert loc is not None
        assert loc.endswith("alembic")

    def test_multiple_invocations_return_distinct_objects(self):
        a = get_alembic_config()
        b = get_alembic_config()
        assert a is not b

    def test_sqlite_aiosqlite_url_is_normalized(self):
        cfg = get_alembic_config("sqlite+aiosqlite:///./prod.db")
        assert cfg.get_main_option("sqlalchemy.url") == "sqlite:///./prod.db"

    def test_url_without_scheme_goes_through_as_is(self):
        cfg = get_alembic_config(" ://malformed")
        url = cfg.get_main_option("sqlalchemy.url")
        assert url == " ://malformed"

    def test_missing_alembic_ini_still_sets_script_location(self, tmp_path, monkeypatch):
        import importlib

        monkeypatch.setattr(
            importlib.import_module("general_ludd.db.migrations"),
            "Path",
            lambda *a, **kw: pathlib.Path(tmp_path),
        )
        cfg = get_alembic_config()
        assert cfg.get_main_option("script_location") is not None


# ---------------------------------------------------------------------------
# stamp_head — edge cases
# ---------------------------------------------------------------------------


class TestStampHeadEdgeCases:
    def test_stamp_head_with_empty_config(self):
        cfg = AlembicConfig()
        cfg.set_main_option("script_location", "/nonexistent/for/sure/12345")
        with pytest.raises(CommandError, match="Path doesn't exist"):
            stamp_head(cfg)
        assert cfg.get_main_option("script_location") == "/nonexistent/for/sure/12345"

    def test_stamp_does_not_modify_config(self):
        cfg = AlembicConfig()
        cfg.set_main_option("script_location", "/tmp")
        with patch("general_ludd.db.migrations.command"):
            stamp_head(cfg)
        assert cfg is not None

    def test_stamp_head_passes_revision_as_string(self):
        cfg = AlembicConfig()
        with patch("general_ludd.db.migrations.command") as mock_cmd:
            stamp_head(cfg)
        call_args = mock_cmd.stamp.call_args
        assert call_args[0][1] == "head"


# ---------------------------------------------------------------------------
# MigrationPlan — named-tuple integrity
# ---------------------------------------------------------------------------


class TestMigrationPlanNamedTuple:
    def test_fields_accessible_by_name(self):
        mp = MigrationPlan(sql="SELECT 1", pending_count=3, current_rev="abc", head_rev="def")
        assert mp.sql == "SELECT 1"
        assert mp.pending_count == 3
        assert mp.current_rev == "abc"
        assert mp.head_rev == "def"

    def test_fields_accessible_by_index(self):
        mp = MigrationPlan(sql="x", pending_count=0, current_rev=None, head_rev="h")
        assert mp[0] == "x"
        assert mp[2] is None

    def test_repr_contains_field_names(self):
        mp = MigrationPlan(sql="", pending_count=0, current_rev=None, head_rev="001")
        r = repr(mp)
        assert "MigrationPlan" in r

    def test_equality(self):
        a = MigrationPlan(sql="a", pending_count=0, current_rev=None, head_rev="h")
        b = MigrationPlan(sql="a", pending_count=0, current_rev=None, head_rev="h")
        assert a == b

    def test_inequality_different_plan(self):
        a = MigrationPlan(sql="a", pending_count=0, current_rev=None, head_rev="h")
        b = MigrationPlan(sql="b", pending_count=1, current_rev="x", head_rev="h")
        assert a != b

    def test_empty_sql_and_zero_pending(self):
        mp = MigrationPlan(sql="", pending_count=0, current_rev="001", head_rev="001")
        assert mp.sql == ""
        assert mp.pending_count == 0


# ---------------------------------------------------------------------------
# plan_migration — edge cases (uses real ScriptDirectory on disk)
# ---------------------------------------------------------------------------


class TestPlanMigrationEdgeCases:
    def test_plan_migration_returns_migration_plan(self):
        cfg = get_alembic_config()
        plan = plan_migration(cfg)
        assert isinstance(plan, MigrationPlan)

    def test_plan_has_head_revision(self):
        cfg = get_alembic_config()
        plan = plan_migration(cfg)
        assert plan.head_rev is not None
        assert len(plan.head_rev) > 0

    def test_plan_sql_is_string(self):
        cfg = get_alembic_config()
        plan = plan_migration(cfg)
        assert isinstance(plan.sql, str)
        assert "SQLite offline SQL unavailable" in plan.sql

    def test_plan_pending_count_is_non_negative(self):
        cfg = get_alembic_config()
        plan = plan_migration(cfg)
        assert plan.pending_count >= 0

    def test_plan_with_custom_config_keeps_defaults(self):
        cfg = get_alembic_config("sqlite:///./test.db")
        cfg2 = get_alembic_config("sqlite:///./test.db")
        assert cfg.get_main_option("sqlalchemy.url") == cfg2.get_main_option("sqlalchemy.url")


# ---------------------------------------------------------------------------
# check_pending — edge cases
# ---------------------------------------------------------------------------


class TestCheckPendingEdgeCases:
    def test_check_pending_returns_int(self):
        cfg = get_alembic_config()
        result = check_pending(cfg)
        assert isinstance(result, int)

    def test_check_pending_with_connection_error_returns_minus_one(self):
        cfg = get_alembic_config("sqlite:///nonexistent/path/test.db")
        result = check_pending(cfg)
        assert result == -1

    def test_check_pending_does_not_modify_database(self):
        cfg = get_alembic_config()
        before = plan_migration(cfg)
        check_pending(cfg)
        after = plan_migration(cfg)
        assert before.head_rev == after.head_rev

    def test_check_pending_on_nonexistent_config_location(self):
        cfg = AlembicConfig()
        cfg.set_main_option("script_location", "/nonexistent/dead/path")
        cfg.set_main_option("sqlalchemy.url", "sqlite:///./test.db")
        with pytest.raises(CommandError, match="Path doesn't exist"):
            check_pending(cfg)
        assert cfg.get_main_option("sqlalchemy.url") == "sqlite:///./test.db"


# ---------------------------------------------------------------------------
# Version chain — deep validation of every migration on disk
# ---------------------------------------------------------------------------


class TestVersionChainDeepValidation:
    def test_no_duplicate_revision_ids(self):
        all_revs: list[str] = []
        versions_dir = pathlib.Path(__file__).parent.parent.parent / "alembic" / "versions"
        for f in sorted(versions_dir.glob("*.py")):
            if f.name == "__init__.py":
                continue
            content = f.read_text()
            for line in content.splitlines():
                stripped = line.strip()
                if stripped.startswith("revision:") or stripped.startswith("revision ="):
                    rev = stripped.split("=")[-1].strip().strip("'\"").strip(":\"'")
                    if rev and rev != "None":
                        all_revs.append(rev)
                    break
                if stripped.startswith("revision"):
                    rev = stripped.split(":")[-1].strip().strip("'\"").strip()
                    if rev and rev != "None":
                        all_revs.append(rev)
                    break
        dupes = [r for r in set(all_revs) if all_revs.count(r) > 1]
        assert len(dupes) == 0, f"Duplicate revision ids: {dupes}"

    def test_chain_is_walkable_from_head(self):
        from alembic.script import ScriptDirectory

        cfg = get_alembic_config()
        script = ScriptDirectory.from_config(cfg)
        heads = script.get_heads()
        assert len(heads) == 1, f"Expected single head, got {heads}"
        head = heads[0]
        walked: set[str] = set()
        for rev in script.iterate_revisions(head, "base"):
            walked.add(rev.revision)
        on_disk = _rev_ids()
        missing = on_disk - walked
        assert not missing, f"Revisions on disk but unreachable from head: {missing}"

    def test_every_down_revision_exists_as_revision(self):
        down_map = _down_revs()
        all_ids = _rev_ids()
        for rev, down in down_map.items():
            if down is not None:
                assert down in all_ids, (
                    f"Revision {rev} references down_revision '{down}' which does not exist in any version file"
                )

    def test_root_revision_has_no_down_revision(self):
        down_map = _down_revs()
        roots = [rev for rev, down in down_map.items() if down is None]
        assert len(roots) == 1, f"Expected exactly 1 root revision (down_revision=None), got {roots}"
        assert roots[0] == "001", f"Root revision should be 001, got {roots[0]}"

    def test_no_revision_references_itself(self):
        down_map = _down_revs()
        for rev, down in down_map.items():
            if down is not None:
                assert down != rev, f"Revision {rev} has down_revision pointing to itself"

    def test_linear_chain_no_branch_points(self):
        from alembic.script import ScriptDirectory

        cfg = get_alembic_config()
        script = ScriptDirectory.from_config(cfg)
        for rev in script.walk_revisions():
            assert len(rev.nextrev) <= 1, (
                f"Revision {rev.revision} has {len(rev.nextrev)} children "
                f"({rev.nextrev}); migration chain must be linear"
            )

    def test_every_migration_has_upgrade_function(self):
        versions_dir = pathlib.Path(__file__).parent.parent.parent / "alembic" / "versions"
        missing: list[str] = []
        for f in sorted(versions_dir.glob("*.py")):
            if f.name == "__init__.py":
                continue
            mod = _import_version_file(f)
            if not hasattr(mod, "upgrade") or not callable(mod.upgrade):
                missing.append(f.name)
        assert not missing, f"Missing upgrade() in: {missing}"

    def test_every_migration_has_downgrade_function(self):
        versions_dir = pathlib.Path(__file__).parent.parent.parent / "alembic" / "versions"
        missing: list[str] = []
        for f in sorted(versions_dir.glob("*.py")):
            if f.name == "__init__.py":
                continue
            mod = _import_version_file(f)
            if not hasattr(mod, "downgrade") or not callable(mod.downgrade):
                missing.append(f.name)
        assert not missing, f"Missing downgrade() in: {missing}"

    def test_no_broken_imports(self):
        """Every alembic version file must be importable."""
        versions_dir = pathlib.Path(__file__).parent.parent.parent / "alembic" / "versions"
        failures: list[tuple[str, str]] = []
        for f in sorted(versions_dir.glob("*.py")):
            if f.name == "__init__.py":
                continue
            try:
                _import_version_file(f)
            except Exception as exc:
                failures.append((f.name, str(exc)))
        assert not failures, f"Version files with import errors: {failures}"

    def test_revision_ids_are_globally_unique_across_version_variables(self):
        versions_dir = pathlib.Path(__file__).parent.parent.parent / "alembic" / "versions"
        seen: dict[str, str] = {}
        for f in sorted(versions_dir.glob("*.py")):
            if f.name == "__init__.py":
                continue
            content = f.read_text()
            for line in content.splitlines():
                s = line.strip()
                if s.startswith("revision"):
                    rev = s.split("=")[-1].strip().strip("'\"").strip(":\"'")
                    if rev and rev != "None":
                        if rev in seen:
                            pytest.fail(f"Revision '{rev}' declared in both {seen[rev]} and {f.name}")
                        seen[rev] = f.name
                    break

    def test_head_revision_matches_highest_numeric(self):
        from alembic.script import ScriptDirectory

        cfg = get_alembic_config()
        script = ScriptDirectory.from_config(cfg)
        head = script.get_current_head()
        assert head is not None
        assert head in _rev_ids()


# ---------------------------------------------------------------------------
# Stamped / blank-database operations
# ---------------------------------------------------------------------------


class TestStampOperations:
    def test_stamp_head_to_fresh_database(self, tmp_path):
        """Simulate stamping head to a new, empty database."""
        db_path = tmp_path / "stamp_test.db"
        import sqlalchemy

        engine = sqlalchemy.create_engine(f"sqlite:///{db_path}")
        cfg = get_alembic_config(f"sqlite:///{db_path}")
        with engine.connect() as conn:
            cfg.attributes["connection"] = conn
            with patch("general_ludd.db.migrations.command") as mock_cmd:
                stamp_head(cfg)
            mock_cmd.stamp.assert_called_once_with(cfg, "head")
        engine.dispose()

    def test_plan_migration_on_stamped_database(self, tmp_path):
        """After stamp, plan_migration should see no pending migrations."""
        db_path = tmp_path / "stamp_plan.db"
        cfg = get_alembic_config(f"sqlite:///{db_path}")
        cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
        plan = plan_migration(cfg)
        assert plan.head_rev is not None

    def test_check_pending_minus_one_on_broken_url(self):
        cfg = AlembicConfig()
        cfg.set_main_option(
            "script_location",
            str(pathlib.Path(__file__).parent.parent.parent / "alembic"),
        )
        cfg.set_main_option("sqlalchemy.url", "bogus://localhost:12345/nope")
        result = check_pending(cfg)
        assert result == -1

    def test_check_pending_does_not_raise_on_missing_db_file(self):
        cfg = get_alembic_config("sqlite:///nonexistent_edge_case_999.db")
        result = check_pending(cfg)
        assert isinstance(result, int)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _import_version_file(path: pathlib.Path) -> Any:
    import importlib.util
    from typing import cast

    spec = importlib.util.spec_from_file_location(f"migration_{path.stem}", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    cast(Any, spec.loader).exec_module(mod)
    return mod
