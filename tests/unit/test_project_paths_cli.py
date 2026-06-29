"""Unit tests for ``gludd project paths`` diagnostic CLI.

Pins the human-readable precedence table, JSON output, per-tier
role/module counts, and missing-dir skip behaviour. The resolver itself
is covered by ``tests/unit/test_collection_paths.py``; these tests assert
the CLI presentation layer on top of it.
"""

from __future__ import annotations

import io
import json
from contextlib import redirect_stdout
from pathlib import Path

import pytest

from general_ludd.cli_project_paths import (
    _cmd_project_paths,
    render_project_paths,
)


def _materialize_collection_tree(
    base: Path, namespace: str, name: str
) -> Path:
    col_root = base / "ansible_collections" / namespace / name
    (col_root / "plugins" / "modules").mkdir(parents=True, exist_ok=True)
    (col_root / "roles").mkdir(parents=True, exist_ok=True)
    return col_root


def _materialize_role(
    base: Path, namespace: str, name: str, role: str
) -> Path:
    col_root = _materialize_collection_tree(base, namespace, name)
    role_dir = col_root / "roles" / role
    (role_dir / "tasks").mkdir(parents=True, exist_ok=True)
    (role_dir / "tasks" / "main.yml").write_text("---\n# stub\n")
    return role_dir


def _materialize_module(
    base: Path, namespace: str, name: str, module: str
) -> Path:
    col_root = _materialize_collection_tree(base, namespace, name)
    p = col_root / "plugins" / "modules" / f"{module}.py"
    p.write_text("# stub module\n")
    return p


@pytest.fixture
def bundled_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    bundled = tmp_path / "bundled" / "collections"
    bundled.mkdir(parents=True)
    _materialize_collection_tree(bundled, "general_ludd", "agent")
    monkeypatch.setattr(
        "general_ludd.ansible.paths._bundled_collections_root", lambda: bundled
    )
    return bundled


@pytest.fixture
def user_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    xdg = tmp_path / "xdg-home"
    (xdg / "gludd" / "collections").mkdir(parents=True)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    return xdg / "gludd" / "collections"


@pytest.fixture
def project_root(tmp_path: Path) -> Path:
    proj = tmp_path / "proj"
    (proj / ".gludd" / "collections").mkdir(parents=True)
    return proj


class TestProjectPathsCLI:
    def test_project_paths_prints_precedence_table(
        self, bundled_root, user_root, project_root
    ):
        out = render_project_paths(project_root, as_json=False)
        assert "Collection search path" in out
        assert "PROJECT" in out
        assert "USER" in out
        assert "BUNDLED" in out
        # Project tier appears before user, user before bundled.
        proj_idx = out.find("PROJECT")
        user_idx = out.find("USER")
        bundled_idx = out.find("BUNDLED")
        assert 0 <= proj_idx < user_idx < bundled_idx
        # Paths appear inline.
        assert str(project_root / ".gludd" / "collections") in out
        assert str(bundled_root) in out

    def test_project_paths_reports_role_counts_per_tier(
        self, bundled_root, user_root, project_root
    ):
        # Project: 2 roles, 1 module.
        _materialize_role(project_root / ".gludd" / "collections", "ns", "p", "r1")
        _materialize_role(project_root / ".gludd" / "collections", "ns", "p", "r2")
        _materialize_module(project_root / ".gludd" / "collections", "ns", "p", "m1")
        # User: 1 role, 0 modules.
        _materialize_role(user_root, "ns", "u", "ur1")
        # Bundled: 3 roles, 2 modules.
        _materialize_role(bundled_root, "general_ludd", "agent", "b1")
        _materialize_role(bundled_root, "general_ludd", "agent", "b2")
        _materialize_role(bundled_root, "general_ludd", "agent", "b3")
        _materialize_module(bundled_root, "general_ludd", "agent", "bm1")
        _materialize_module(bundled_root, "general_ludd", "agent", "bm2")

        out = render_project_paths(project_root, as_json=False)
        assert "2 roles, 1 module" in out, out
        assert "1 role, 0 modules" in out, out
        assert "3 roles, 2 modules" in out, out

    def test_project_paths_json_output(
        self, bundled_root, user_root, project_root
    ):
        _materialize_role(project_root / ".gludd" / "collections", "ns", "p", "r1")
        _materialize_module(bundled_root, "general_ludd", "agent", "bm1")

        out = render_project_paths(project_root, as_json=True)
        data = json.loads(out)
        assert isinstance(data, list)
        assert len(data) == 3
        assert [d["source"] for d in data] == ["project", "user", "bundled"]
        assert [d["precedence"] for d in data] == [0, 1, 2]
        project_rec = next(d for d in data if d["source"] == "project")
        assert project_rec["exists"] is True
        assert project_rec["roles"] == 1
        assert project_rec["modules"] == 0
        assert project_rec["path"] == str(project_root / ".gludd" / "collections")
        bundled_rec = next(d for d in data if d["source"] == "bundled")
        assert bundled_rec["modules"] == 1

    def test_project_paths_skips_missing_dirs(
        self, bundled_root, monkeypatch, tmp_path
    ):
        # No project passed; user XDG pointed at a nonexistent path.
        monkeypatch.setenv("XDG_CONFIG_HOME", "/nonexistent/xdg-xyz-12345")
        out = render_project_paths(None, as_json=False)
        # Project tier absent, user tier absent, bundled tier present.
        assert "PROJECT" not in out, out
        assert "USER" not in out, out
        assert "BUNDLED" in out
        assert "(exists" in out  # bundled tier exists.

        # JSON shape: only the bundled entry.
        jout = render_project_paths(None, as_json=True)
        jdata = json.loads(jout)
        assert [d["source"] for d in jdata] == ["bundled"]

    def test_project_paths_cmd_invocation(
        self, bundled_root, user_root, project_root
    ):
        """Calling via the argparse handler prints to stdout."""
        import argparse as _argparse

        args = _argparse.Namespace(
            project_dir=str(project_root), json=False
        )
        buf = io.StringIO()
        with redirect_stdout(buf):
            _cmd_project_paths(args)
        out = buf.getvalue()
        assert "Collection search path" in out
        assert "PROJECT" in out

    def test_project_paths_cmd_json_flag(
        self, bundled_root, user_root, project_root
    ):
        import argparse as _argparse

        args = _argparse.Namespace(
            project_dir=str(project_root), json=True
        )
        buf = io.StringIO()
        with redirect_stdout(buf):
            _cmd_project_paths(args)
        data = json.loads(buf.getvalue())
        assert isinstance(data, list)
        assert len(data) == 3
