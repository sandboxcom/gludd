"""Integration tests for self-improvement project routing.

When gludd is pointed at a gludd repo (a registered Project whose URL is the
gludd repo itself), self-improvement tasks should be performed against THAT
project's workspace — NOT by patching the running daemon's source tree directly.
After the task commits in the project workspace, the running daemon hot-updates.

Architecture references:
  - docs/audit/SELF_IMPROVE_ARCHITECTURE.md
  - docs/audit/PROJECT_HOT_RELOAD_ARCHITECTURE.md
"""

from __future__ import annotations

import importlib
import json
import os
import sys
import textwrap
from pathlib import Path
from types import SimpleNamespace

import pytest

from general_ludd.git_automation.repo import GitAutomation
from general_ludd.projects.manager import ProjectWeight, _detect_self_project
from general_ludd.reload.hot_reloader import HotReloader
from general_ludd.self_improve.harness import SelfImprovementHarness


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text))


def _purge_modules(prefix: str) -> None:
    for name in [n for n in list(sys.modules) if n.startswith(prefix)]:
        sys.modules.pop(name, None)


@pytest.fixture
def temp_pkg(tmp_path: Path):
    """Create an importable package under tmp_path; tear it down after."""
    pkg_name = "gludd_test_reload_pkg"
    pkg_dir = tmp_path / pkg_name
    pkg_dir.mkdir(parents=True, exist_ok=True)
    (pkg_dir / "__init__.py").write_text("")
    sys.path.insert(0, str(tmp_path))
    importlib.invalidate_caches()
    try:
        yield tmp_path, pkg_name, pkg_dir
    finally:
        sys.path.remove(str(tmp_path))
        _purge_modules(pkg_name)


def _init_repo(repo_dir: Path) -> GitAutomation:
    git = GitAutomation(repo_path=str(repo_dir))
    git.init_repo()
    return git


# ── STEP 1 — Detection ───────────────────────────────────────────────────────


class TestDetectSelfProject:
    def test_detect_self_project_matches_configured_url(self):
        """A project whose repo_url matches the gludd self repo URL is detected."""
        project = ProjectWeight(
            project_id="proj-self",
            name="gludd-self",
            weight=1.0,
            repo_url="git@github.com:sandboxcom/gludd.git",
        )
        assert _detect_self_project(
            project, self_repo_url="https://github.com/sandboxcom/gludd.git"
        ) is True

    def test_detect_self_project_rejects_foreign_url(self):
        """A project whose repo_url differs from gludd's is NOT self."""
        project = ProjectWeight(
            project_id="proj-ext",
            name="external",
            weight=1.0,
            repo_url="https://github.com/otherorg/otherrepo.git",
        )
        assert _detect_self_project(
            project, self_repo_url="https://github.com/sandboxcom/gludd.git"
        ) is False

    def test_detect_self_project_normalizes_ssh_https_and_scp_variants(self):
        """All common URL spellings of the same repo normalize to the same key."""
        variants = [
            "git@github.com:sandboxcom/gludd.git",
            "ssh://git@github.com/sandboxcom/gludd.git",
            "https://github.com/sandboxcom/gludd.git",
            "https://github.com/sandboxcom/gludd",
            "GIT@GitHub.Com:SandBoxCom/Gludd.git",
        ]
        for v in variants:
            project = ProjectWeight(
                project_id="p", name="n", weight=1.0, repo_url=v
            )
            assert _detect_self_project(
                project, self_repo_url="git@github.com:sandboxcom/gludd.git"
            ) is True, f"variant {v!r} not detected as self"


# ── STEP 2 — Routing ─────────────────────────────────────────────────────────


class TestHarnessProjectRouting:
    def test_self_improve_with_project_id_routes_to_workspace(self, tmp_path: Path):
        """project_id resolves to the ProjectWorkspace.repo_dir, not cwd."""
        base_dir = tmp_path / "workspaces"
        repo_dir = base_dir / "proj-xyz" / "repo"
        repo_dir.mkdir(parents=True, exist_ok=True)
        (repo_dir / "pyproject.toml").write_text("")

        harness = SelfImprovementHarness(
            project_id="proj-xyz", project_base_dir=str(base_dir)
        )
        assert harness.repo_root == str(repo_dir)

    def test_self_improve_without_project_id_uses_cwd(self):
        """Back-compat: no project_id -> repo_root stays os.getcwd()."""
        harness = SelfImprovementHarness()
        assert harness.repo_root == os.getcwd()

    def test_explicit_repo_root_takes_precedence_over_project_id(self):
        """When both repo_root and project_id are given, repo_root wins."""
        harness = SelfImprovementHarness(
            repo_root="/some/explicit/path", project_id="proj-xyz"
        )
        assert harness.repo_root == "/some/explicit/path"


# ── STEP 3 — Commit + hot-reload ─────────────────────────────────────────────


