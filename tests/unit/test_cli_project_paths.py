"""Unit tests for cli_project_paths utility functions."""

from __future__ import annotations

from pathlib import Path

from general_ludd.ansible.paths import CollectionsPathEntry
from general_ludd.cli_project_paths import (
    _count_modules,
    _count_roles,
    _entry_to_record,
    _format_table,
)


def _materialize_collection(base: Path, ns: str, name: str) -> Path:
    col = base / "ansible_collections" / ns / name
    col.mkdir(parents=True, exist_ok=True)
    return col


def _materialize_role(base: Path, ns: str, name: str, role: str) -> Path:
    col = _materialize_collection(base, ns, name)
    r = col / "roles" / role
    (r / "tasks").mkdir(parents=True, exist_ok=True)
    (r / "tasks" / "main.yml").write_text("---\n# stub\n")
    return r


def _materialize_module(base: Path, ns: str, name: str, mod: str) -> Path:
    col = _materialize_collection(base, ns, name)
    m = col / "plugins" / "modules" / f"{mod}.py"
    m.parent.mkdir(parents=True, exist_ok=True)
    m.write_text("# stub")
    return m


class TestCountRoles:
    def test_counts_roles_with_tasks(self, tmp_path: Path):
        _materialize_role(tmp_path, "ns", "coll", "role1")
        _materialize_role(tmp_path, "ns", "coll", "role2")
        assert _count_roles(tmp_path) == 2

    def test_empty_dir_zero(self, tmp_path: Path):
        assert _count_roles(tmp_path) == 0

    def test_no_ansible_collections_dir(self, tmp_path: Path):
        (tmp_path / "foo").mkdir()
        assert _count_roles(tmp_path) == 0


class TestCountModules:
    def test_counts_module_files(self, tmp_path: Path):
        _materialize_module(tmp_path, "ns", "coll", "mod1")
        _materialize_module(tmp_path, "ns", "coll", "mod2")
        assert _count_modules(tmp_path) == 2

    def test_skips_init_py(self, tmp_path: Path):
        col = _materialize_collection(tmp_path, "ns", "coll")
        mods = col / "plugins" / "modules"
        mods.mkdir(parents=True)
        (mods / "real_mod.py").write_text("# stub")
        (mods / "__init__.py").write_text("# init")
        assert _count_modules(tmp_path) == 1

    def test_empty_dir_zero(self, tmp_path: Path):
        assert _count_modules(tmp_path) == 0


class TestEntryToRecord:
    def test_existing_dir(self, tmp_path: Path):
        _materialize_role(tmp_path, "ns", "coll", "role1")
        _materialize_module(tmp_path, "ns", "coll", "mod1")

        entry = CollectionsPathEntry(source="project", path=tmp_path, precedence=0)
        rec = _entry_to_record(entry)
        assert rec["source"] == "project"
        assert rec["exists"] is True
        assert rec["roles"] == 1
        assert rec["modules"] == 1
        assert rec["precedence"] == 0
        assert rec["path"] == str(tmp_path)

    def test_missing_dir(self, tmp_path: Path):
        missing = tmp_path / "nope"
        entry = CollectionsPathEntry(source="user", path=missing, precedence=1)
        rec = _entry_to_record(entry)
        assert rec["exists"] is False
        assert rec["roles"] == 0
        assert rec["modules"] == 0


class TestFormatTable:
    def test_formats_with_all_sources(self, tmp_path: Path):
        proj = tmp_path / "proj"
        proj.mkdir()
        user = tmp_path / "user"
        user.mkdir()
        bundled = tmp_path / "bundled"
        bundled.mkdir()
        _materialize_role(bundled, "ns", "coll", "b1")
        _materialize_module(bundled, "ns", "coll", "bm1")

        records = [
            {"source": "project", "path": str(proj), "precedence": 0, "exists": False, "roles": 0, "modules": 0},
            {"source": "user", "path": str(user), "precedence": 1, "exists": False, "roles": 0, "modules": 0},
            {"source": "bundled", "path": str(bundled), "precedence": 2, "exists": True, "roles": 1, "modules": 1},
        ]
        result = _format_table(records)
        assert "Collection search path" in result
        assert "PROJECT" in result
        assert "USER" in result
        assert "BUNDLED" in result
        assert "1 role, 1 module" in result

    def test_singular_role(self):
        records = [
            {"source": "bundled", "path": "/a", "precedence": 0, "exists": True, "roles": 1, "modules": 3},
        ]
        result = _format_table(records)
        assert "1 role," in result
        assert "3 modules" in result
