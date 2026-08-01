"""Unit tests for the project/user/bundled ansible collections path resolver."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from general_ludd.ansible.paths import (
    find_resource,
    resolve_collections_paths,
    to_ansible_cfg,
    to_ansible_env,
)


def _materialize_collection_tree(base: Path, namespace: str, name: str) -> Path:
    """Create the standard ansible_collections/<ns>/<name> layout under *base*."""
    col_root = base / "ansible_collections" / namespace / name
    (col_root / "plugins" / "modules").mkdir(parents=True, exist_ok=True)
    (col_root / "roles").mkdir(parents=True, exist_ok=True)
    return col_root


def _materialize_module(
    base: Path, namespace: str, name: str, module: str
) -> Path:
    col_root = _materialize_collection_tree(base, namespace, name)
    p = col_root / "plugins" / "modules" / f"{module}.py"
    p.write_text("# stub module\n", encoding="utf-8")
    return p


def _materialize_role(
    base: Path, namespace: str, name: str, role: str
) -> Path:
    col_root = _materialize_collection_tree(base, namespace, name)
    role_dir = col_root / "roles" / role
    role_dir.mkdir(parents=True, exist_ok=True)
    return role_dir


@pytest.fixture
def bundled_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Provide a tmp-backed bundled collections root, patched into paths module."""
    bundled = tmp_path / "bundled" / "collections"
    bundled.mkdir(parents=True)
    _materialize_collection_tree(bundled, "general_ludd", "agent")
    monkeypatch.setattr(
        "general_ludd.ansible.paths._bundled_collections_root", lambda: bundled
    )
    return bundled