class TestCommitTriggersHotReload:
    def test_commit_in_project_workspace_triggers_hot_reload(self, tmp_path: Path):
        """apply_self_improvement commits in the workspace and invokes the reloader."""
        from unittest.mock import MagicMock

        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()
        git = _init_repo(repo_dir)
        (repo_dir / "mod.py").write_text("X = 1\n")
        git.commit("initial")
        (repo_dir / "mod.py").write_text("X = 2\n")

        reloader = MagicMock()
        reloader.reload_changed_modules.return_value = MagicMock(
            success=True, details={"reloaded_modules": ["mod"]}
        )
        harness = SelfImprovementHarness(repo_root=str(repo_dir))
        result = harness.apply_self_improvement(
            workspace_repo_dir=str(repo_dir),
            message="test change",
            reloader=reloader,
        )
        assert result["commit_sha"]
        assert len(result["commit_sha"]) >= 7
        assert "mod.py" in result["changed_files"]
        assert result["reload_success"] is True
        reloader.reload_changed_modules.assert_called_once()
        call_kwargs = reloader.reload_changed_modules.call_args
        assert call_kwargs.kwargs["repo_dir"] == str(repo_dir)
        assert "mod.py" in call_kwargs.kwargs["changed_paths"]

    def test_apply_self_improvement_emits_event(self, tmp_path: Path):
        """A wired event bus receives SelfUpdateAppliedEvent."""
        from unittest.mock import MagicMock

        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()
        git = _init_repo(repo_dir)
        (repo_dir / "mod.py").write_text("X = 1\n")
        git.commit("init")
        (repo_dir / "mod.py").write_text("X = 2\n")

        reloader = MagicMock()
        reloader.reload_changed_modules.return_value = MagicMock(
            success=True, details={"reloaded_modules": ["mod"]}
        )
        bus = MagicMock()
        harness = SelfImprovementHarness(repo_root=str(repo_dir))
        harness.apply_self_improvement(
            workspace_repo_dir=str(repo_dir),
            message="ev",
            reloader=reloader,
            event_bus=bus,
        )
        bus.publish.assert_called_once()
        event = bus.publish.call_args.args[0]
        from general_ludd.events.types import SelfUpdateAppliedEvent

        assert isinstance(event, SelfUpdateAppliedEvent)
        assert event.payload["commit_sha"]
        assert event.payload["reloaded_modules"] == ["mod"]


class TestReloadChangedModules:
    def test_reload_changed_modules_handles_modified_module(self, temp_pkg):
        """An already-imported module picks up new bytes after reload."""
        tmp_path, pkg_name, pkg_dir = temp_pkg
        mod_path = pkg_dir / "modified_mod.py"
        _write(mod_path, "VALUE = 1\n")
        importlib.invalidate_caches()
        mod = importlib.import_module(f"{pkg_name}.modified_mod")
        assert mod.VALUE == 1

        _write(mod_path, "VALUE = 2\n")
        reloader = HotReloader(config_dir=str(tmp_path))
        result = reloader.reload_changed_modules(
            repo_dir=str(tmp_path),
            changed_paths=[f"{pkg_name}/modified_mod.py"],
            health_check=lambda: True,
        )
        assert result.success is True
        assert "reloaded_modules" in result.details
        assert any(
            m.endswith("modified_mod")
            for m in result.details["reloaded_modules"]
        )
        assert mod.VALUE == 2

    def test_reload_changed_modules_handles_added_file(self, temp_pkg):
        """A new .py file in an importable package becomes importable after reload."""
        tmp_path, pkg_name, pkg_dir = temp_pkg
        new_mod_path = pkg_dir / "added_mod.py"
        _write(new_mod_path, "VALUE = 99\n")

        assert f"{pkg_name}.added_mod" not in sys.modules
        reloader = HotReloader(config_dir=str(tmp_path))
        result = reloader.reload_changed_modules(
            repo_dir=str(tmp_path),
            changed_paths=[f"{pkg_name}/added_mod.py"],
            health_check=lambda: True,
        )
        assert result.success is True
        new_mod = importlib.import_module(f"{pkg_name}.added_mod")
        assert new_mod.VALUE == 99
        assert "added_modules" in result.details

    def test_reload_changed_modules_handles_deleted_module(self, temp_pkg):
        """A deleted file is removed from sys.modules gracefully."""
        tmp_path, pkg_name, pkg_dir = temp_pkg
        del_mod_path = pkg_dir / "delete_me.py"
        _write(del_mod_path, "VALUE = 0\n")
        importlib.invalidate_caches()
        importlib.import_module(f"{pkg_name}.delete_me")
        assert f"{pkg_name}.delete_me" in sys.modules

        del_mod_path.unlink()
        reloader = HotReloader(config_dir=str(tmp_path))
        result = reloader.reload_changed_modules(
            repo_dir=str(tmp_path),
            changed_paths=[f"{pkg_name}/delete_me.py"],
            health_check=lambda: True,
        )
        assert result.success is True
        assert "deleted_modules" in result.details
        assert f"{pkg_name}.delete_me" not in sys.modules

    def test_reload_changed_modules_ignores_non_python_paths(self, temp_pkg):
        """Non-.py paths in changed_paths are skipped without error."""
        tmp_path, _pkg_name, _pkg_dir = temp_pkg
        reloader = HotReloader(config_dir=str(tmp_path))
        result = reloader.reload_changed_modules(
            repo_dir=str(tmp_path),
            changed_paths=["README.md", "config.yml"],
            health_check=lambda: True,
        )
        assert result.success is True


