"""Structural tests for config/project.py — find_project_root."""

from __future__ import annotations

from pathlib import Path

from general_ludd.config.project import find_project_root


class TestFindProjectRoot:
    def test_cwd_contains_gludd_dir(self):
        root = find_project_root()
        assert root is not None
        assert (root / ".gludd").is_dir()

    def test_start_at_none_same_as_cwd(self):
        assert find_project_root(None) == find_project_root()

    def test_returns_none_for_path_without_gludd(self):
        root = find_project_root(Path("/"))
        assert root is None

    def test_returns_same_root_from_subdir(self):
        cwd_root = find_project_root()
        sub = cwd_root / "src"
        if sub.is_dir():
            assert find_project_root(sub) == cwd_root

    def test_start_at_gludd_dir_returns_parent(self):
        cwd_root = find_project_root()
        gludd = cwd_root / ".gludd"
        assert find_project_root(gludd) == cwd_root