@pytest.fixture
def user_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Provide a tmp-backed user collections root via XDG_CONFIG_HOME."""
    xdg = tmp_path / "xdg-home"
    (xdg / "gludd" / "collections").mkdir(parents=True)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    return xdg / "gludd" / "collections"


@pytest.fixture
def project_root(tmp_path: Path) -> Path:
    """Provide a tmp-backed project root with .gludd/collections/."""
    proj = tmp_path / "proj"
    (proj / ".gludd" / "collections").mkdir(parents=True)
    return proj


class TestResolveCollectionsPaths:
    def test_resolve_returns_three_paths_when_all_exist(
        self, bundled_root, user_root, project_root
    ):
        entries = resolve_collections_paths(project_root=project_root)
        assert len(entries) == 3
        sources = [e.source for e in entries]
        assert sources == ["project", "user", "bundled"]

    def test_resolve_skips_missing_project_dir(self, bundled_root, user_root, tmp_path):
        # project_root passed but has no .gludd/collections/
        proj = tmp_path / "bare-proj"
        proj.mkdir()
        entries = resolve_collections_paths(project_root=proj)
        assert [e.source for e in entries] == ["user", "bundled"]

    def test_resolve_skips_missing_user_dir(self, bundled_root, project_root, monkeypatch):
        # Force an XDG path that does not exist on disk.
        monkeypatch.setenv("XDG_CONFIG_HOME", "/nonexistent/xdp-path-xyz-12345")
        entries = resolve_collections_paths(project_root=project_root)
        assert [e.source for e in entries] == ["project", "bundled"]

    def test_resolve_bundled_always_present(self, bundled_root, monkeypatch, tmp_path):
        # No project, no user (XDG points to nonexistent dir).
        monkeypatch.setenv("XDG_CONFIG_HOME", "/nonexistent/xdp-path-xyz-67890")
        entries = resolve_collections_paths(project_root=None)
        assert [e.source for e in entries] == ["bundled"]
        assert entries[0].path == bundled_root

    def test_resolve_xdg_override(self, bundled_root, tmp_path, monkeypatch):
        xdg = tmp_path / "custom-xdg"
        (xdg / "gludd" / "collections").mkdir(parents=True)
        monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
        entries = resolve_collections_paths(project_root=None)
        user_entry = next(e for e in entries if e.source == "user")
        assert user_entry.path == xdg / "gludd" / "collections"

    def test_resolve_xdg_fallback_to_home_config(
        self, bundled_root, tmp_path, monkeypatch
    ):
        # Unset XDG; patch Path.home() so we don't depend on real $HOME.
        monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
        fake_home = tmp_path / "fake-home"
        (fake_home / ".config" / "gludd" / "collections").mkdir(parents=True)
        with patch("general_ludd.ansible.paths.Path.home", return_value=fake_home):
            entries = resolve_collections_paths(project_root=None)
        user_entry = next(e for e in entries if e.source == "user")
        assert user_entry.path == fake_home / ".config" / "gludd" / "collections"

    def test_precedence_order_project_first(
        self, bundled_root, user_root, project_root
    ):
        entries = resolve_collections_paths(project_root=project_root)
        assert entries[0].source == "project"
        assert entries[1].source == "user"
        assert entries[2].source == "bundled"
        # precedence values ascending: 0, 1, 2
        assert [e.precedence for e in entries] == [0, 1, 2]


class TestToAnsibleEnv:
    def test_to_ansible_env_collections_path_colon_separated(
        self, bundled_root, user_root, project_root
    ):
        entries = resolve_collections_paths(project_root=project_root)
        env = to_ansible_env(entries)
        assert "ANSIBLE_COLLECTIONS_PATH" in env
        parts = env["ANSIBLE_COLLECTIONS_PATH"].split(os.pathsep)
        assert parts[0] == str(project_root / ".gludd" / "collections")
        assert parts[1] == str(user_root)
        assert parts[2] == str(bundled_root)

    def test_to_ansible_env_prepends_existing_env(
        self, bundled_root, user_root, project_root, monkeypatch
    ):
        monkeypatch.setenv("ANSIBLE_COLLECTIONS_PATH", "/usr/share/ansible/collections")
        monkeypatch.setenv("ANSIBLE_ROLES_PATH", "/usr/share/ansible/roles")
        entries = resolve_collections_paths(project_root=project_root)
        env = to_ansible_env(entries)
        cp = env["ANSIBLE_COLLECTIONS_PATH"]
        # Project path FIRST, then existing /usr/share/... appended.
        assert cp.startswith(str(project_root / ".gludd" / "collections"))
        assert cp.endswith("/usr/share/ansible/collections")
        assert "/usr/share/ansible/collections" not in cp.split(os.pathsep)[0]

    def test_to_ansible_env_includes_roles_path(
        self, bundled_root, user_root, project_root
    ):
        entries = resolve_collections_paths(project_root=project_root)
        env = to_ansible_env(entries)
        assert "ANSIBLE_ROLES_PATH" in env
        # Roles path mirrors collections path so legacy role includes work too.
        assert env["ANSIBLE_ROLES_PATH"] == env["ANSIBLE_COLLECTIONS_PATH"]


class TestToAnsibleCfg:
    def test_to_ansible_cfg_format(
        self, bundled_root, user_root, project_root
    ):
        entries = resolve_collections_paths(project_root=project_root)
        line = to_ansible_cfg(entries)
        assert line.startswith("collections_path = ")
        # Three colon-separated paths in precedence order.
        body = line[len("collections_path = "):]
        parts = body.split(os.pathsep)
        assert len(parts) == 3
        assert parts[0] == str(project_root / ".gludd" / "collections")
        assert parts[1] == str(user_root)
        assert parts[2] == str(bundled_root)


class TestFindResource:
    def test_find_resource_returns_project_override(
        self, bundled_root, user_root, project_root
    ):
        # Same module in both project and bundled → project wins.
        proj_col = project_root / ".gludd" / "collections"
        _materialize_module(proj_col, "general_ludd", "agent", "gludd_facts")
        _materialize_module(bundled_root, "general_ludd", "agent", "gludd_facts")
        entries = resolve_collections_paths(project_root=project_root)
        found = find_resource("general_ludd.agent.gludd_facts", entries)
        assert found is not None
        assert str(found).startswith(str(proj_col))

    def test_find_resource_returns_bundled_when_no_override(
        self, bundled_root, user_root, project_root
    ):
        # Only the bundled copy exists.
        _materialize_module(bundled_root, "general_ludd", "agent", "gludd_ping")
        entries = resolve_collections_paths(project_root=project_root)
        found = find_resource("general_ludd.agent.gludd_ping", entries)
        assert found is not None
        assert str(found).startswith(str(bundled_root))

    def test_find_resource_returns_none_for_unknown_fqcn(
        self, bundled_root, user_root, project_root
    ):
        entries = resolve_collections_paths(project_root=project_root)
        assert find_resource("general_ludd.agent.does_not_exist_xyz", entries) is None

    def test_find_resource_handles_role_fqcn(
        self, bundled_root, user_root, project_root
    ):
        _materialize_role(bundled_root, "general_ludd", "agent", "implement_change")
        entries = resolve_collections_paths(project_root=project_root)
        found = find_resource(
            "general_ludd.agent.implement_change", entries
        )
        assert found is not None
        assert found.is_dir()
        assert found.name == "implement_change"


class TestRunnerAdapterWiring:
    """Verify AnsibleRunnerAdapter merges the collections env into runs."""

    def test_adapter_resolves_collections_env_on_init(self, bundled_root, user_root, tmp_path):
        from general_ludd.ansible.runner import AnsibleRunnerAdapter

        proj = tmp_path / "proj"
        (proj / ".gludd" / "collections").mkdir(parents=True)
        with tempfile.TemporaryDirectory() as runner_tmp:
            adapter = AnsibleRunnerAdapter(
                private_data_dir=runner_tmp, project_root=proj
            )
            cp = adapter._collections_env["ANSIBLE_COLLECTIONS_PATH"]
            assert str(proj / ".gludd" / "collections") in cp.split(os.pathsep)[0]

    def test_set_project_root_refreshes_env(self, bundled_root, user_root, tmp_path):
        from general_ludd.ansible.runner import AnsibleRunnerAdapter

        proj_a = tmp_path / "a"
        proj_b = tmp_path / "b"
        (proj_a / ".gludd" / "collections").mkdir(parents=True)
        (proj_b / ".gludd" / "collections").mkdir(parents=True)
        with tempfile.TemporaryDirectory() as runner_tmp:
            adapter = AnsibleRunnerAdapter(
                private_data_dir=runner_tmp, project_root=proj_a
            )
            assert str(proj_a) in adapter._collections_env["ANSIBLE_COLLECTIONS_PATH"]
            adapter.set_project_root(proj_b)
            assert str(proj_b) in adapter._collections_env["ANSIBLE_COLLECTIONS_PATH"]
            assert str(proj_a) not in adapter._collections_env["ANSIBLE_COLLECTIONS_PATH"]

    def test_run_playbook_merges_collections_env(self, bundled_root, user_root, tmp_path):
        from general_ludd.ansible.runner import AnsibleRunnerAdapter

        proj = tmp_path / "proj"
        proj_col = proj / ".gludd" / "collections"
        proj_col.mkdir(parents=True)
        with patch("general_ludd.ansible.runner.CoreAnsibleRunner") as mock_cls:
            mock_core = MagicMock()
            mock_result = MagicMock()
            mock_result.model_dump.return_value = {"status": "successful", "rc": 0}
            mock_core.run_playbook.return_value = mock_result
            mock_cls.return_value = mock_core
            with tempfile.TemporaryDirectory() as runner_tmp:
                adapter = AnsibleRunnerAdapter(
                    private_data_dir=runner_tmp, project_root=proj
                )
                adapter.run_playbook(playbook_name="noop.yml")
            call_kwargs = mock_core.run_playbook.call_args.kwargs
            extra_env = call_kwargs.get("extra_env") or {}
            assert str(proj_col) in extra_env["ANSIBLE_COLLECTIONS_PATH"].split(os.pathsep)[0]


class TestFindProjectRoot:
    def test_finds_nearest_gludd_parent(self, tmp_path):
        from general_ludd.config.project import find_project_root

        proj = tmp_path / "myproj"
        (proj / ".gludd").mkdir(parents=True)
        deep = proj / "src" / "subdir"
        deep.mkdir(parents=True)
        assert find_project_root(deep) == proj.resolve()

    def test_returns_none_when_no_gludd(self, tmp_path, monkeypatch):
        from general_ludd.config.project import find_project_root

        # Other full-suite tests may legitimately create a `.gludd` marker in
        # pytest's shared temp ancestry. Isolate this unit contract from that
        # concurrent state while preserving all non-marker filesystem checks.
        original_is_dir = Path.is_dir

        def _without_project_markers(path: Path) -> bool:
            if path.name == ".gludd":
                return False
            return original_is_dir(path)

        monkeypatch.setattr(Path, "is_dir", _without_project_markers)
        assert find_project_root(tmp_path) is None
