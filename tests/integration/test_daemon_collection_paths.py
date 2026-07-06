"""Integration tests for daemon ↔ ansible-collection-path resolver wiring."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, cast
from unittest.mock import MagicMock, patch

import pytest

from general_ludd.ansible import paths as paths_module
from general_ludd.ansible.paths import (
    CollectionsPathEntry,
    resolve_collections_paths,
    to_ansible_env,
)
from general_ludd.ansible.runner import AnsibleRunnerAdapter


def _make_db_config(tmp_path: Path) -> str:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    db_path = tmp_path / "test.db"
    (config_dir / "general-ludd.yml").write_text(
        f"database:\n  url: 'sqlite+aiosqlite:///{db_path}'\n"
    )
    return str(config_dir)


@pytest.fixture(autouse=True)
def _isolated_resolver_paths(monkeypatch, tmp_path):
    bundled = tmp_path / "bundled-collections"
    bundled.mkdir()
    user_dir = tmp_path / "absent-user-collections"
    monkeypatch.setattr(
        paths_module, "_bundled_collections_root", lambda: bundled, raising=True
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(user_dir))
    monkeypatch.delenv("ANSIBLE_COLLECTIONS_PATH", raising=False)
    monkeypatch.delenv("ANSIBLE_ROLES_PATH", raising=False)
    yield bundled


class TestResolverPrimitives:
    def test_no_project_returns_bundled_only(self, _isolated_resolver_paths):
        entries = resolve_collections_paths(None)
        assert [e.source for e in entries] == ["bundled"]

    def test_project_with_collections_dir_listed_first(
        self, tmp_path, _isolated_resolver_paths
    ):
        proj = tmp_path / "proj"
        (proj / ".gludd" / "collections").mkdir(parents=True)
        entries = resolve_collections_paths(str(proj))
        assert entries[0].source == "project"
        assert entries[-1].source == "bundled"

    def test_to_ansible_env_joins_paths(self):
        entries = [
            CollectionsPathEntry("project", Path("/a"), 0),
            CollectionsPathEntry("bundled", Path("/b"), 1),
        ]
        env = to_ansible_env(entries)
        assert env["ANSIBLE_COLLECTIONS_PATH"] == "/a:/b"


class TestDaemonStartupWiring:
    def test_daemon_startup_sets_ansible_collections_path(
        self, tmp_path, _isolated_resolver_paths
    ):
        config_dir = _make_db_config(tmp_path)
        with patch(
            "general_ludd.ansible.runner.AnsibleRunnerAdapter",
            return_value=MagicMock(),
        ):
            from fastapi.testclient import TestClient

            from general_ludd.daemon import create_daemon_app

            app = create_daemon_app(tick_interval=0.01, config_dir=config_dir)
            with TestClient(app):
                env = getattr(app.state, "_ansible_env", None)
                assert env is not None
                assert str(_isolated_resolver_paths) in env.get(
                    "ANSIBLE_COLLECTIONS_PATH", ""
                )

    def test_daemon_startup_includes_project_collections_when_present(
        self, tmp_path, monkeypatch, _isolated_resolver_paths
    ):
        project_root = tmp_path / "myproj"
        proj_collections = project_root / ".gludd" / "collections"
        proj_collections.mkdir(parents=True)
        monkeypatch.setenv("GLUDD_PROJECT_DIR", str(project_root / ".gludd"))

        config_dir = _make_db_config(tmp_path)
        with patch(
            "general_ludd.ansible.runner.AnsibleRunnerAdapter",
            return_value=MagicMock(),
        ):
            from fastapi.testclient import TestClient

            from general_ludd.daemon import create_daemon_app

            app = create_daemon_app(tick_interval=0.01, config_dir=config_dir)
            with TestClient(app):
                cp = app.state._ansible_env["ANSIBLE_COLLECTIONS_PATH"]
                assert cp.split(os.pathsep)[0] == str(proj_collections)

    def test_no_project_falls_back_to_bundled_only(
        self, tmp_path, monkeypatch, _isolated_resolver_paths
    ):
        monkeypatch.delenv("GLUDD_PROJECT_DIR", raising=False)
        monkeypatch.chdir(tmp_path)
        config_dir = _make_db_config(tmp_path)
        with patch(
            "general_ludd.ansible.runner.AnsibleRunnerAdapter",
            return_value=MagicMock(),
        ):
            from fastapi.testclient import TestClient

            from general_ludd.daemon import create_daemon_app

            app = create_daemon_app(tick_interval=0.01, config_dir=config_dir)
            with TestClient(app):
                entries = app.state._collections_paths
                assert [e.source for e in entries] == ["bundled"]


class TestProjectSwitchRebuildsEnv:
    @pytest.mark.asyncio
    async def test_project_switch_rebuilds_env(
        self, tmp_path, monkeypatch, _isolated_resolver_paths
    ):
        proj_a = tmp_path / "projA"
        proj_b = tmp_path / "projB"
        (proj_a / "repo" / ".gludd" / "collections").mkdir(parents=True)
        (proj_b / "repo" / ".gludd" / "collections").mkdir(parents=True)

        config_dir = _make_db_config(tmp_path)
        monkeypatch.delenv("GLUDD_PROJECT_DIR", raising=False)
        monkeypatch.chdir(tmp_path)

        with patch(
            "general_ludd.ansible.runner.AnsibleRunnerAdapter",
            return_value=MagicMock(),
        ):
            import general_ludd.daemon as daemon_mod
            from general_ludd.daemon import create_daemon_app

            app = create_daemon_app(tick_interval=300.0, config_dir=config_dir)
            async with daemon_mod._lifespan(app):
                assert not getattr(app.state, "_degraded", None)
                loop = app.state.event_loop
                assert loop is not None

                from general_ludd.projects.workspace import ProjectWorkspace

                ws_a = ProjectWorkspace(
                    project_id="proj-a", workspace_path=str(proj_a)
                )
                ws_b = ProjectWorkspace(
                    project_id="proj-b", workspace_path=str(proj_b)
                )
                loop._project_workspace = {"proj-a": ws_a, "proj-b": ws_b}

                loop._rebuild_ansible_env_for_project("proj-a")
                cp_a = app.state._ansible_env["ANSIBLE_COLLECTIONS_PATH"]
                assert str(proj_a / "repo" / ".gludd" / "collections") in cp_a
                assert str(proj_b / "repo" / ".gludd" / "collections") not in cp_a

                loop._rebuild_ansible_env_for_project("proj-b")
                cp_b = app.state._ansible_env["ANSIBLE_COLLECTIONS_PATH"]
                assert str(proj_b / "repo" / ".gludd" / "collections") in cp_b
                assert str(proj_a / "repo" / ".gludd" / "collections") not in cp_b


class TestAnsibleRunnerUsesResolvedEnv:
    def test_ansible_runner_uses_resolved_env(self, tmp_path):
        """Adapter resolves collections env from project_root at construction."""
        proj_colls = tmp_path / "proj" / ".gludd" / "collections"
        proj_colls.mkdir(parents=True)

        adapter = AnsibleRunnerAdapter(project_root=str(proj_colls.parent.parent))

        colls_env = adapter._collections_env
        assert str(proj_colls) in colls_env["ANSIBLE_COLLECTIONS_PATH"]
        assert colls_env["ANSIBLE_COLLECTIONS_PATH"].split(os.pathsep)[0] == str(
            proj_colls
        )

        captured: dict[str, object] = {}

        def _fake_run(*args, **kwargs):
            captured["extra_env"] = kwargs.get("extra_env")
            result = MagicMock()
            result.model_dump.return_value = {"status": "ok"}
            return result

        cast(Any, adapter._core_runner).run_playbook = _fake_run
        adapter.run_playbook(playbook_name="noop.yml")

        extra_env = captured.get("extra_env") or {}
        assert str(proj_colls) in extra_env.get("ANSIBLE_COLLECTIONS_PATH", "")