# ── STEP 4 — Endpoint ────────────────────────────────────────────────────────


class TestSelfImproveApplyEndpoint:
    @pytest.mark.asyncio
    async def test_self_improve_apply_endpoint_accepts_project_id(self, monkeypatch):
        """POST /admin/self-improve/apply accepts a project_id field without 4xx."""
        from httpx import ASGITransport, AsyncClient
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        from general_ludd.db.models import Base

        psk = "test-psk-routing"
        monkeypatch.setenv("GLUDD_AUTH_PSK", psk)
        engine = create_async_engine("sqlite+aiosqlite://", echo=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)

        from general_ludd.daemon import create_daemon_app

        app = create_daemon_app(tick_interval=1.0)
        app.state._session_factory = factory

        transport = ASGITransport(app=app)
        client = AsyncClient(transport=transport, base_url="http://test")
        try:
            resp = await client.post(
                "/admin/self-improve/apply",
                headers={"Authorization": f"Bearer {psk}"},
                json={
                    "kind": "config",
                    "project_id": "proj-self-test",
                    "target_paths": ["config/test_routing.yml"],
                    "change_content": "key: value\n",
                    "title": "routing test",
                },
            )
            # Acceptable: 200 (enqueued for approval) — NOT a 4xx schema rejection.
            assert resp.status_code < 400, resp.text
            data = resp.json()
            assert data["tier"] == "config"
        finally:
            await client.aclose()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_released_non_config_plan_keeps_approved_project_identity(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The apply POST cannot substitute another project's worktree."""
        from httpx import ASGITransport, AsyncClient
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        import general_ludd.reload.self_improve as reload_self_improve
        from general_ludd.daemon import create_daemon_app
        from general_ludd.db.models import Base, ProjectModel
        from general_ludd.db.repository import TodoRepository

        approved_root = tmp_path / "approved-project"
        approved_worktree = approved_root / "repo" / "worktrees" / "approved"
        approved_worktree.mkdir(parents=True)
        attacker_root = tmp_path / "attacker-project"
        attacker_worktree = attacker_root / "repo" / "worktrees" / "attacker"
        attacker_worktree.mkdir(parents=True)
        engine = create_async_engine("sqlite+aiosqlite://", echo=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            session.add(
                ProjectModel(
                    project_id="approved-project",
                    name="Approved project",
                    workspace_path=str(approved_root),
                )
            )
            await session.commit()

        validated_paths: list[str] = []

        class Workflow:
            def validate_improvement(self, worktree_path: str) -> SimpleNamespace:
                validated_paths.append(worktree_path)
                return SimpleNamespace(success=True)

            def apply_improvement(
                self,
                _approval_id: str,
                _validation: object,
            ) -> SimpleNamespace:
                return SimpleNamespace(applied=True, reload_needed=True)

            def reload_if_needed(self, _result: object) -> SimpleNamespace:
                return SimpleNamespace(status="reloaded")

        project_lookups: list[str] = []

        def get_project(project_id: str) -> SimpleNamespace | None:
            project_lookups.append(project_id)
            roots = {
                "approved-project": approved_root,
                "attacker-project": attacker_root,
            }
            root = roots.get(project_id)
            return None if root is None else SimpleNamespace(workspace_path=str(root))

        monkeypatch.setattr(reload_self_improve, "SelfImprovementWorkflow", Workflow)
        app = create_daemon_app(tick_interval=1.0)
        app.state._session_factory = factory
        app.state._project_manager = SimpleNamespace(get_project=get_project)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/admin/self-improve/apply",
                json={
                    "kind": "code",
                    "project_id": "approved-project",
                    "worktree_path": str(approved_worktree),
                    "title": "approved title",
                    "description": "approved description",
                },
            )
            assert response.status_code == 200
            approval_id = response.json()["approval_id"]
            async with factory() as session:
                todo = await TodoRepository(session).get_by_id(approval_id)
                assert todo is not None
                assert todo.project_id == "approved-project"
                assert todo.plan_artifact == json.dumps(
                    json.loads(todo.plan_artifact or ""),
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                )

            response = await client.post(
                f"/admin/self-improve/approvals/{approval_id}/approve"
            )
            assert response.status_code == 200
            response = await client.post(
                "/admin/self-improve/apply",
                json={
                    "kind": "code",
                    "approval_id": approval_id,
                    "project_id": "attacker-project",
                    "worktree_path": str(attacker_worktree),
                },
            )

        assert response.status_code == 200
        assert validated_paths == [str(approved_worktree.resolve())]
        assert project_lookups == ["approved-project", "approved-project"]
        await engine.dispose()
